"""Generate a textured synthetic 3D room for COLMAP/3DGS integration checks.

Analytic ray intersections provide consistent parallax and known cameras. This is
not the user's scene, a learned reconstruction, or a SAM accuracy evaluation.
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from roboenv_perception.construction.common import new_run
from roboenv_perception.io import write_json
from roboenv_perception.masks import save_mask


def create_scene(output: Path, count=12):
    width, height, focal = 640, 480, 540.0
    rng = np.random.default_rng(20261022)
    textures = []
    for _ in range(6):
        texture = rng.integers(25, 235, (192, 192, 3), dtype=np.uint8)
        textures.append(
            np.asarray(Image.fromarray(texture).resize((1536, 1536), Image.Resampling.BILINEAR))
        )
    y, x = np.mgrid[:height, :width]
    rays_camera = np.stack(
        [(x - width / 2) / focal, (y - height / 2) / focal, np.ones_like(x)], axis=-1
    )
    frames, truth = [], []
    with new_run(output, "synthetic_3d_background_fixture", {}):
        for index in range(count):
            t = index / (count - 1)
            centre = np.array(
                [-0.65 + 1.3 * t, -2.1 + 0.15 * np.sin(t * np.pi), 1.25 + 0.08 * np.cos(t * np.pi)]
            )
            forward = np.array([0, 0.65, 0.5]) - centre
            forward /= np.linalg.norm(forward)
            right = np.cross(forward, [0, 0, 1])
            right /= np.linalg.norm(right)
            down = np.cross(forward, right)
            rotation = np.stack([right, down, forward])
            rays = rays_camera @ rotation
            distance = np.full((height, width), np.inf)
            rgb = np.zeros((height, width, 3), np.uint8)
            excluded = np.zeros((height, width), bool)
            # Floor and walls, all expressed in one world coordinate system.
            for axis, plane, uv_axes, texture_index in [
                (2, 0.0, (0, 1), 0),
                (1, 2.4, (0, 2), 1),
                (0, -2.4, (1, 2), 2),
                (0, 2.4, (1, 2), 3),
            ]:
                denominator = rays[..., axis]
                safe = np.where(np.abs(denominator) > 1e-8, denominator, 1e-8)
                hit_distance = (plane - centre[axis]) / safe
                visible = (hit_distance > 0) & (hit_distance < distance)
                hit = centre + rays * hit_distance[..., None]
                u = np.floor((hit[..., uv_axes[0]] + 8) * 150).astype(int) % 1536
                v = np.floor((hit[..., uv_axes[1]] + 8) * 150).astype(int) % 1536
                rgb[visible] = textures[texture_index][v[visible], u[visible]]
                distance[visible] = hit_distance[visible]
            # One excluded independent foreground object plus a static pedestal.
            for box_index, (low, high) in enumerate(
                [
                    ([-0.25, 0.25, 0], [0.25, 0.75, 0.85]),
                    ([-1.3, 0.6, 0], [-0.65, 1.25, 0.5]),
                ]
            ):
                safe = np.where(np.abs(rays) > 1e-8, rays, 1e-8)
                a, b = (np.array(low) - centre) / safe, (np.array(high) - centre) / safe
                near, far = np.minimum(a, b), np.maximum(a, b)
                hit_distance = near.max(axis=-1)
                visible = (
                    (hit_distance > 0)
                    & (hit_distance <= far.min(axis=-1))
                    & (hit_distance < distance)
                )
                hit = centre + rays * hit_distance[..., None]
                # Separate texture coordinates by face orientation to avoid repeating identical patches.
                face = near.argmax(axis=-1)
                u = np.floor((hit[..., 0] + hit[..., 1] + 8) * 210).astype(int) % 1536
                v = np.floor((hit[..., 2] + face * 2 + 8) * 210).astype(int) % 1536
                rgb[visible] = textures[4 + box_index][v[visible], u[visible]]
                distance[visible] = hit_distance[visible]
                excluded[visible] = box_index == 0
            base = f"frames/{index:06d}"
            (output / base).mkdir(parents=True)
            Image.fromarray(rgb).save(output / base / "image.png")
            observed = np.isfinite(distance) & ~excluded
            save_mask(output / base / "observed.png", observed)
            save_mask(output / base / "excluded.png", excluded)
            save_mask(output / base / "sfm.png", np.isfinite(distance))
            frames.append(
                {
                    "frame_id": f"synthetic-{index:03d}",
                    "image_name": f"{index:06d}.png",
                    "timestamp_seconds": index / 6,
                    "size": [width, height],
                    "image_ref": f"{base}/image.png",
                    "observed_mask_ref": f"{base}/observed.png",
                    "excluded_mask_ref": f"{base}/excluded.png",
                    "sfm_mask_ref": f"{base}/sfm.png",
                    "observed_pixels": int(observed.sum()),
                    "split": "holdout" if index % 6 == 0 else "train",
                }
            )
            world = np.eye(4)
            world[:3, :3], world[:3, 3] = rotation, -rotation @ centre
            truth.append(
                {
                    "frame_id": frames[-1]["frame_id"],
                    "world_to_camera": world.tolist(),
                    "intrinsics": [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
                }
            )
        write_json(
            output / "background.json",
            {
                "scene_id": "synthetic-textured-room",
                "frames": frames,
                "fixture": "synthetic_3d",
                "holdout_policy": "appearance holdout; all images allowed for SfM",
                "sources": {},
                "camera_status": "synthetic_truth_separate",
                "scale_status": "unknown",
            },
        )
        write_json(output / "synthetic_truth.json", {"frames": truth, "not_used_by_colmap": True})
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(create_scene(args.output))
