"""Turn independently observed photographs into a hash-bound perception bundle.

An image set has an order for deterministic processing, but no inferred elapsed time,
camera trajectory, or observed object motion. Each image goes through the existing
single-image perception pipeline; cross-image identity is only a hypothesis.
"""

import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping

from ..construction.common import new_run, verify_run
from ..contracts import SCHEMA_VERSION, PipelineConfig
from ..io import read_json, relative_file, sha256, write_json
from ..pipeline import run_image
from ..validation import validate_bundle

_IDENTITY_ATTRIBUTES = ("brand", "model", "serial", "color", "colour")
_ARTIFACT_REFS = ("mask_ref", "rgba_ref", "context_ref")


def _identity_key(observation: dict) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    """Produce a candidate key; this is never proof of physical identity."""
    category = observation.get("category", "").strip().casefold()
    if not category or category == "unknown":
        return None
    attributes = {
        str(name).strip().casefold(): str(value).strip().casefold()
        for name, value in observation.get("attributes", {}).items()
        if value is not None
    }
    cues = tuple((name, attributes[name]) for name in _IDENTITY_ATTRIBUTES if attributes.get(name))
    return category, cues


def _group_observations(
    frames: list[list[dict]], identity_links: Mapping[str, str] | None
) -> list[dict]:
    """Link only unique category/attribute candidates or explicitly linked instances.

    The caller of an explicit link must still review it; a link does not become
    measured identity or establish whether photos are viewpoints or motion states.
    """
    identity_links = dict(identity_links or {})
    known = {
        f"image-{index:04d}:{row['instance_id']}"
        for index, rows in enumerate(frames)
        for row in rows
    }
    unknown = set(identity_links) - known
    if unknown:
        raise ValueError(f"identity links reference nonexistent observations: {sorted(unknown)}")
    if any(not isinstance(value, str) or not value.strip() for value in identity_links.values()):
        raise ValueError("identity link labels must be nonempty strings")

    key_counts = [Counter(_identity_key(row) for row in rows) for rows in frames]
    candidates = defaultdict(list)
    for index, rows in enumerate(frames):
        for row in rows:
            source_id = f"image-{index:04d}:{row['instance_id']}"
            key = _identity_key(row)
            if source_id in identity_links:
                group_key = ("explicit", identity_links[source_id])
            elif key is None or any(count[key] > 1 for count in key_counts):
                group_key = ("unmatched", source_id)
            else:
                group_key = ("candidate", key)
            candidates[group_key].append((index, source_id, row))

    groups = []
    for group_key, items in candidates.items():
        per_frame = Counter(index for index, _, _ in items)
        categories = {row.get("category", "unknown").strip().casefold() for _, _, row in items}
        routes = {row.get("visual_route", "unresolved") for _, _, row in items}
        physical_roles = {row.get("physical_role", "unknown") for _, _, row in items}
        # A caller may explicitly link two observations with incompatible
        # identifying details. Keep them separate and surface a review issue.
        identity_values = defaultdict(set)
        for _, _, row in items:
            identity = _identity_key(row)
            for name, value in identity[1] if identity else ():
                identity_values[name].add(value)
        conflicts = (
            any(count > 1 for count in per_frame.values())
            or len(categories) > 1
            or len(routes) > 1
            or len(physical_roles) > 1
            or any(len(values) > 1 for values in identity_values.values())
        )
        # Preserve observations separately if an explicit or inferred group is
        # contradictory. Never put two objects from one photograph on one track.
        if conflicts:
            for item in items:
                groups.append(
                    {
                        "observations": [item],
                        "identity_status": "ambiguous",
                        "identity_method": "conflicting_link",
                    }
                )
        else:
            groups.append(
                {
                    "observations": items,
                    "identity_status": "unverified" if len(items) > 1 else "provisional",
                    "identity_method": group_key[0],
                }
            )
    # Assign track IDs in input order, independent of category or dictionary order.
    return sorted(groups, key=lambda group: group["observations"][0][:2])


