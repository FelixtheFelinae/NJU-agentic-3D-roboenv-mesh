"""Prepare real observations and explicit exclusions; never train on blacked-out holes."""

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..io import read_json, relative_file, write_json
from ..masks import save_mask
from .common import copy_ref, new_run, perception_frames, read_mask, source_identity, verify_run
from .contracts import VERSION, BackgroundConfig


def dilate(mask: np.ndarray, pixels: int):
    if not pixels:
        return mask.copy()
    return (
        np.asarray(
            Image.fromarray(mask.astype(np.uint8) * 255).filter(
                ImageFilter.MaxFilter(2 * pixels + 1)
            )
        )
        > 0
    )


def prepare_background(
    bundle: Path,
    output: Path,
    config: BackgroundConfig,
    designs: Path | None = None,
    exclusions: Path | None = None,
):
    scene = verify_run(bundle, "scene.json")
    frames = perception_frames(bundle, scene)
    asset_ids, rejected_ids = set(), set()
    sources = {"perception": source_identity(bundle)}
    if designs:
        designed = verify_run(designs, "designs.json")
        if designed["scene_id"] != scene["scene_id"]:
            raise ValueError("design and perception scene IDs differ")
        sources["designs"] = source_identity(designs)
        for row in designed["objects"]:
            # The original ID is run-scoped: stale/different-run designs cannot silently match.
            if row["status"] == "design_ready":
                asset_ids.add(row["object_id"])
            elif row["status"] != "routed" or row.get("route") in {"unresolved", "exclude"}:
                rejected_ids.add(row["object_id"])
    extras = read_json(exclusions) if exclusions else {}
    if not isinstance(extras, dict) or set(extras) - {f["frame_id"] for f in frames}:
        raise ValueError("exclusions must map known frame IDs to binary mask paths")
    with new_run(output, "background_preparation", sources):
        write_json(output / "config.json", config.model_dump(mode="json"))
        records = []
        seen_ids = set()
        for i, frame in enumerate(frames):
            fid = frame["frame_id"]
            base = f"frames/{i:06d}"
            with Image.open(relative_file(bundle, frame["image_ref"])) as image:
                size = image.size
            masks = read_json(relative_file(bundle, frame["background_ref"]))["masks"]
            background = read_mask(relative_file(bundle, masks["background"]), size)
            unknown = read_mask(relative_file(bundle, masks["unknown"]), size)
            transient = read_mask(relative_file(bundle, masks["transient"]), size)
            independent = read_mask(relative_file(bundle, masks["mesh"]), size)
            for obj in read_json(relative_file(bundle, frame["objects_ref"])):
                seen_ids.add(obj["object_id"])
                if obj["object_id"] in asset_ids | rejected_ids:
                    independent |= read_mask(relative_file(bundle, obj["mask_ref"]), size)
            extra = np.zeros((size[1], size[0]), dtype=bool)
            if fid in extras:
                path = Path(extras[fid])
                path = path if path.is_absolute() else exclusions.parent / path
                extra = read_mask(path, size)
                save_mask(output / base / "manual_exclusion.png", extra)
            excluded = dilate(independent | transient | extra, config.boundary_margin_pixels)
            observed = background & ~excluded & ~unknown
            # Static objects can assist pose recovery, but never seed background Gaussians.
            feature_valid = (
                np.ones((size[1], size[0]), dtype=bool)
                if config.sfm_mask_mode == "all"
                else ~dilate(transient | extra, config.boundary_margin_pixels) & ~unknown
            )
            image_ref = copy_ref(bundle, frame["image_ref"], output, f"{base}/image.png")
            save_mask(output / base / "observed.png", observed)
            save_mask(output / base / "excluded.png", excluded)
            save_mask(output / base / "sfm_valid.png", feature_valid)
            split = "holdout" if len(frames) >= 3 and i % config.holdout_every == 0 else "train"
            records.append(
                {
                    "frame_id": fid,
                    "image_name": f"{i:06d}.png",
                    "timestamp_seconds": frame.get("timestamp_seconds", 0.0),
                    "size": list(size),
                    "image_ref": image_ref,
                    "observed_mask_ref": f"{base}/observed.png",
                    "excluded_mask_ref": f"{base}/excluded.png",
                    "sfm_mask_ref": f"{base}/sfm_valid.png",
                    "split": split,
                    "observed_pixels": int(observed.sum()),
                }
            )
        if (asset_ids | rejected_ids) - seen_ids:
            raise ValueError("design IDs do not belong to this perception run")
        write_json(
            output / "background.json",
            {
                "schema_version": VERSION,
                "scene_id": scene["scene_id"],
                "frames": records,
                "sources": sources,
                "camera_status": "not_estimated",
                "scale_status": "unknown",
                "sfm_mask_mode": config.sfm_mask_mode,
                "mask_coordinates": "original normalized perception frame; not undistorted",
                "holdout_policy": "appearance holdout; cameras may use all frames for SfM",
                "completion": "not_run",
                "gaussians": "not_trained",
                "collision_geometry": None,
                "warnings": [
                    "Unlabelled regions stay unknown.",
                    "Masks do not guarantee removal of shadows/reflections; supply exclusions.",
                    "All exported assets must be excluded from background observations.",
                    *(["Unmasked SfM assumes all visible features belong to a static scene."]
                      if config.sfm_mask_mode == "all" else []),
                ],
            },
        )
    return output
