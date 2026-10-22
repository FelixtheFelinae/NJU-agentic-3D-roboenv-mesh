"""Linux COLMAP orchestration and a foreground-filtered, mask-aware 3DGS dataset."""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ..io import read_json, relative_file, sha256, write_json
from .common import cv2_module, new_run, read_mask, source_identity, verify_run
from .contracts import VERSION, BackgroundConfig


def run_process(argv: list[str], cwd: Path, log: Path, timeout: int):
    """No shell; preserve logs, failure codes, timeout and process-group cleanup."""
    log.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"argv": argv, "cwd": str(cwd), "started_unix": time.time(), "status": "running"}
    write_json(log.with_suffix(".json"), receipt)
    process = None
    try:
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(
                argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
            )
            receipt["pid"] = process.pid
            process.wait(timeout=timeout)
        receipt["returncode"] = process.returncode
        if process.returncode:
            raise RuntimeError(f"command exited {process.returncode}; see {log}")
        receipt["status"] = "completed"
    except BaseException as error:
        receipt.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        receipt["ended_unix"] = time.time()
        write_json(log.with_suffix(".json"), receipt)


def read_colmap_text(root: Path):
    cameras, images, points = {}, {}, {}
    for line in (root / "cameras.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        cameras[int(parts[0])] = {
            "model": parts[1],
            "width": int(parts[2]),
            "height": int(parts[3]),
            "params": list(map(float, parts[4:])),
        }
    lines = (root / "images.txt").read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split(maxsplit=9)
        if len(parts) != 10 or i >= len(lines):
            raise ValueError("malformed COLMAP images.txt")
        observations = lines[i].split()
        i += 1  # This may be an empty line, not the next image header.
        if len(observations) % 3:
            raise ValueError("malformed COLMAP image observations")
        images[int(parts[0])] = {
            "qvec": list(map(float, parts[1:5])),
            "tvec": list(map(float, parts[5:8])),
            "camera_id": int(parts[8]),
            "name": parts[9],
            "points": [
                (float(observations[j]), float(observations[j + 1]), int(observations[j + 2]))
                for j in range(0, len(observations), 3)
            ],
        }
    for line in (root / "points3D.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p = line.split()
        points[int(p[0])] = {"xyz": list(map(float, p[1:4])), "rgb": list(map(int, p[4:7]))}
    return cameras, images, points


def intrinsics(camera):
    model, p = camera["model"], camera["params"]
    if model in {"SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"}:
        f, cx, cy = p[:3]
        fx, fy = f, f
        d = [p[3] if len(p) > 3 else 0, p[4] if len(p) > 4 else 0, 0, 0]
    elif model in {"PINHOLE", "OPENCV"}:
        fx, fy, cx, cy = p[:4]
        d = p[4:8] if model == "OPENCV" else [0, 0, 0, 0]
    else:
        raise ValueError(f"unsupported distortion model: {model}")
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64), np.array(d)


def world_to_camera(image):
    w, x, y, z = np.asarray(image["qvec"], dtype=float)
    norm = np.linalg.norm([w, x, y, z])
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("invalid COLMAP quaternion")
    w, x, y, z = np.array([w, x, y, z]) / norm
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = image["tvec"]
    return matrix


def write_ply(path, points):
    with path.open("w", encoding="ascii") as handle:
        handle.write(
            f"ply\nformat ascii 1.0\nelement vertex {len(points)}\n"
            "property float x\nproperty float y\nproperty float z\n"
            "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
        )
        for p in points:
            handle.write(" ".join(map(str, [*p["xyz"], *p["rgb"]])) + "\n")


