"""Fit an estimated metric floor/bicycle frame and fuse observed floor RGB only.

A narrow scene-specific experiment, not an automatic semantic reconstruction.
Hub annotations and the radius prior remain explicit inputs. No RGB inpainting.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation


def read_model(folder):
    lines = [s for s in (folder / "images.txt").read_text().splitlines() if not s.startswith("#")]
    cameras = {}
    for line in lines[::2]:
        f = line.split()
        q = np.array(list(map(float, f[1:5])))
        r = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
        cameras[f[9]] = (r, np.array(list(map(float, f[5:8]))))
    line = next(
        s for s in (folder / "cameras.txt").read_text().splitlines() if not s.startswith("#")
    )
    f = line.split()
    if f[1] != "SIMPLE_RADIAL":
        raise ValueError("This experiment expects COLMAP SIMPLE_RADIAL")
    return cameras, np.array(list(map(float, f[4:])))


def project(xyz, camera, intrinsics):
    r, t = camera
    p = xyz @ r.T + t
    uv = p[:, :2] / p[:, 2:3]
    focal, cx, cy, k = intrinsics
    return uv * (1 + k * (uv * uv).sum(1, keepdims=True)) * focal + [cx, cy], p[:, 2]


def fit_plane(points):
    rng = np.random.default_rng(17)
    best = np.zeros(len(points), bool)
    for _ in range(1200):
        a, b, c = points[rng.choice(len(points), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        inside = abs((points - a) @ n) < 0.018
        if inside.sum() > best.sum():
            best = inside
    for _ in range(4):
        centre = np.median(points[best], axis=0)
        _, _, v = np.linalg.svd(points[best] - centre, full_matrices=False)
        n = v[-1]
        best = abs((points - centre) @ n) < 0.018
    return centre, n, best


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    bundle = root / "perception"
    cameras, intrinsics = read_model(root / "camera-recovery/text-0")
    annotations = json.loads((root / "alignment-input/wheel-landmarks.json").read_text())
    shutil.copyfile(root / "alignment-input/wheel-landmarks.json", output / "wheel-landmarks.json")
    # Fit the dominant plane from actual sparse points. Background masks prevent
    # the bicycle and other segmented objects from seeding the plane.
    allowed = {}
    for name in cameras:
        index = int(Path(name).stem)
        mask = (
            np.array(Image.open(bundle / f"frames/frame-{index:06}/background/background.png")) > 0
        )
        mask[: int(mask.shape[0] * 0.38)] = False
        allowed[name] = cv2.erode(mask.astype("uint8"), np.ones((15, 15), "uint8")) > 0
    points = []
    for line in (root / "camera-recovery/text-0/points3D.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split()
        xyz = np.array(list(map(float, f[1:4])))
        if float(f[7]) > 2 or (len(f) - 8) // 2 < 4:
            continue
        votes = 0
        for name in list(cameras)[::4]:
            uv, depth = project(xyz[None], cameras[name], intrinsics)
            u, v = np.rint(uv[0]).astype(int)
            mask = allowed[name]
            if depth[0] > 0 and 0 <= v < mask.shape[0] and 0 <= u < mask.shape[1] and mask[v, u]:
                votes += 1
        if votes >= 2:
            points.append(xyz)
    points = np.array(points)
    centre, normal, inside = fit_plane(points)
    camera_centres = np.array([-r.T @ t for r, t in cameras.values()])
    if np.median((camera_centres - centre) @ normal) < 0:
        normal = -normal
    hubs, errors = {}, {}
    for wheel in ["rear", "front"]:
        observations = [
            (name, np.array(h[wheel]) * 2) for name, h in annotations["observations"].items()
        ]

        def residual(p):
            return np.concatenate(
                [
                    project(p[None], cameras[name], intrinsics)[0][0] - uv
                    for name, uv in observations
                ]
            )

        fit = least_squares(residual, centre + normal * 0.5, loss="soft_l1", f_scale=5)
        hubs[wheel] = fit.x
        errors[wheel] = {
            name: float(np.linalg.norm(project(fit.x[None], cameras[name], intrinsics)[0][0] - uv))
            for name, uv in observations
        }
    height = np.mean([(p - centre) @ normal for p in hubs.values()])
    scale = annotations["wheel_radius_prior_m"] / height
    forward = hubs["front"] - hubs["rear"]
    forward -= normal * (forward @ normal)
    forward /= np.linalg.norm(forward)
    sideways = np.cross(normal, forward)
    axes = np.stack([forward, sideways, normal])
    midpoint = (hubs["front"] + hubs["rear"]) / 2
    origin = midpoint - normal * ((midpoint - centre) @ normal)
    world_hubs = {k: (scale * axes @ (v - origin)).tolist() for k, v in hubs.items()}
    report = {
        "registered_views": len(cameras),
        "sparse_floor_candidates": len(points),
        "plane_inliers": int(inside.sum()),
        "floor_plane_rmse_m": float(
            scale * np.sqrt(np.mean(((points[inside] - centre) @ normal) ** 2))
        ),
        "scale_m_per_colmap_unit": float(scale),
        "scale_source": "assumed 0.30 m wheel radius and manually annotated hub centres",
        "relative_scale_uncertainty_assumed": 0.15,
        "hub_reprojection_errors_px": errors,
        "world_hubs_m": world_hubs,
        "wheelbase_m": float(np.linalg.norm(hubs["front"] - hubs["rear"]) * scale),
        "colmap_origin": origin.tolist(),
        "colmap_to_world_rotation": axes.tolist(),
        "floor_extent_xy_m": [-1.8, -1.5, 1.8, 1.5],
        "assumptions": [
            "Floor is a plane. Radius and all metric dimensions are estimates, not measurements.",
            "Bicycle is a video-conditioned procedural mesh, not a photogrammetric mesh.",
            "Masks are eroded and top 38% excluded; unseen texels remain neutral and unsupervised.",
            "Procedural asset wheelbase is 1.08 m. Triangulated hub positions have manual-label error; placement is approximate.",
        ],
    }
    report["source_sha256"] = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            root / "alignment-input/wheel-landmarks.json",
            root / "camera-recovery/text-0/cameras.txt",
            root / "camera-recovery/text-0/images.txt",
            root / "camera-recovery/text-0/points3D.txt",
        ]
    }
    report["asset_hub_fit_discrepancy_m"] = {
        wheel: float(np.linalg.norm(np.array(world_hubs[wheel]) - [x, 0, 0.30]))
        for wheel, x in [("rear", -0.54), ("front", 0.54)]
    }
    (output / "alignment.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    # Inverse project a metric floor atlas into all training RGB views. Visibility
    # comes from the original semantic background masks, never a filled image.
    width, height_px = 1536, 1280
    x, y = np.meshgrid(np.linspace(-1.8, 1.8, width), np.linspace(1.5, -1.5, height_px))
    world = np.c_[x.ravel(), y.ravel(), np.zeros(x.size)]
    colmap = world @ axes / scale + origin
    atlas = np.full((world.shape[0], 3), 110, dtype=np.uint8)
    confidence = np.zeros(len(world), dtype=np.float32)
    source = np.full(len(world), -1, dtype=np.int16)
    holdout = {5, 17, 29}
    for name, (r, t) in sorted(cameras.items()):
        index = int(Path(name).stem)
        if index in holdout:
            continue
        uv, z = project(colmap, (r, t), intrinsics)
        mx, my = [uv[:, i].astype("float32").reshape(height_px, width) for i in [0, 1]]
        valid = cv2.remap(allowed[name].astype("uint8"), mx, my, cv2.INTER_NEAREST).ravel() > 0
        valid &= z > 0
        # Closest valid observation yields the sharpest local texture. Retain
        # source frame per texel instead of claiming unknown regions are observed.
        score = 1 / np.maximum(z, 0.001) ** 2
        choose = valid & (score > confidence)
        rgb = np.array(Image.open(bundle / "inputs/frames" / name).convert("RGB"))
        warped = cv2.remap(rgb, mx, my, cv2.INTER_LINEAR).reshape(-1, 3)
        atlas[choose], confidence[choose], source[choose] = warped[choose], score[choose], index
    Image.fromarray(atlas.reshape(height_px, width, 3)).save(output / "ground-observed.png")
    Image.fromarray((confidence.reshape(height_px, width) > 0).astype("uint8") * 255).save(
        output / "ground-observed-mask.png"
    )
    np.save(output / "ground-source-frame.npy", source.reshape(height_px, width))
    report["atlas_observed_fraction"] = float(np.mean(confidence > 0))
    report["holdout_frames_not_used_for_atlas"] = sorted(holdout)
    # Diagnostic overlays show projected hub estimates and floor grid in the
    # original views; these permit checking the coordinate fit by eye.
    for index in [0, 18, 30]:
        name = f"{index:06}.png"
        rgb = Image.open(bundle / "inputs/frames" / name).convert("RGB")
        draw = ImageDraw.Draw(rgb)
        for p in [
            np.c_[np.full(61, u), np.linspace(-1.5, 1.5, 61), np.zeros(61)]
            for u in np.arange(-1.5, 1.6, 0.3)
        ]:
            uv, z = project(p @ axes / scale + origin, cameras[name], intrinsics)
            if (z > 0).all():
                draw.line([tuple(v) for v in uv], fill=(10, 220, 230), width=2)
        for wheel, p in hubs.items():
            uv, _ = project(p[None], cameras[name], intrinsics)
            u, v = uv[0]
            draw.ellipse((u - 12, v - 12, u + 12, v + 12), outline="red", width=4)
            draw.text((u + 15, v), wheel, fill="red", stroke_width=1)
        rgb.resize((960, 540)).save(output / f"fit-{index:02}.jpg")
    (output / "alignment.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
