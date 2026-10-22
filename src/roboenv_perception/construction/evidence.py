"""Select time-separated, visible object evidence without claiming measured view diversity."""

from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..io import read_json, relative_file, write_json
from ..masks import make_crops
from .common import copy_ref, new_run, perception_frames, read_mask, source_identity, verify_run
from .contracts import VERSION, EvidenceConfig


def observation_quality(image: Image.Image, mask: np.ndarray):
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    if mask.sum() == 0:
        return {"visible_pixels": 0, "sharpness": 0.0, "edge_touching": True}
    # Texture-sensitive ranking proxy, not a calibrated blur/segmentation metric.
    laplace = (
        gray[1:-1, :-2] + gray[1:-1, 2:] + gray[:-2, 1:-1] + gray[2:, 1:-1] - 4 * gray[1:-1, 1:-1]
    )
    interior = mask[1:-1, 1:-1]
    sharpness = float(np.var(laplace[interior])) if interior.any() else 0.0
    edge = bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())
    return {"visible_pixels": int(mask.sum()), "sharpness": sharpness, "edge_touching": edge}


def select_views(observations: list[dict], config: EvidenceConfig):
    eligible = [
        o for o in observations if o["quality"]["visible_pixels"] >= config.min_visible_pixels
    ]

    def rank(obs):
        q = obs["quality"]
        return (
            not q["edge_touching"],
            np.log1p(q["sharpness"]) + np.log1p(q["visible_pixels"]),
            obs["frame_id"],
        )

    selected = []
    for obs in sorted(eligible, key=rank, reverse=True):
        if all(
            abs(obs["timestamp_seconds"] - s["timestamp_seconds"]) >= config.min_time_gap_seconds
            for s in selected
        ):
            selected.append(obs)
        if len(selected) >= config.max_views:
            break
    return sorted(selected, key=lambda o: o["timestamp_seconds"])


def prepare_objects(bundle: Path, output: Path, config: EvidenceConfig):
    scene = verify_run(bundle, "scene.json")
    frames = perception_frames(bundle, scene)
    groups = defaultdict(list)
    with new_run(output, "object_evidence", source_identity(bundle)):
        write_json(output / "config.json", config.model_dump(mode="json"))
        for frame in frames:
            with Image.open(relative_file(bundle, frame["image_ref"])) as raw:
                image = raw.convert("RGB")
            objects = read_json(relative_file(bundle, frame["objects_ref"]))
            by_instance = {o["instance_id"]: o["object_id"] for o in objects}
            for obj in objects:
                obj = {**obj, "parent_object_id": by_instance.get(obj.get("parent_instance_id"))}
                mask = read_mask(relative_file(bundle, obj["mask_ref"]), image.size)
                groups[obj["object_id"]].append(
                    {
                        "frame_id": frame["frame_id"],
                        "timestamp_seconds": frame.get("timestamp_seconds", 0.0),
                        "image_ref": frame["image_ref"],
                        "observation": obj,
                        "quality": observation_quality(image, mask),
                    }
                )
        entries = []
        for i, (object_id, observations) in enumerate(sorted(groups.items())):
            asset_id = f"asset-{i + 1:04d}"
            base = f"objects/{asset_id}"
            selected = select_views(observations, config)
            identity_conflict = any(
                o["observation"].get("identity_status") == "ambiguous" for o in observations
            )
            known = [
                o["observation"]
                for o in observations
                if o["observation"].get("category", "unknown") != "unknown"
            ]
            categories = {o["category"] for o in known}
            representative = known[0] if known else observations[0]["observation"]
            refs = []
            board = Image.new("RGB", (640, max(1, len(selected)) * 220), "white")
            draw = ImageDraw.Draw(board)
            for j, obs in enumerate(selected):
                ref_id = f"view-{j + 1:03d}"
                dst = f"{base}/views/{ref_id}"
                obj = obs["observation"]
                copy_ref(bundle, obs["image_ref"], output, f"{dst}/frame.png")
                copy_ref(bundle, obj["mask_ref"], output, f"{dst}/mask.png")
                with Image.open(relative_file(bundle, obs["image_ref"])) as raw:
                    image = raw.convert("RGB")
                mask = read_mask(relative_file(bundle, obj["mask_ref"]), image.size)
                rgba, context, tight, context_box = make_crops(image, mask, config.crop_padding)
                rgba.save(output / dst / "rgba.png")
                context.save(output / dst / "context.png")
                # Keep both surrounding context and an isolated view in the reference board.
                isolated = Image.new("RGBA", rgba.size, "white")
                isolated.alpha_composite(rgba)
                for col, tile in enumerate([context, isolated.convert("RGB")]):
                    tile = tile.copy()
                    tile.thumbnail((310, 185))
                    board.paste(tile, (col * 320, j * 220 + 25))
                draw.text(
                    (5, j * 220 + 5), f"{ref_id}  {obs['timestamp_seconds']:.3f}s", fill="black"
                )
                refs.append(
                    {
                        "ref_id": ref_id,
                        "frame_id": obs["frame_id"],
                        "timestamp_seconds": obs["timestamp_seconds"],
                        "quality": obs["quality"],
                        "frame_ref": f"{dst}/frame.png",
                        "mask_ref": f"{dst}/mask.png",
                        "rgba_ref": f"{dst}/rgba.png",
                        "context_ref": f"{dst}/context.png",
                        "bbox_xyxy": tight,
                        "context_box_xyxy": context_box,
                        "sam_score": obj.get("sam_score"),
                        "occluded": obj.get("occluded", False),
                        "mask_semantics": "visible prediction; not completed geometry",
                    }
                )
            board.save(output / base / "references.png") if refs else None
            status = (
                "blocked_identity"
                if identity_conflict or len(categories) > 1
                else "insufficient_evidence"
                if not refs
                else "ready_for_design"
            )
            record = {
                "schema_version": VERSION,
                "asset_id": asset_id,
                "object_id": object_id,
                "category": representative.get("category", "unknown"),
                "visual_route": representative.get("visual_route", "unresolved"),
                "physical_role": representative.get("physical_role", "unknown"),
                "task_role": representative.get("task_role", "unknown"),
                "identity_status": "ambiguous"
                if identity_conflict
                else representative.get("identity_status", "unverified"),
                "status": status,
                "views": refs,
                "parent_object_ids": sorted(
                    {
                        o["observation"]["parent_object_id"]
                        for o in observations
                        if o["observation"]["parent_object_id"]
                    }
                ),
                "instance_part_boundary": "parented perception instances are evidence for the parent, not automatically separate assets",
                "observation_count": len(observations),
                "known_categories": sorted(categories),
                "selection": "visibility/texture rank and timestamp separation; camera diversity unmeasured",
                "board_ref": f"{base}/references.png" if refs else None,
                "camera_and_scale": "not_estimated",
                "ground_truth_accuracy": None,
            }
            write_json(output / base / "evidence.json", record)
            entries.append(
                {
                    "asset_id": asset_id,
                    "object_id": object_id,
                    "status": status,
                    "evidence_ref": f"{base}/evidence.json",
                }
            )
        write_json(
            output / "objects.json",
            {
                "schema_version": VERSION,
                "scene_id": scene["scene_id"],
                "objects": entries,
                "perception_source": source_identity(bundle),
            },
        )
    return output