def run_images(
    images: list[Path],
    output: Path,
    config: PipelineConfig,
    scene_id: str = "scene",
    task: str | None = None,
    *,
    identity_links: Mapping[str, str] | None = None,
    image_runner=run_image,
) -> Path:
    """Create a prepare_objects-compatible image-set perception run.

    ``identity_links`` maps ``image-0000:i0001`` to a shared label. Unlinked
    instances may be tentatively matched only by a unique category and stable
    attributes. Neither route establishes verified identity or motion.
    """
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", scene_id):
        raise ValueError("scene_id must be a short alphanumeric identifier")
    if not images:
        raise ValueError("at least one image is required")
    if len(images) > min(config.video.max_frames, 32):
        raise ValueError("image set exceeds the configured limit of 32 images")
    sources = [Path(path) for path in images]
    if any(not path.is_file() for path in sources):
        raise ValueError("every input image must exist")
    if output.exists():
        raise FileExistsError(output)
    source_rows = [
        {"image_id": f"image-{index:04d}", "filename": path.name, "sha256": sha256(path)}
        for index, path in enumerate(sources)
    ]
    with new_run(output, "image_set_perception", {"images": source_rows}) as run:
        run_id = "run-" + uuid.uuid4().hex[:12]
        run.update(
            pipeline="single_image_runs_plus_conservative_image_set_linking",
            scene_id=scene_id,
            run_id=run_id,
            task_context=task or "not_provided",
            execution_mode=(
                "replay"
                if config.vlm.backend == "replay" or config.sam.backend == "replay"
                else "live"
            ),
        )
        safe_config = config.model_dump(mode="json")
        safe_config["vlm"]["headers"] = {key: "<redacted>" for key in config.vlm.headers}
        write_json(output / "config.json", safe_config)

        frame_inputs = []
        for index, source in enumerate(sources):
            child_rel = Path("runs") / f"image-{index:04d}"
            child = output / child_rel
            image_runner(
                source,
                child,
                config,
                scene_id=f"image-{index:04d}",
                task=task,
            )
            scene = verify_run(child, "scene.json")
            validation = validate_bundle(child)
            if not validation["valid"]:
                raise ValueError(f"image-{index:04d} bundle invalid: {validation['errors']}")
            frame = scene["frame"]
            rows = read_json(relative_file(child, scene["objects_ref"]))
            background = read_json(relative_file(child, scene["background_ref"]))
            frame_inputs.append((child_rel, frame, rows, background, scene, validation))

        groups = _group_observations([items[2] for items in frame_inputs], identity_links)
        write_json(
            output / "inputs/identity_links.json",
            {
                "links": dict(identity_links or {}),
                "status": "unverified",
                "interpretation": "caller-proposed links do not prove physical identity",
            },
        )
        memberships = {}
        tracks = []
        for number, group in enumerate(groups, start=1):
            object_id = f"{scene_id}:{run_id}:object-{number:04d}"
            source_ids = []
            for index, source_id, row in group["observations"]:
                memberships[(index, row["instance_id"])] = (object_id, group)
                source_ids.append(source_id)
            tracks.append(
                {
                    "object_id": object_id,
                    "category": group["observations"][0][2].get("category", "unknown"),
                    "identity_status": group["identity_status"],
                    "identity_method": group["identity_method"],
                    "identity_cues": _identity_key(group["observations"][0][2]),
                    "identity_scope": "image-set hypothesis; no cross-image tracking performed",
                    "reference_interpretation": "unresolved",
                    "source_observation_ids": source_ids,
                    "observations": [],
                }
            )
        by_track = {row["object_id"]: row for row in tracks}

        frame_records = []
        issues = []
        for index, (child_rel, child_frame, rows, background, scene, validation) in enumerate(
            frame_inputs
        ):
            image_id = f"image-{index:04d}"
            frame_base = Path("frames") / image_id
            child_prefix = child_rel.as_posix() + "/"
            child_image_ref = child_prefix + child_frame["image_ref"]
            original_ref = (
                child_prefix + "inputs/original" + (sources[index].suffix.lower() or ".bin")
            )
            if sha256(relative_file(output, original_ref)) != source_rows[index]["sha256"]:
                raise ValueError(f"source photo changed while processing image-{index:04d}")
            observations = []
            for row in rows:
                object_id, group = memberships[(index, row["instance_id"])]
                obj = {
                    **row,
                    "object_id": object_id,
                    "observation_id": f"{image_id}:{row['instance_id']}",
                    "frame_id": image_id,
                    "source_image_id": image_id,
                    "identity_status": group["identity_status"],
                    "identity_method": group["identity_method"],
                    "native_track_keys": [],
                    "timestamp_seconds": float(index),
                    "timestamp_semantics": "image_order_proxy_not_elapsed_time",
                    "reference_interpretation": "unresolved",
                }
                for key in _ARTIFACT_REFS:
                    obj[key] = child_prefix + row[key]
                observation_ref = (
                    frame_base / "objects" / row["instance_id"] / "observation.json"
                ).as_posix()
                write_json(output / observation_ref, obj)
                by_track[object_id]["observations"].append(
                    {
                        "frame_id": image_id,
                        "timestamp_seconds": float(index),
                        "observation_ref": observation_ref,
                    }
                )
                observations.append(obj)
            objects_ref = (frame_base / "objects/index.json").as_posix()
            write_json(output / objects_ref, observations)
            background_ref = (frame_base / "background/index.json").as_posix()
            write_json(
                output / background_ref,
                {
                    **background,
                    "frame_id": image_id,
                    "image_ref": child_image_ref,
                    "masks": {
                        name: child_prefix + ref for name, ref in background["masks"].items()
                    },
                    "ready_for_3dgs": False,
                    "source_modality": "image_set",
                },
            )
            frame_records.append(
                {
                    **child_frame,
                    "frame_id": image_id,
                    "image_ref": child_image_ref,
                    "image_sha256": sha256(relative_file(output, child_image_ref)),
                    "source_ref": original_ref,
                    "source_filename": sources[index].name,
                    "source_sha256": source_rows[index]["sha256"],
                    "timestamp_seconds": float(index),
                    "capture_timestamp_seconds": None,
                    "timestamp_semantics": "image_order_proxy_not_elapsed_time",
                    "objects_ref": objects_ref,
                    "background_ref": background_ref,
                    "quality_ref": child_prefix + scene["quality_ref"],
                    "preview_ref": child_prefix + "previews/instances.png",
                    "camera_pose": None,
                    "metric_scale": "unknown",
                }
            )
            if validation["checks_status"] == "fail":
                issues.append(
                    {"code": "source_image_checks_failed", "severity": "fail", "image_id": image_id}
                )
        for track in tracks:
            if track["identity_status"] == "unverified":
                issues.append(
                    {
                        "code": "cross_image_identity_unverified",
                        "severity": "review",
                        "object_id": track["object_id"],
                        "method": track["identity_method"],
                    }
                )
            elif track["identity_status"] == "ambiguous":
                issues.append(
                    {
                        "code": "cross_image_identity_ambiguous",
                        "severity": "review",
                        "object_id": track["object_id"],
                    }
                )
        duplicate_sources = defaultdict(list)
        for row in source_rows:
            duplicate_sources[row["sha256"]].append(row["image_id"])
        for image_ids in duplicate_sources.values():
            if len(image_ids) > 1:
                issues.append(
                    {
                        "code": "duplicate_image_content",
                        "severity": "review",
                        "image_ids": image_ids,
                    }
                )
        issues.append(
            {
                "code": "reference_interpretation_unresolved",
                "severity": "review",
                "detail": "Independent photographs do not establish camera viewpoints or object motion states.",
            }
        )
        checks_status = "fail" if any(item["severity"] == "fail" for item in issues) else "review"
        quality = {
            "schema_version": SCHEMA_VERSION,
            "checks_status": checks_status,
            "issues": issues,
            "ground_truth_metrics": {"status": "not_evaluated"},
            "reconstruction_ready": False,
            "identity_scope": "candidate links only; no cross-image tracking or physical identity proof",
            "reference_interpretation": "unresolved",
            "temporal_semantics": "none",
            "scene_continuity": "unverified",
            "camera_and_scale": "unknown",
        }
        write_json(output / "quality.json", quality)
        write_json(output / "tracks/index.json", tracks)
        write_json(
            output / "inputs/images.json",
            {
                "source_modality": "image_set",
                "order_semantics": "user_supplied_order_only",
                "images": [
                    {
                        **row,
                        "source_ref": frame_records[index]["source_ref"],
                        "normalized_ref": frame_records[index]["image_ref"],
                    }
                    for index, row in enumerate(source_rows)
                ],
            },
        )
        write_json(
            output / "scene.json",
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "image_set",
                "scene_id": scene_id,
                "run_id": run_id,
                "task_context": task or "not_provided",
                "frames": frame_records,
                "tracks_ref": "tracks/index.json",
                "quality_ref": "quality.json",
                "source_manifest_ref": "inputs/images.json",
                "identity_links_ref": "inputs/identity_links.json",
                "temporal_semantics": "none",
                "scene_continuity": "unverified",
                "reference_interpretation": "unresolved",
            },
        )
        run["checks_status"] = checks_status
    return output
