"""Fuse measured planar tabletop patches into one atlas before optional appearance completion."""

from pathlib import Path

import numpy as np
from PIL import Image

from ..io import read_json, relative_file, write_json
from ..masks import save_mask
from .common import cv2_module, new_run, read_mask, source_identity, verify_run
from .contracts import VERSION, TableSpec


def complete_table(background: Path, spec_path: Path, output: Path):
    cv2 = cv2_module()
    data = verify_run(background, "background.json")
    spec = TableSpec.model_validate(read_json(spec_path))
    frame_by_id = {f["frame_id"]: f for f in data["frames"]}
    if any(v.frame_id not in frame_by_id for v in spec.views):
        raise ValueError("table specification contains unknown frame IDs")
    if set(spec.extra_exclusions) - set(frame_by_id):
        raise ValueError("extra exclusions contain unknown frame IDs")
    width = max(2, round(spec.width_m * spec.pixels_per_metre))
    height = max(2, round(spec.depth_m * spec.pixels_per_metre))
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32
    )
    total = np.zeros((height, width, 3), np.float32)
    count = np.zeros((height, width), np.float32)
    transforms = {}
    with new_run(output, "table_completion", source_identity(background)):
        write_json(output / "table_spec.json", spec.model_dump(mode="json"))
        # Holdout RGB never enters the atlas or inpainting inputs.
        for view in spec.views:
            frame = frame_by_id[view.frame_id]
            corners = np.asarray(view.corners_xy, np.float32)
            if not cv2.isContourConvex(corners) or abs(cv2.contourArea(corners)) < 4:
                raise ValueError("corners must describe a nondegenerate convex planar patch")
            transform = cv2.getPerspectiveTransform(corners, target)
            if not np.isfinite(transform).all() or abs(np.linalg.det(transform)) < 1e-12:
                raise ValueError("degenerate tabletop homography")
            transforms[view.frame_id] = transform
            if frame["split"] != "train":
                continue
            with Image.open(relative_file(background, frame["image_ref"])) as raw:
                image = np.asarray(raw.convert("RGB"), dtype=np.float32)
            valid = read_mask(relative_file(background, frame["observed_mask_ref"]), frame["size"])
            if view.frame_id in spec.extra_exclusions:
                p = Path(spec.extra_exclusions[view.frame_id])
                p = p if p.is_absolute() else spec_path.parent / p
                exclusion = read_mask(p, frame["size"])
                save_mask(output / "extra_exclusions" / f"{frame['image_name']}", exclusion)
                valid &= ~exclusion
            # Premultiplication avoids interpolating foreground colour across mask boundaries.
            weight = cv2.warpPerspective(valid.astype(np.float32), transform, (width, height))
            warped = cv2.warpPerspective(image * valid[..., None], transform, (width, height))
            trusted = weight >= 0.999
            total[trusted] += warped[trusted] / weight[trusted, None]
            count[trusted] += 1
        observed = count > 0
        if not observed.any():
            raise ValueError(
                "no training-view table observations; check splits and corner annotations"
            )
        atlas = np.clip(total / np.maximum(count[..., None], 1), 0, 255).astype(np.uint8)
        provenance = observed.astype(
            np.uint8
        )  # 0 unknown, 1 real training observation, 2 synthesized
        holes = ~observed
        fill_fraction = float(holes.mean())
        if spec.fill == "telea":
            if fill_fraction > spec.max_fill_fraction:
                raise ValueError(
                    "unobserved tabletop exceeds max_fill_fraction; capture more views"
                )
            if holes.any():
                # Appearance-only baseline; not semantic or metric reconstruction evidence.
                atlas = cv2.inpaint(atlas, holes.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
                provenance[holes] = 2
        Image.fromarray(atlas).save(output / "atlas.png")
        Image.fromarray(provenance).save(output / "provenance.png")
        save_mask(output / "observed.png", observed)
        save_mask(output / "synthesized.png", provenance == 2)
        rows = []
        for view in spec.views:
            frame = frame_by_id[view.frame_id]
            size = tuple(frame["size"])
            inverse = np.linalg.inv(transforms[view.frame_id])
            projected = cv2.warpPerspective(atlas, inverse, size)
            projected_provenance = cv2.warpPerspective(
                provenance, inverse, size, flags=cv2.INTER_NEAREST
            )
            observed_frame = read_mask(relative_file(background, frame["observed_mask_ref"]), size)
            if view.frame_id in spec.extra_exclusions:
                p = Path(spec.extra_exclusions[view.frame_id])
                p = p if p.is_absolute() else spec_path.parent / p
                observed_frame &= ~read_mask(p, size)
            # Restore only missing pixels. Original reliable observations are immutable.
            # A nearest-neighbour label alone can admit bilinear RGB mixed with
            # unknown atlas texels. Require full support for the RGB footprint.
            support = cv2.warpPerspective((provenance > 0).astype(np.float32), inverse, size)
            region = (projected_provenance > 0) & (support >= 0.999) & ~observed_frame
            with Image.open(relative_file(background, frame["image_ref"])) as raw:
                completed = np.asarray(raw.convert("RGB")).copy()
            completed[region] = projected[region]
            base = f"frames/{frame['image_name'].removesuffix('.png')}"
            (output / base).mkdir(parents=True, exist_ok=True)
            Image.fromarray(completed).save(output / base / "completed.png")
            # Even real texels reprojected to an unobserved view receive weak supervision.
            save_mask(output / base / "filled.png", region)
            rows.append(
                {
                    "frame_id": view.frame_id,
                    "image_ref": f"{base}/completed.png",
                    "filled_mask_ref": f"{base}/filled.png",
                    "image_to_atlas": transforms[view.frame_id].tolist(),
                }
            )
        write_json(
            output / "completion.json",
            {
                "schema_version": VERSION,
                "background_source": source_identity(background),
                "frames": rows,
                "atlas_ref": "atlas.png",
                "provenance_ref": "provenance.png",
                "provenance_values": {"0": "unknown", "1": "observed_train", "2": "synthetic"},
                "width_m": spec.width_m,
                "depth_m": spec.depth_m,
                "fill_method": spec.fill,
                "unobserved_fraction_before_fill": fill_fraction,
                "holdout_pixels_used_for_fusion": False,
                "geometry": "manually annotated planar patch; not camera/metric-scene calibration",
                "physics_geometry": None,
                "independent_accuracy": None,
            },
        )
    return output
