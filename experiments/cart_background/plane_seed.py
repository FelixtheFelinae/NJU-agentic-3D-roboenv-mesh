"""Fit sparse observed floor plane; initialize extra plane points from real RGB.

This is an explicit planar initialization, not extra triangulated geometry.
No excluded/unknown pixels supply colors. SfM scale remains an assumed metric fit.
"""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from plyfile import PlyData

from roboenv_perception.construction.common import new_run, source_identity
from roboenv_perception.construction.reconstruction import write_ply
from roboenv_perception.io import write_json

p = argparse.ArgumentParser()
p.add_argument("--cameras", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
data = json.loads((a.cameras / "cameras.json").read_text())
v = PlyData.read(a.cameras / data["points_ref"])["vertex"]
xyz = np.stack([v[k] for k in ("x", "y", "z")], 1)
centre = np.median(xyz, axis=0)
_, _, vh = np.linalg.svd(xyz - centre)
normal = vh[-1]
cameras = np.asarray([f["camera_to_world"] for f in data["frames"]])[:, :3, 3]
if np.median((cameras - centre) @ normal) < 0:
    normal = -normal


def intersect(frame, uv):
    k = np.array(frame["intrinsics"])
    c2w = np.array(frame["camera_to_world"])
    o = c2w[:3, 3]
    ray = np.c_[uv, np.ones(len(uv))] @ np.linalg.inv(k).T @ c2w[:3, :3].T
    den = ray @ normal
    depth = ((centre - o) @ normal) / den
    return o + depth[:, None] * ray, depth


# Ground projection of the centre of the first frame's vehicle mask.
f = data["frames"][0]
origin = intersect(f, np.array([[310.0, 190.0]]))[0][0]
right = np.array(f["camera_to_world"])[:3, 0]
right -= normal * (right @ normal)
right /= np.linalg.norm(right)
up = np.cross(normal, right)
axes = np.stack([right, up, normal])
ends = intersect(f, np.array([[135.0, 255.0], [470.0, 255.0]]))[0]
scale = 0.40 / np.linalg.norm(ends[1] - ends[0])
points = []
for frame in data["frames"]:
    if frame["split"] != "train":
        continue
    mask = np.array(Image.open(a.cameras / frame["observed_mask_ref"])) > 0
    rgb = np.array(Image.open(a.cameras / frame["real_ref"]))
    yy, xx = np.mgrid[0 : mask.shape[0] : 5, 0 : mask.shape[1] : 5]
    valid = mask[yy, xx]
    uv = np.c_[xx[valid], yy[valid]]
    cloud, depth = intersect(frame, uv)
    local = (cloud - origin) @ axes.T * scale
    ok = (depth > 0) & (np.abs(local[:, :2]).max(1) < 1.2)
    for pt, col in zip(cloud[ok], rgb[uv[ok, 1], uv[ok, 0]]):
        points.append([*pt, *col])
points = np.array(points)
# Voxel selection prevents repeated views from overwhelming initial point density.
keys = np.floor((points[:, :3] - origin) * scale / 0.006).astype(int)
_, ids = np.unique(keys, axis=0, return_index=True)
points = points[ids]
with new_run(a.output, "floor_planar_seed_initialization", source_identity(a.cameras)):
    for frame in data["frames"]:
        for key in ["image_ref", "real_ref", "observed_mask_ref", "synthetic_mask_ref"]:
            dst = a.output / frame[key]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(a.cameras / frame[key], dst)
    write_ply(
        a.output / data["points_ref"],
        [{"xyz": r[:3].tolist(), "rgb": r[3:].astype(int).tolist()} for r in points],
    )
    write_json(
        a.output / "cameras.json",
        {
            **data,
            "background_seed_count": len(points),
            "seed_filter": "real observed training RGB backprojected to sparse fitted plane; explicit geometry prior",
        },
    )
    write_json(
        a.output / "alignment.json",
        dict(
            plane_origin_sfm=centre.tolist(),
            normal_sfm=normal.tolist(),
            world_origin_sfm=origin.tolist(),
            sfm_to_world_rotation=axes.tolist(),
            metres_per_sfm_unit=scale,
            sparse_plane_rmse_sfm=float(np.sqrt(np.mean(((xyz - centre) @ normal) ** 2))),
            sparse_point_count=len(xyz),
            planar_seed_count=len(points),
            scale_source="assumed 0.40 m projected footprint span in first view; not measured",
            car_placement="approximate centre, heading chosen for presentation; not calibrated wheel landmarks",
            plane_assumption="flat foam floor; relief retained in appearance only",
        ),
    )
print("planar seeds", len(points), "scale", scale)
