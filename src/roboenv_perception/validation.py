from pathlib import Path

import numpy as np
from PIL import Image

from .io import read_json, relative_file, sha256
from .masks import bbox


def validate_bundle(root: Path) -> dict:
    """Check persisted final artifacts, independent of in-memory pipeline state."""
    errors = []
    try:
        run = read_json(root / "run.json")
        if run["status"] != "completed":
            errors.append("run is not completed")
        if sha256(root / "artifacts.json") != run.get("artifact_index_sha256"):
            errors.append("artifact index hash mismatch")
        index = read_json(root / "artifacts.json")
        for ref, expected in index.items():
            path = relative_file(root, ref)
            if not path.is_file() or sha256(path) != expected:
                errors.append(f"missing or changed artifact: {ref}")
        scene = read_json(root / "scene.json")
        if scene.get("kind") in {"video", "image_set"}:
            from .video_validation import validate_video_contents

            validate_video_contents(root, scene, errors)
            if scene.get("kind") == "image_set":
                manifest = read_json(relative_file(root, scene["source_manifest_ref"]))
                if len(manifest.get("images", [])) != len(scene["frames"]):
                    errors.append("image-set source manifest does not match frames")
                for item, frame in zip(manifest.get("images", []), scene["frames"]):
                    if (item.get("source_ref") != frame.get("source_ref")
                            or item.get("sha256") != frame.get("source_sha256")
                            or sha256(relative_file(root, item["source_ref"])) != item["sha256"]):
                        errors.append("image-set source changed or is mismatched")
            quality = read_json(relative_file(root, scene["quality_ref"]))
            return {
                "valid": not errors,
                "errors": errors,
                "checks_status": quality["checks_status"],
                "ground_truth_evaluated": False,
                "validated_artifact_count": len(index),
            }
        frame = scene["frame"]
        with Image.open(relative_file(root, frame["image_ref"])) as image:
            width, height = image.size
        routes = read_json(root / "background/index.json")
        masks = []
        for name, ref in routes["masks"].items():
            with Image.open(relative_file(root, ref)) as image:
                array = np.array(image)
            if array.shape != (height, width) or not np.isin(array, [0, 255]).all():
                errors.append(f"invalid routing mask: {name}")
            masks.append(array > 0)
        if len(masks) != 4 or not np.all(sum(mask.astype(int) for mask in masks) == 1):
            errors.append("routing masks do not form a disjoint exhaustive partition")
        objects = read_json(relative_file(root, scene["objects_ref"]))
        ids = [obj["object_id"] for obj in objects]
        if len(ids) != len(set(ids)):
            errors.append("object IDs are not unique")
        for obj in objects:
            with Image.open(relative_file(root, obj["mask_ref"])) as image:
                mask = np.array(image)
            if mask.shape != (height, width) or not np.isin(mask, [0, 255]).all():
                errors.append(f"invalid object mask: {obj['instance_id']}")
                continue
            if not mask.any() or bbox(mask > 0) != obj["bbox_xyxy"]:
                errors.append(f"mask/bbox mismatch: {obj['instance_id']}")
                continue
            x0, y0, x1, y1 = obj["bbox_xyxy"]
            with Image.open(relative_file(root, obj["rgba_ref"])) as image:
                if image.mode != "RGBA" or image.size != (x1 - x0, y1 - y0):
                    errors.append(f"invalid RGBA crop: {obj['instance_id']}")
                elif not np.array_equal(np.asarray(image)[:, :, 3], mask[y0:y1, x0:x1]):
                    errors.append(f"RGBA alpha mismatch: {obj['instance_id']}")
            relative_file(root, obj["context_ref"])
        quality = read_json(relative_file(root, scene["quality_ref"]))
        return {
            "valid": not errors,
            "errors": errors,
            "checks_status": quality["checks_status"],
            "ground_truth_evaluated": False,
            "validated_artifact_count": len(index),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"{type(error).__name__}: {error}")
        return {"valid": False, "errors": errors, "ground_truth_evaluated": False}
