"""Scene-specific conservative floor masks, then the existing COLMAP/3DGS path."""

import argparse
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from roboenv_perception.construction.common import new_run
from roboenv_perception.construction.contracts import BackgroundConfig
from roboenv_perception.construction.reconstruction import (
    estimate_background_cameras,
    train_background,
)
from roboenv_perception.io import sha256, write_json

# Reviewed broad vehicle+shadow rectangles in 320x180 contact-sheet coordinates.
# Floor-only supervision also excludes the top 25% (room boundary/red tape).
BOXES = [
    (55, 20, 260, 168),
    (65, 20, 270, 172),
    (85, 20, 250, 165),
    (110, 20, 250, 157),
    (115, 15, 255, 155),
    (100, 20, 267, 165),
    (100, 10, 278, 165),
    (110, 5, 288, 170),
    (105, 5, 290, 166),
    (145, 10, 304, 165),
    (110, 0, 298, 163),
    (95, 0, 300, 168),
    (100, 10, 290, 174),
    (105, 20, 242, 170),
    (70, 35, 245, 180),
    (60, 35, 255, 180),
    (70, 65, 305, 180),
    (100, 70, 315, 180),
    (70, 30, 295, 174),
    (48, 10, 306, 153),
    (28, 5, 292, 150),
    (30, 50, 249, 180),
    (20, 50, 280, 180),
    (45, 45, 280, 180),
    (0, 35, 250, 175),
    (35, 30, 268, 180),
    (20, 15, 270, 175),
    (15, 10, 295, 175),
    (0, 5, 280, 175),
    (0, 5, 310, 170),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--stage", choices=["prepare", "cameras", "train"], required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    config = BackgroundConfig(
        colmap="/home/felixz/.local/share/roboenv/colmap/bin/colmap",
        matching="exhaustive",
        use_gpu_features=False,
        gaussian_repo="/home/felixz/.local/share/roboenv/gaussian-splatting",
        gaussian_python="/home/felixz/.local/share/roboenv/gaussian-env/bin/python",
        iterations=3000,
        densify_until_iter=2500,
        checkpoint_iterations=[1000],
        max_gaussians=100000,
        timeout_seconds=7200,
    )
    if a.stage == "prepare":
        root = a.output / "background"
        with new_run(root, "cart_floor_manual_mask_preparation", {"video_sha256": sha256(a.video)}):
            (root / "frames").mkdir()
            frames = []
            for i, box in enumerate(BOXES):
                name = f"{i:06d}.png"
                path = root / "frames" / name
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        str(i * 0.5),
                        "-i",
                        str(a.video),
                        "-vf",
                        "scale=640:360",
                        "-frames:v",
                        "1",
                        str(path),
                    ],
                    check=True,
                )
                mask = Image.new("L", (640, 360), 255)
                d = ImageDraw.Draw(mask)
                d.rectangle((0, 0, 639, 89), fill=0)
                d.rectangle(tuple(int(v * 2) for v in box), fill=0)
                mask.save(root / "frames" / f"{i:06d}_observed.png")
                Image.new("L", (640, 360), 255).save(root / "frames" / f"{i:06d}_sfm.png")
                frames.append(
                    dict(
                        frame_id=f"f{i:06d}",
                        image_name=name,
                        timestamp_seconds=i * 0.5,
                        size=[640, 360],
                        image_ref=f"frames/{name}",
                        observed_mask_ref=f"frames/{i:06d}_observed.png",
                        sfm_mask_ref=f"frames/{i:06d}_sfm.png",
                        split="holdout" if i % 7 == 0 else "train",
                    )
                )
            write_json(
                root / "background.json",
                dict(
                    scene_id="cart_floor",
                    frames=frames,
                    fixture="real_video_manually_masked_floor",
                    holdout_policy="appearance holdout; all frames may participate in SfM",
                    scale_status="unknown",
                    mask_provenance="manually reviewed conservative rectangles + top quarter exclusion",
                    sfm_assumption="cart is static during capture; object features allowed for camera poses, excluded from floor supervision",
                ),
            )
    elif a.stage == "cameras":
        estimate_background_cameras(a.output / "background", a.output / "cameras", config)
    else:
        train_background(a.output / "cameras", a.output / "gaussians", config)


if __name__ == "__main__":
    main()