def estimate_background_cameras(
    background: Path, output: Path, config: BackgroundConfig, completion: Path | None = None
):
    cv2 = cv2_module()
    data = verify_run(background, "background.json")
    if len(data["frames"]) < 3:
        raise ValueError("background camera reconstruction requires at least three views")
    completed = {}
    sources = {"background": source_identity(background)}
    if completion:
        completed_data = verify_run(completion, "completion.json")
        expected = completed_data["background_source"]["artifact_index_sha256"]
        if expected != sha256(background / "artifacts.json"):
            raise ValueError("table completion belongs to another background version")
        completed = {f["frame_id"]: f for f in completed_data["frames"]}
        sources["completion"] = source_identity(completion)
    with new_run(output, "background_cameras", sources):
        image_dir, mask_dir = output / "input/images", output / "input/sfm_masks"
        image_dir.mkdir(parents=True)
        mask_dir.mkdir(parents=True)
        for frame in data["frames"]:
            shutil.copyfile(
                relative_file(background, frame["image_ref"]), image_dir / frame["image_name"]
            )
            shutil.copyfile(
                relative_file(background, frame["sfm_mask_ref"]),
                mask_dir / (frame["image_name"] + ".png"),
            )
        database = output / "database.db"
        sparse = output / "sparse"
        sparse.mkdir()
        argv = [
            config.colmap,
            "feature_extractor",
            "--database_path",
            str(database.resolve()),
            "--image_path",
            str(image_dir.resolve()),
            "--ImageReader.mask_path",
            str(mask_dir.resolve()),
            "--ImageReader.camera_model",
            config.camera_model,
            "--ImageReader.single_camera",
            str(int(config.single_camera)),
            "--SiftExtraction.use_gpu",
            str(int(config.use_gpu_features)),
        ]
        run_process(argv, output.resolve(), output / "logs/features.log", config.timeout_seconds)
        run_process(
            [
                config.colmap,
                config.matching + "_matcher",
                "--database_path",
                str(database.resolve()),
                "--SiftMatching.use_gpu",
                str(int(config.use_gpu_features)),
            ],
            output.resolve(),
            output / "logs/matching.log",
            config.timeout_seconds,
        )
        run_process(
            [
                config.colmap,
                "mapper",
                "--database_path",
                str(database.resolve()),
                "--image_path",
                str(image_dir.resolve()),
                "--output_path",
                str(sparse.resolve()),
            ],
            output.resolve(),
            output / "logs/mapper.log",
            config.timeout_seconds,
        )
        models = sorted(p for p in sparse.iterdir() if p.is_dir() and (p / "images.bin").exists())
        # A single consistent model is required. Do not silently pick one disconnected fragment.
        if len(models) != 1:
            raise ValueError(
                f"expected one connected COLMAP model, got {len(models)}; inspect capture"
            )
        original_text = output / "colmap_original"
        original_text.mkdir()
        run_process(
            [
                config.colmap,
                "model_converter",
                "--input_path",
                str(models[0].resolve()),
                "--output_path",
                str(original_text.resolve()),
                "--output_type",
                "TXT",
            ],
            output.resolve(),
            output / "logs/original_text.log",
            config.timeout_seconds,
        )
        original_cameras, original_images, original_points = read_colmap_text(original_text)
        requested = {f["image_name"] for f in data["frames"]}
        registered = {v["name"] for v in original_images.values()}
        if registered != requested:
            raise ValueError(f"unregistered or unexpected views: {sorted(registered ^ requested)}")
        undistorted = output / "undistorted"
        run_process(
            [
                config.colmap,
                "image_undistorter",
                "--image_path",
                str(image_dir.resolve()),
                "--input_path",
                str(models[0].resolve()),
                "--output_path",
                str(undistorted.resolve()),
                "--output_type",
                "COLMAP",
            ],
            output.resolve(),
            output / "logs/undistort.log",
            config.timeout_seconds,
        )
        dataset = output / "dataset"
        dataset.mkdir()
        text = dataset / "sparse/0"
        text.mkdir(parents=True)
        run_process(
            [
                config.colmap,
                "model_converter",
                "--input_path",
                str((undistorted / "sparse").resolve()),
                "--output_path",
                str(text.resolve()),
                "--output_type",
                "TXT",
            ],
            output.resolve(),
            output / "logs/undistorted_text.log",
            config.timeout_seconds,
        )
        cameras, images, _ = read_colmap_text(text)
        originals = {im["name"]: im for im in original_images.values()}
        poses = {im["name"]: im for im in images.values()}
        frame_by_name = {f["image_name"]: f for f in data["frames"]}
        mask_cache, image_cache, records = {}, {}, []
        for name, pose in poses.items():
            frame = frame_by_name[name]
            original = originals[name]
            old_k, distortion = intrinsics(original_cameras[original["camera_id"]])
            camera = cameras[pose["camera_id"]]
            new_k, new_distortion = intrinsics(camera)
            if new_distortion.any():
                raise ValueError("COLMAP undistortion did not produce pinhole cameras")
            size = (camera["width"], camera["height"])
            maps = cv2.initUndistortRectifyMap(old_k, distortion, None, new_k, size, cv2.CV_32FC1)
            observed = read_mask(
                relative_file(background, frame["observed_mask_ref"]), frame["size"]
            )
            # Linear remapping with a conservative threshold removes mixed foreground boundaries.
            observed_u = cv2.remap(observed.astype(np.float32), *maps, cv2.INTER_LINEAR) >= 0.999
            observed_u = cv2.erode(observed_u.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            with Image.open(relative_file(background, frame["image_ref"])) as raw:
                real = np.asarray(raw.convert("RGB"))
            real_u = cv2.remap(real, *maps, cv2.INTER_LINEAR)
            training = real_u.copy()
            synth_u = np.zeros(observed_u.shape, dtype=bool)
            if frame["frame_id"] in completed and frame["split"] == "train":
                filled = completed[frame["frame_id"]]
                with Image.open(relative_file(completion, filled["image_ref"])) as raw:
                    completed_rgb = np.asarray(raw.convert("RGB"))
                if completed_rgb.shape != real.shape:
                    raise ValueError("completed background image changed frame geometry")
                synthetic = read_mask(
                    relative_file(completion, filled["filled_mask_ref"]), frame["size"]
                )
                synth_u = (
                    cv2.remap(synthetic.astype(np.float32), *maps, cv2.INTER_LINEAR) >= 0.999
                ) & ~observed_u
                warped = cv2.remap(completed_rgb, *maps, cv2.INTER_LINEAR)
                training[synth_u] = warped[synth_u]
            for folder in ["images", "real", "observed", "synthetic"]:
                (dataset / folder).mkdir(exist_ok=True)
            Image.fromarray(training).save(dataset / "images" / name)
            Image.fromarray(real_u).save(dataset / "real" / name)
            Image.fromarray(observed_u.astype(np.uint8) * 255).save(dataset / "observed" / name)
            Image.fromarray(synth_u.astype(np.uint8) * 255).save(dataset / "synthetic" / name)
            mask_cache[name], image_cache[name] = observed_u, real_u
            transform = world_to_camera(pose)
            records.append(
                {
                    "frame_id": frame["frame_id"],
                    "image_name": name,
                    "split": frame["split"],
                    "image_ref": f"dataset/images/{name}",
                    "real_ref": f"dataset/real/{name}",
                    "observed_mask_ref": f"dataset/observed/{name}",
                    "synthetic_mask_ref": f"dataset/synthetic/{name}",
                    "size": list(size),
                    "intrinsics": new_k.tolist(),
                    "world_to_camera": transform.tolist(),
                    "camera_to_world": np.linalg.inv(transform).tolist(),
                    "observed_pixels": int(observed_u.sum()),
                }
            )
        # Reproject each sparse point into ALL registered views. Reject a point if any
        # training view says it belongs to an excluded/unknown region. Conservative by design.
        # This avoids object points becoming permanent background ghosts.
        accepted = []
        for point in original_points.values():
            colors, rejected = [], False
            xyz = np.asarray(point["xyz"])
            for record in records:
                if record["split"] != "train":
                    continue
                matrix = np.asarray(record["world_to_camera"])
                p = matrix[:3, :3] @ xyz + matrix[:3, 3]
                if p[2] <= 0:
                    continue
                pixel = np.asarray(record["intrinsics"]) @ p
                x, y = np.rint(pixel[:2] / pixel[2]).astype(int)
                name = record["image_name"]
                mask = mask_cache[name]
                if 0 <= x < mask.shape[1] and 0 <= y < mask.shape[0]:
                    if not mask[y, x]:
                        rejected = True
                        break
                    colors.append(image_cache[name][y, x])
            if not rejected and len(colors) >= 2:
                accepted.append(
                    {"xyz": point["xyz"], "rgb": np.median(colors, axis=0).astype(int).tolist()}
                )
        if len(accepted) < 4:
            raise ValueError("too few conservative background seed points; inspect masks/capture")
        write_ply(dataset / "background_points.ply", accepted)
        # Prevent downstream users from accidentally initializing with the unfiltered points.
        (text / "points3D.txt").unlink()
        write_json(
            output / "cameras.json",
            {
                "schema_version": VERSION,
                "scene_id": data["scene_id"],
                "frames": records,
                "points_ref": "dataset/background_points.ply",
                "background_seed_count": len(accepted),
                "scale_status": "arbitrary_SfM_units",
                "metres_per_sfm_unit": None,
                "world_frame": "COLMAP world; camera +X right, +Y down, +Z forward",
                "holdout_policy": data["holdout_policy"],
                "physical_geometry": None,
                "limitations": [
                    "SfM scale and object-local frames still need metric scene alignment.",
                    "Conservative point filtering may reject occluded real background.",
                    "Table atlas dimensions alone do not calibrate the SfM scene scale.",
                ],
            },
        )
    return output


def train_background(cameras: Path, output: Path, config: BackgroundConfig):
    data = verify_run(cameras, "cameras.json")
    repo = Path(config.gaussian_repo).resolve()
    if not config.gaussian_repo or not (repo / "gaussian_renderer/__init__.py").is_file():
        raise ValueError(
            "background.gaussian_repo must point to an installed classic 3DGS checkout"
        )
    worker = Path(__file__).with_name("train_worker.py").resolve()
    with new_run(output, "background_gaussian_training", source_identity(cameras)) as run:
        # Copy the exact inputs into the run so moving the original bundle cannot change it.
        dataset = output / "input"
        dataset.mkdir()
        copied = {**data, "frames": []}
        for frame in data["frames"]:
            row = dict(frame)
            for key in ["image_ref", "real_ref", "observed_mask_ref", "synthetic_mask_ref"]:
                src = relative_file(cameras, frame[key])
                dst = dataset / frame[key]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            copied["frames"].append(row)
        src_points = relative_file(cameras, data["points_ref"])
        dst_points = dataset / data["points_ref"]
        dst_points.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_points, dst_points)
        write_json(dataset / "cameras.json", copied)
        try:
            revision = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            revision = None
        run["gaussian_backend"] = {
            "path": str(repo),
            "revision": revision,
            "worker_sha256": sha256(worker),
            "iterations": config.iterations,
            "synthetic_weight": config.synthetic_weight,
        }
        run_process(
            [
                config.gaussian_python,
                str(worker),
                "--repo",
                str(repo),
                "--dataset",
                str(dataset.resolve()),
                "--output",
                str((output / "model").resolve()),
                "--iterations",
                str(config.iterations),
                "--synthetic-weight",
                str(config.synthetic_weight),
            ],
            output.resolve(),
            output / "logs/training.log",
            config.timeout_seconds,
        )
        result = read_json(output / "model/result.json")
        if result.get("status") != "completed" or not (output / "model/point_cloud.ply").is_file():
            raise ValueError("training did not produce its required result and Gaussian asset")
        write_json(
            output / "background_model.json",
            {
                "schema_version": VERSION,
                "model_ref": "model/point_cloud.ply",
                "cameras_ref": "input/cameras.json",
                "result_ref": "model/result.json",
                "scale_status": data["scale_status"],
                "physics_status": "not_built",
                "independent_acceptance": False,
                "backend": run["gaussian_backend"],
            },
        )
    return output
