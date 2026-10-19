"""Deterministic synthetic video, independent truth masks and replayed model output."""

from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image

from .contracts import SCHEMA_VERSION, VideoConfig
from .io import sha256, write_json
from .masks import save_mask
from .video_io import decode_video, require_av


def create_video_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=False)
    av = require_av()
    width, height, count, fps = 160, 112, 6, 6
    source = root / "scene.mkv"
    masks_per_frame = []
    with av.open(str(source), "w") as container:
        stream = container.add_stream("ffv1", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "bgr0"
        for index in range(count):
            table = np.zeros((height, width), bool)
            table[40:, :] = True
            red, blue = np.zeros_like(table), np.zeros_like(table)
            if index != 2:  # Explicit full occlusion; ID should survive reappearance.
                red[48:78, 18 + index * 6 : 38 + index * 6] = True
            blue[60:90, 112 - index * 3 : 134 - index * 3] = True
            table &= ~(red | blue)
            image = np.full((height, width, 3), (225, 230, 235), dtype=np.uint8)
            image[table], image[red], image[blue] = (165, 130, 95), (215, 50, 50), (45, 100, 215)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, fps)
            for packet in stream.encode(frame):
                container.mux(packet)
            masks_per_frame.append([table, red, blue])
        for packet in stream.encode():
            container.mux(packet)
    decoded = decode_video(source, root / "decoded", VideoConfig(sample_fps=fps, max_frames=20))
    if len(decoded["frames"]) != count:
        raise ValueError("fixture PTS sampling unexpectedly dropped frames")
    truth_dir = root / "truth"
    truth_dir.mkdir()
    replay_frames, truth_frames = [], []
    for index, (frame, masks) in enumerate(zip(decoded["frames"], masks_per_frame, strict=True)):
        rows, objects = [], []
        image_ref = f"images/{index:06d}.png"
        (truth_dir / "images").mkdir(exist_ok=True)
        with Image.open(root / "decoded" / frame["image_ref"]) as image:
            image.save(truth_dir / image_ref)
        for object_index, (mask, category, gt_id, native, score) in enumerate(
            zip(
                masks,
                ["table", "cup", "cup"],
                ["table", "red-cup", "blue-cup"],
                ["c001:1", "c002:7", "c002:12"],
                [0.99, 0.98, 0.97],
                strict=True,
            )
        ):
            ref = f"masks/{index:06d}-{object_index}.png"
            if mask.any():
                save_mask(truth_dir / ref, mask)
                rows.append(
                    {
                        "candidate_ids": ["c001" if object_index == 0 else "c002"],
                        "track_keys": [native],
                        "score": score,
                        "mask_ref": f"truth/{ref}",
                    }
                )
            objects.append(
                {
                    "gt_id": gt_id,
                    "category": category,
                    "visible": bool(mask.any()),
                    "mask": ref if mask.any() else None,
                    "critical": object_index == 1,
                    "exclude_from_background": object_index != 0,
                }
            )
        replay_frames.append({"image_sha256": frame["image_sha256"], "detections": rows})
        truth_frames.append(
            {
                "frame_id": frame["frame_id"],
                "image": image_ref,
                "image_sha256": sha256(truth_dir / image_ref),
                "objects": objects,
            }
        )
    write_json(root / "video_masks.json", {"fixture": True, "frames": replay_frames})
    write_json(
        root / "inventory.json",
        {
            "scene_description": "Synthetic moving cups on a table.",
            "objects": [
                {
                    "candidate_id": "c001",
                    "category": "table",
                    "prompt": "tabletop",
                    "expected_count": 1,
                },
                {"candidate_id": "c002", "category": "cup", "prompt": "cup", "expected_count": 2},
            ],
        },
    )
    write_json(
        root / "labels.json",
        {
            "instances": [
                {
                    "instance_id": "i0001",
                    "category": "table",
                    "task_role": "support",
                    "visual_route": "gs_background",
                    "physical_role": "support",
                },
                {
                    "instance_id": "i0002",
                    "category": "cup",
                    "attributes": {"color": "red"},
                    "task_role": "manipulation_candidate",
                    "visual_route": "mesh_candidate",
                    "physical_role": "rigid_body_candidate",
                },
                {
                    "instance_id": "i0003",
                    "category": "cup",
                    "attributes": {"color": "blue"},
                    "task_role": "manipulation_candidate",
                    "visual_route": "mesh_candidate",
                    "physical_role": "rigid_body_candidate",
                },
            ]
        },
    )
    write_json(truth_dir / "truth.json", {"schema_version": SCHEMA_VERSION, "frames": truth_frames})
    write_json(
        root / "dataset.json",
        {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": "synthetic-video-v1",
            "annotation_source": "synthetic_fixture",
            "samples": [{"sample_id": "clip", "kind": "video", "truth": "truth/truth.json"}],
        },
    )
    (root / "replay.toml").write_text(
        '[vlm]\nbackend="replay"\nreplay_dir="."\n[sam]\nbackend="replay"\n[video]\nsample_fps=6\nmax_frames=20\nreplay_manifest="video_masks.json"\npreview_fps=6\n',
        encoding="utf-8",
    )
    return source, root / "replay.toml", root / "dataset.json"
