"""Mask-aware classic 3DGS worker with optional adaptive density control.

Executed in the configured Gaussian checkout's Python environment. Upstream imports
are lazy. Fixed mode preserves the L1-only baseline for reproducible comparisons.
"""

import argparse
import inspect
import json
import math
import random
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, required=True)
    parser.add_argument("--synthetic-weight", type=float, required=True)
    parser.add_argument("--training-config", type=Path)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from roboenv_perception.construction.common import read_mask
    from roboenv_perception.construction.contracts import BackgroundConfig
    from roboenv_perception.construction.supervision import (
        projection_matrix,
        region_weights,
        ssim_region_weights,
    )
    from roboenv_perception.construction.training_loss import (
        densification_budget_mask,
        masked_ssim_loss,
    )
    from roboenv_perception.io import read_json, relative_file, write_json

    settings = BackgroundConfig.model_validate(
        read_json(args.training_config)
        if args.training_config
        else {"iterations": args.iterations, "synthetic_weight": args.synthetic_weight}
    )
    if settings.iterations != args.iterations or settings.synthetic_weight != args.synthetic_weight:
        raise ValueError("worker arguments differ from the saved training configuration")
    adaptive = settings.training_mode == "adaptive"
    sys.path.insert(0, str(args.repo.resolve()))
    import torch
    from arguments import OptimizationParams
    from gaussian_renderer import render
    from plyfile import PlyData
    from scene.gaussian_model import GaussianModel
    from utils.graphics_utils import BasicPointCloud

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable in configured 3DGS Python; no training executed")
    random.seed(0)
    np.random.seed(0)
    torch.manual_seed(0)
    data = read_json(args.dataset / "cameras.json")
    views = []
    for frame in data["frames"]:
        width, height = frame["size"]
        k = np.asarray(frame["intrinsics"])
        world = torch.tensor(frame["world_to_camera"], dtype=torch.float32, device="cuda").T
        projection = torch.tensor(projection_matrix(k, frame["size"]), device="cuda").T
        camera = SimpleNamespace(
            image_name=frame["image_name"],
            image_width=width,
            image_height=height,
            FoVx=2 * math.atan(width / (2 * k[0, 0])),
            FoVy=2 * math.atan(height / (2 * k[1, 1])),
            world_view_transform=world,
            full_proj_transform=world @ projection,
            camera_center=world.inverse()[3, :3],
        )
        observed = read_mask(relative_file(args.dataset, frame["observed_mask_ref"]), frame["size"])
        synthetic = read_mask(
            relative_file(args.dataset, frame["synthetic_mask_ref"]), frame["size"]
        )
        if frame["split"] != "train" and synthetic.any():
            raise ValueError("validation images cannot contain synthetic supervision")
        weights = region_weights(observed, synthetic, args.synthetic_weight)
        structural = (
            ssim_region_weights(observed, synthetic, args.synthetic_weight)
            if adaptive and settings.ssim_weight
            else None
        )
        views.append((frame, camera, weights, observed, structural))
    train = [v for v in views if v[0]["split"] == "train" and v[2].sum() > 0]
    if not train:
        raise ValueError("no supervised training pixels")
    vertices = PlyData.read(relative_file(args.dataset, data["points_ref"]))["vertex"]
    xyz = np.stack([vertices[n] for n in ("x", "y", "z")], axis=1)
    rgb = np.stack([vertices[n] for n in ("red", "green", "blue")], axis=1) / 255.0
    if len(xyz) < 4 or not np.isfinite(xyz).all():
        raise ValueError("at least four finite background seeds required")
    if adaptive and len(xyz) > settings.max_gaussians:
        raise ValueError("initial seed count exceeds max_gaussians")
    initial_count = len(xyz)
    cloud = BasicPointCloud(points=xyz, colors=rgb, normals=np.zeros_like(xyz))
    gaussians = GaussianModel(3)
    if adaptive:
        centers = np.asarray([v[0]["camera_to_world"] for v in train])[:, :3, 3]
        extent = max(float(np.linalg.norm(centers - centers.mean(0), axis=1).max()) * 1.1, 1e-3)
    else:
        extent = max(float(np.linalg.norm(xyz - xyz.mean(0), axis=1).max()), 1e-3)
    parameters = inspect.signature(gaussians.create_from_pcd).parameters
    if "cam_infos" in parameters:
        gaussians.create_from_pcd(cloud, [v[1] for v in train], extent)
    else:
        gaussians.create_from_pcd(cloud, extent)
    opt_parser = argparse.ArgumentParser()
    optimization = OptimizationParams(opt_parser)
    opt = optimization.extract(opt_parser.parse_args([]))
    opt.iterations = args.iterations
    opt.position_lr_max_steps = args.iterations
    gaussians.training_setup(opt)
    densify_has_radii = "radii" in inspect.signature(gaussians.densify_and_prune).parameters
    densify_until = min(settings.densify_until_iter, args.iterations)
    pipe = SimpleNamespace(
        debug=False, antialiasing=False, convert_SHs_python=False, compute_cov3D_python=False
    )
    background = torch.zeros(3, device="cuda")
    args.output.mkdir(parents=True, exist_ok=False)

    @lru_cache(maxsize=64)
    def cached_rgb(reference):
        with Image.open(relative_file(args.dataset, reference)) as image:
            rgb = np.array(image.convert("RGB"))
        return torch.from_numpy(rgb).permute(2, 0, 1)

    def target(frame, key):
        rgb = cached_rgb(frame[key])
        if tuple(rgb.shape[1:]) != (frame["size"][1], frame["size"][0]):
            raise ValueError("target image geometry differs from camera")
        return rgb.to(device="cuda", dtype=torch.float32).div_(255)

    checkpoints = []
    save_at = {i for i in settings.checkpoint_iterations if i <= args.iterations}
    save_at.add(args.iterations)

    @torch.no_grad()
    def save_checkpoint(iteration):
        folder = args.output / "checkpoints" / f"iteration_{iteration}"
        folder.mkdir(parents=True)
        gaussians.save_ply(str(folder / "point_cloud.ply"))
        (folder / "holdout").mkdir()
        evaluation = []
        for frame, camera, _, observed, _ in views:
            if frame["split"] != "holdout" or not observed.any():
                continue
            prediction = render(camera, gaussians, pipe, background)["render"]
            truth = target(frame, "real_ref")
            mask = torch.from_numpy(observed).cuda()
            mse = ((prediction - truth).square() * mask).sum() / (3 * mask.sum())
            if not torch.isfinite(mse):
                raise RuntimeError("nonfinite validation error")
            preview_name = f"{len(evaluation):03d}.png"
            pixels = prediction.permute(1, 2, 0).cpu().numpy()
            Image.fromarray((pixels * 255).astype(np.uint8)).save(folder / "holdout" / preview_name)
            evaluation.append(
                {
                    "frame_id": frame["frame_id"],
                    "observed_pixels": int(observed.sum()),
                    "masked_mse": mse.item(),
                    "render_ref": str((folder / "holdout" / preview_name).relative_to(args.output)),
                }
            )
        checkpoint = {
            "iteration": iteration,
            "gaussian_count": int(gaussians.get_xyz.shape[0]),
            "model_ref": str((folder / "point_cloud.ply").relative_to(args.output)),
            "mean_masked_mse": float(np.mean([v["masked_mse"] for v in evaluation]))
            if evaluation
            else None,
            "holdout": evaluation,
        }
        write_json(folder / "evaluation.json", checkpoint)
        checkpoints.append(checkpoint)
        print(
            json.dumps(
                {
                    "checkpoint": iteration,
                    **{k: checkpoint[k] for k in ["gaussian_count", "mean_masked_mse"]},
                }
            ),
            flush=True,
        )

    for iteration in range(1, args.iterations + 1):
        frame, camera, weights, _, structural = random.choice(train)
        gaussians.update_learning_rate(iteration)
        if iteration % 1000 == 0:
            gaussians.oneupSHdegree()
        package = render(camera, gaussians, pipe, background)
        prediction = package["render"]
        weight = torch.from_numpy(weights).cuda()
        truth = target(frame, "image_ref")
        l1 = ((prediction - truth).abs() * weight).sum() / (3 * weight.sum())
        coefficient = settings.ssim_weight if structural is not None and structural.any() else 0
        structural_loss = (
            masked_ssim_loss(prediction, truth, torch.from_numpy(structural).cuda())
            if coefficient
            else prediction.new_zeros(())
        )
        loss = (1 - coefficient) * l1 + coefficient * structural_loss
        if not torch.isfinite(loss):
            raise RuntimeError("nonfinite weighted training loss")
        loss.backward()
        with torch.no_grad():
            if adaptive and iteration < densify_until:
                gradient = package["viewspace_points"].grad
                # Accumulate only views where the Gaussian received supervised gradients.
                supported = (package["radii"] > 0) & torch.isfinite(gradient).all(dim=1)
                supported &= gradient[:, :2].norm(dim=1) > 0
                gaussians.max_radii2D[supported] = torch.maximum(
                    gaussians.max_radii2D[supported], package["radii"][supported]
                )
                gaussians.add_densification_stats(package["viewspace_points"], supported)
        gaussians.optimizer.step()
        gaussians.optimizer.zero_grad(set_to_none=True)
        # Save before opacity resets so checkpoints represent the fitted state.
        if iteration in save_at:
            save_checkpoint(iteration)
        with torch.no_grad():
            if adaptive and iteration < densify_until:
                if (
                    iteration > settings.densify_from_iter
                    and iteration % settings.densification_interval == 0
                ):
                    count = int(gaussians.get_xyz.shape[0])
                    scores = (gaussians.xyz_gradient_accum / gaussians.denom.clamp_min(1)).flatten()
                    selected = densification_budget_mask(
                        scores, settings.densify_grad_threshold, settings.max_gaussians - count
                    )
                    gaussians.xyz_gradient_accum[~selected] = 0
                    size_limit = 20 if iteration > settings.opacity_reset_interval else None
                    prune_args = [settings.densify_grad_threshold, 0.005, extent, size_limit]
                    if densify_has_radii:
                        prune_args.append(package["radii"])
                    gaussians.densify_and_prune(*prune_args)
                    count = int(gaussians.get_xyz.shape[0])
                    if count < 4 or count > settings.max_gaussians:
                        raise RuntimeError(f"adaptive topology count outside limits: {count}")
                if iteration % settings.opacity_reset_interval == 0:
                    gaussians.reset_opacity()
        if iteration == 1 or iteration % 100 == 0:
            print(
                json.dumps(
                    {
                        "iteration": iteration,
                        "weighted_l1": l1.item(),
                        "masked_ssim_loss": structural_loss.item(),
                        "loss": loss.item(),
                        "gaussian_count": int(gaussians.get_xyz.shape[0]),
                    }
                ),
                flush=True,
            )
    evaluated = [c for c in checkpoints if c["mean_masked_mse"] is not None]
    selected = min(evaluated, key=lambda c: c["mean_masked_mse"]) if evaluated else checkpoints[-1]
    shutil.copyfile(args.output / selected["model_ref"], args.output / "point_cloud.ply")
    write_json(
        args.output / "result.json",
        {
            "status": "completed",
            "iterations": args.iterations,
            "selected_iteration": selected["iteration"],
            "selection_rule": "minimum mean observed-background validation MSE"
            if evaluated
            else "final iteration; no validation views",
            "scene_id": data.get("scene_id"),
            "source_kind": data.get("source_kind", "unclassified_source"),
            "holdout": selected["holdout"],
            "checkpoints": checkpoints,
            "supervision": "observed=1; completion=synthetic_weight; unknown/excluded=0; SSIM only within single-provenance full windows",
            "topology": "adaptive clone/split/prune with supervised gradients"
            if adaptive
            else "fixed background seeds",
            "initial_gaussian_count": initial_count,
            "gaussian_count": selected["gaussian_count"],
            "training": settings.model_dump(),
            "extent": extent,
            "extent_source": "training camera radius" if adaptive else "seed radius",
            "effective_densify_until_iter": densify_until if adaptive else None,
            "synthetic_weight": args.synthetic_weight,
            "independent_acceptance": False,
            "physics_status": "not_built",
            "metric_scale": "unknown",
            "limitations": [
                "Validation views select a checkpoint, so are not an independent final test.",
                "Unknown/occluded regions receive no supervision and may contain spillover.",
            ],
        },
    )


if __name__ == "__main__":
    main()
