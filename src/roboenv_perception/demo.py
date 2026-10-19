"""Explicit synthetic fixture: validates plumbing, never model accuracy."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .io import sha256, write_json


def create_fixture(root: Path):
    root.mkdir(parents=True, exist_ok=False)
    width, height = 640, 400
    image = Image.new("RGB", (width, height), (224, 230, 235))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 130, 639, 399), fill=(175, 139, 98))
    draw.rounded_rectangle((120, 155, 205, 275), radius=12, fill=(205, 55, 55))
    draw.rounded_rectangle((260, 170, 345, 290), radius=12, fill=(55, 110, 205))
    draw.rectangle((440, 205, 540, 300), fill=(220, 175, 60))
    draw.text((16, 18), "SYNTHETIC FIXTURE / NO MODEL INFERENCE", fill=(35, 45, 55))
    image.save(root / "scene.png")
    masks = []
    for rectangle in [
        (0, 130, 640, 400),
        (120, 155, 206, 276),
        (260, 170, 346, 291),
        (440, 205, 541, 301),
    ]:
        mask = np.zeros((height, width), dtype=bool)
        x0, y0, x1, y1 = rectangle
        mask[y0:y1, x0:x1] = True
        masks.append(mask)
    # Remove known foreground pixels from the observed support mask.
    masks[0] &= ~(masks[1] | masks[2] | masks[3])
    detections = []
    for index, (mask, candidate, score) in enumerate(
        zip(masks, ["c001", "c002", "c002", "c003"], [0.99, 0.98, 0.97, 0.96], strict=True)
    ):
        name = f"mask-{index}.png"
        Image.fromarray(mask.astype(np.uint8) * 255).save(root / name)
        detections.append({"candidate_id": candidate, "mask": name, "score": score})
    write_json(
        root / "masks.json",
        {"fixture": True, "image_sha256": sha256(root / "scene.png"), "detections": detections},
    )
    write_json(
        root / "inventory.json",
        {
            "scene_description": "Synthetic tabletop, two cups and one box.",
            "objects": [
                {
                    "candidate_id": "c001",
                    "category": "table",
                    "prompt": "tabletop",
                    "expected_count": 1,
                },
                {"candidate_id": "c002", "category": "cup", "prompt": "cup", "expected_count": 2},
                {"candidate_id": "c003", "category": "box", "prompt": "box", "expected_count": 1},
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
                {
                    "instance_id": "i0004",
                    "category": "box",
                    "task_role": "container",
                    "visual_route": "mesh_candidate",
                    "physical_role": "rigid_body_candidate",
                },
            ]
        },
    )
    (root / "replay.toml").write_text(
        '[vlm]\nbackend = "replay"\nreplay_dir = "."\n\n[sam]\nbackend = "replay"\nreplay_manifest = "masks.json"\n',
        encoding="utf-8",
    )
    return root / "scene.png", root / "replay.toml"
