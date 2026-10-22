"""Opt-in classic 3DGS adapter. Fixed seed topology; no GUI or downloads.

Executed in the configured Gaussian checkout's Python environment. Upstream imports
are intentionally lazy. This baseline optimizes seed Gaussians without densification.
"""

import argparse
import inspect
import json
import math
import random
import sys
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
    args = parser.parse_args(argv)
    # Source package remains available even when the external worker env is separate.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from roboenv_perception.construction.common import read_mask
    from roboenv_perception.construction.supervision import projection_matrix, region_weights
    from roboenv_perception.io import read_json, relative_file, write_json

    sys.path.insert(0, str(args.repo.resolve()))
    import torch
    from arguments import OptimizationParams
    from gaussian_renderer import render
    from plyfile import PlyData
    from scene.gaussian_model import GaussianModel
    from utils.graphics_utils import BasicPointCloud

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable in configured 3DGS Python; no training executed")
    if args.iterations < 1:
        raise ValueError("iterations must be positive")
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
        weights = region_weights(observed, synthetic, args.synthetic_weight)
        views.append((frame, camera, weights, observed))
    train = [v for v in views if v[0]["split"] == "train" and v[2].sum() > 0]
    if not train:
        raise ValueError("no supervised training pixels")
    vertices = PlyData.read(relative_file(args.dataset, data["points_ref"]))["vertex"]
    xyz = np.stack([vertices[n] for n in ("x", "y", "z")], axis=1)
    rgb = np.stack([vertices[n] for n in ("red", "green", "blue")], axis=1) / 255.0
    if len(xyz) < 4 or not np.isfinite(xyz).all():
        raise ValueError("at least four finite background seeds required")
    cloud = BasicPointCloud(points=xyz, colors=rgb, normals=np.zeros_like(xyz))
    gaussians = GaussianModel(3)
    extent = max(float(np.linalg.norm(xyz - xyz.mean(0), axis=1).max()), 1e-3)
    parameters = inspect.signature(gaussians.create_from_pcd).parameters
    if "cam_infos" in parameters:
        gaussians.create_from_pcd(cloud, [v[1] for v in views], extent)
    else:
        gaussians.create_from_pcd(cloud, extent)
    opt_parser = argparse.ArgumentParser()
    optimization = OptimizationParams(opt_parser)
    opt = optimization.extract(opt_parser.parse_args([]))
    opt.iterations = args.iterations
    opt.position_lr_max_steps = args.iterations
    gaussians.training_setup(opt)
    pipe = SimpleNamespace(
        debug=False, antialiasing=False, convert_SHs_python=False, compute_cov3D_python=False
    )
    background = torch.zeros(3, device="cuda")

    def target(frame, key):
        with Image.open(relative_file(args.dataset, frame[key])) as image:
            rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255
        if rgb.shape[:2] != (frame["size"][1], frame["size"][0]):
            raise ValueError("target image geometry differs from camera")
        return torch.from_numpy(rgb).permute(2, 0, 1).cuda()

    for iteration in range(1, args.iterations + 1):
        frame, camera, weights, _ = random.choice(train)
        gaussians.update_learning_rate(iteration)
        if iteration % 1000 == 0:
            gaussians.oneupSHdegree()
        prediction = render(camera, gaussians, pipe, background)["render"]
        weight = torch.from_numpy(weights).cuda()
        loss = ((prediction - target(frame, "image_ref")).abs() * weight).sum() / (3 * weight.sum())
        if not torch.isfinite(loss):
            raise RuntimeError("nonfinite weighted training loss")
        loss.backward()
        gaussians.optimizer.step()
        gaussians.optimizer.zero_grad(set_to_none=True)
        if iteration == 1 or iteration % 100 == 0:
            print(json.dumps({"iteration": iteration, "weighted_l1": loss.item()}), flush=True)
    args.output.mkdir(parents=True, exist_ok=False)
    gaussians.save_ply(str(args.output / "point_cloud.ply"))
    evaluation = []
    with torch.no_grad():
        for frame, camera, _, observed in views:
            if frame["split"] != "holdout" or not observed.any():
                continue
            prediction = render(camera, gaussians, pipe, background)["render"]
            truth = target(frame, "real_ref")
            mask = torch.from_numpy(observed).cuda()
            mse = ((prediction - truth).square() * mask).sum() / (3 * mask.sum())
            evaluation.append(
                {
                    "frame_id": frame["frame_id"],
                    "observed_pixels": int(observed.sum()),
                    "masked_mse": mse.item(),
                }
            )
    write_json(
        args.output / "result.json",
        {
            "status": "completed",
            "iterations": args.iterations,
            "holdout": evaluation,
            "supervision": "L1: observed=1; completion=synthetic_weight; unknown/excluded=0",
            "topology": "fixed background seeds; no densification/pruning",
            "synthetic_weight": args.synthetic_weight,
            "independent_acceptance": False,
            "physics_status": "not_built",
            "metric_scale": "unknown",
        },
    )


if __name__ == "__main__":
    main()
