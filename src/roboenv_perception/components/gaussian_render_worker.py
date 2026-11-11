"""Runs in the configured classic-3DGS CUDA interpreter; no appearance substitution."""

import argparse
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--camera", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import numpy as np
    import torch
    from gaussian_renderer import render
    from PIL import Image
    from scene.gaussian_model import GaussianModel

    from roboenv_perception.construction.supervision import projection_matrix

    if not torch.cuda.is_available():
        raise RuntimeError("3DGS 渲染需要可用的 CUDA 环境")
    frame = json.loads(args.camera.read_text())
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
    model = GaussianModel(3)
    model.load_ply(str(args.model))
    model.active_sh_degree = model.max_sh_degree
    pipe = SimpleNamespace(
        debug=False, antialiasing=False, convert_SHs_python=False, compute_cov3D_python=False
    )
    with torch.no_grad():
        image = render(camera, model, pipe, torch.zeros(3, device="cuda"))["render"]
        pixels = image.clamp(0, 1).permute(1, 2, 0).cpu().numpy()
        Image.fromarray((pixels * 255).astype(np.uint8)).save(args.output)


if __name__ == "__main__":
    main()
