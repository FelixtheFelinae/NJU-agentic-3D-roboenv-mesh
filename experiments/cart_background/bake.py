"""Render trained 3DGS from above to a MuJoCo-compatible floor appearance atlas.

The atlas is a baked view-dependent appearance, not an estimated PBR material.
Unknown floor regions are neutral and explicitly masked, never inpainted.
"""

import argparse
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

p = argparse.ArgumentParser()
p.add_argument("--model", type=Path, required=True)
p.add_argument("--cameras", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from roboenv_perception.construction.supervision import projection_matrix  # noqa: E402

sys.path.insert(0, "/home/felixz/.local/share/roboenv/gaussian-splatting")
import torch  # noqa: E402
from gaussian_renderer import render  # noqa: E402
from plyfile import PlyData  # noqa: E402
from scene.gaussian_model import GaussianModel  # noqa: E402

align = json.loads((a.cameras / "alignment.json").read_text())
data = json.loads((a.cameras / "cameras.json").read_text())
origin = np.array(align["world_origin_sfm"])
axes = np.array(align["sfm_to_world_rotation"])
scale = align["metres_per_sfm_unit"]
# Crop trained Gaussians to a declared near-floor slab; exclude floating spillover.
vertices = PlyData.read(a.model / "point_cloud.ply")["vertex"].data
xyz = np.stack([vertices[k] for k in ("x", "y", "z")], 1)
local = (xyz - origin) @ axes.T * scale
keep = (np.abs(local[:, 2]) < 0.035) & (np.abs(local[:, :2]).max(1) < 1.3)
from plyfile import PlyElement  # noqa: E402

PlyData([PlyElement.describe(vertices[keep], "vertex")]).write(
    str(a.output / "floor_gaussians.ply")
)
gs = GaussianModel(3)
gs.load_ply(str(a.output / "floor_gaussians.ply"))
gs.active_sh_degree = 3
size = 1536
extent = 1.2
distance = 15.0 / scale
centre = origin + axes[2] * distance
rotation = np.stack([axes[0], -axes[1], -axes[2]])
w2c = np.eye(4)
w2c[:3, :3] = rotation
w2c[:3, 3] = -rotation @ centre
focal = size * distance / (2 * extent / scale)
K = np.array([[focal, 0, size / 2], [0, focal, size / 2], [0, 0, 1]])
world = torch.tensor(w2c, dtype=torch.float32, device="cuda").T
projection = torch.tensor(projection_matrix(K, [size, size]), device="cuda").T
camera = SimpleNamespace(
    image_name="top_down",
    image_width=size,
    image_height=size,
    FoVx=2 * math.atan(size / (2 * focal)),
    FoVy=2 * math.atan(size / (2 * focal)),
    world_view_transform=world,
    full_proj_transform=world @ projection,
    camera_center=world.inverse()[3, :3],
)
pipe = SimpleNamespace(
    debug=False, antialiasing=False, convert_SHs_python=False, compute_cov3D_python=False
)
with torch.no_grad():
    black = (
        render(camera, gs, pipe, torch.zeros(3, device="cuda"))["render"]
        .permute(1, 2, 0)
        .cpu()
        .numpy()
    )
    white = (
        render(camera, gs, pipe, torch.ones(3, device="cuda"))["render"]
        .permute(1, 2, 0)
        .cpu()
        .numpy()
    )
alpha = 1 - np.mean(white - black, axis=2)
# Independent observed floor coverage by projecting atlas texels into source masks.
y, x = np.mgrid[0:size, 0:size]
world_xy = np.stack(
    [(x - size / 2) / size * 2 * extent, (size / 2 - y) / size * 2 * extent, np.zeros_like(x)], -1
)
points = world_xy.reshape(-1, 3) @ axes / scale + origin
coverage = np.zeros(len(points), np.uint16)
for frame in data["frames"]:
    if frame["split"] != "train":
        continue
    t = np.array(frame["world_to_camera"])
    k = np.array(frame["intrinsics"])
    q = points @ t[:3, :3].T + t[:3, 3]
    pix = q @ k.T
    uv = np.rint(pix[:, :2] / pix[:, 2:]).astype(int)
    mask = np.array(Image.open(a.cameras / frame["observed_mask_ref"])) > 0
    ok = (
        (q[:, 2] > 0)
        & (uv[:, 0] >= 0)
        & (uv[:, 1] >= 0)
        & (uv[:, 0] < mask.shape[1])
        & (uv[:, 1] < mask.shape[0])
    )
    ids = np.where(ok)[0]
    coverage[ids] += mask[uv[ids, 1], uv[ids, 0]]
coverage = coverage.reshape(size, size)
valid = (coverage >= 2) & (alpha > 0.8)
# Unpremultiply against black; neutral unknowns are explicit in coverage_mask.png.
rgb = np.clip(black / np.maximum(alpha[..., None], 0.01), 0, 1)
rgb[~valid] = [0.46, 0.45, 0.43]
Image.fromarray((rgb * 255).astype("uint8")).save(a.output / "floor_3dgs.png")
Image.fromarray((np.clip(black, 0, 1) * 255).astype("uint8")).save(
    a.output / "raw_3dgs_topdown.png"
)
Image.fromarray(valid.astype("uint8") * 255).save(a.output / "coverage_mask.png")
np.save(a.output / "coverage_count.npy", coverage)
report = {
    "source": "trained Gaussian rasterizer top-down rendering",
    "gaussians_before": len(vertices),
    "gaussians_retained": int(keep.sum()),
    "plane_slab_halfwidth_m_assumed": 0.035,
    "extent_xy_m": [-extent, -extent, extent, extent],
    "resolution": [size, size],
    "observed_and_opaque_fraction": float(valid.mean()),
    "projection": "near orthographic perspective at 15m; approximate planar bake",
    "unknown_fill": "neutral grey; no inpainting",
    "material": "baked RGB appearance incl. lighting; no measured roughness/friction/height",
    "alignment": align,
}
(a.output / "bake.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
