"""Extract an observed 3DGS appearance patch; tile repetition is explicit synthesis."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def choose_patch(rgb, mask):
    for size in (160, 128, 96, 64):
        candidates = []
        for y in range(0, rgb.shape[0] - size + 1, 16):
            for x in range(0, rgb.shape[1] - size + 1, 16):
                a = rgb[y : y + size, x : x + size]
                m = mask[y : y + size, x : x + size]
                if not m.all():
                    continue
                # Reject colored tape and dark splat artifacts rather than painting them out.
                if np.mean(np.max(a, 2) - np.min(a, 2)) > 20 or np.quantile(a, 0.001) < 120:
                    continue
                candidates.append((float(a.std()), x, y, size))
        if candidates:
            return max(candidates)
    raise ValueError("No sufficiently observed clean material patch; do not invent one")


def mirrored_tile(patch):
    top = np.concatenate([patch, patch[:, ::-1]], 1)
    return np.concatenate([top, top[::-1]], 0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bake", type=Path, required=True)
    a = p.parse_args()
    rgb = np.array(Image.open(a.bake / "floor_3dgs.png"))
    mask = np.array(Image.open(a.bake / "coverage_mask.png")) > 0
    _, x, y, n = choose_patch(rgb, mask)
    patch = rgb[y : y + n, x : x + n]
    Image.fromarray(mirrored_tile(patch)).save(a.bake / "floor_material_tile.png")
    Image.fromarray(patch).resize((512, 512)).save(a.bake / "material_patch_preview.png")
    report = {
        "source": "floor_3dgs.png, trained Gaussian rasterizer output",
        "source_crop_xywh": [x, y, n, n],
        "observed_fraction": float(mask[y : y + n, x : x + n].mean()),
        "operation": "mirrored repeat; appearance synthesis, not reconstructed unseen floor",
        "tile_width_m_assumed": 2 * n / rgb.shape[1] * 2.4,
    }
    (a.bake / "material_tile.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report)


if __name__ == "__main__":
    main()
