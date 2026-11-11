"""Independently executable perception steps, reusing the actual VLM/SAM stages."""

import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from ..construction.common import new_run, perception_frames, read_mask, verify_run
from ..contracts import SCHEMA_VERSION, Inventory
from ..execution.context import emit
from ..io import normalize_image, read_json, relative_file, sha256, write_json
from ..masks import draw_overlay, make_crops, route_masks, routing_preview, save_mask
from ..pipeline import stage_inventory, stage_labels, stage_segment
from ..segmentation import Detection, make_segmenter
from ..vlm import make_vlm


def ingest(inputs, output):
    if any(Path(r["name"]).suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"} for r in inputs):
        raise ValueError("视频请使用 perceive 操作，包含解码与跟踪")
    with new_run(output, "input_prepare", {"inputs": [{"sha256": r["sha256"]} for r in inputs]}):
        frames = []
        for i, row in enumerate(inputs):
            folder = output / f"frames/frame-{i:06d}"
            folder.mkdir(parents=True)
            image, geometry = normalize_image(Path(row["path"]), folder / "frame.png")
            shutil.copy2(row["path"], folder / ("original" + Path(row["name"]).suffix.lower()))
            frames.append(
                {
                    "frame_id": f"frame-{i:06d}",
                    "image_ref": str((folder / "frame.png").relative_to(output)),
                    "source_sha256": row["sha256"],
                    "size": list(image.size),
                    "geometry": geometry,
                }
            )
            emit("progress", name="input", current=i + 1, total=len(inputs))
        write_json(output / "frames.json", {"frames": frames})


def inventory(images, output, config, task):
    frames = verify_run(images, "frames.json")["frames"]
    with new_run(output, "inventory", {"images": sha256(images / "artifacts.json")}):
        vlm = make_vlm(config.vlm)
        for i, frame in enumerate(frames):
            stage_inventory(
                vlm, relative_file(images, frame["image_ref"]), task, output / frame["frame_id"]
            )
            emit("progress", name="inventory", current=i + 1, total=len(frames))
        write_json(output / "frames.json", {"frames": [r["frame_id"] for r in frames]})


def segment(images, inventory_root, output, config, overrides=None):
    frames = verify_run(images, "frames.json")["frames"]
    with new_run(
        output,
        "segment",
        {
            "images": sha256(images / "artifacts.json"),
            "inventory": sha256(inventory_root / "artifacts.json"),
        },
    ):
        backend = make_segmenter(config.sam)
        for i, frame in enumerate(frames):
            data = read_json(inventory_root / frame["frame_id"] / "inventory.json")
            if overrides:
                data = {**data, "objects": overrides}
            proposed = Inventory.model_validate(data)
            with Image.open(relative_file(images, frame["image_ref"])) as raw:
                stage_segment(
                    backend, raw.convert("RGB"), proposed, config, output / frame["frame_id"]
                )
            write_json(
                output / frame["frame_id"] / "inventory.json", proposed.model_dump(mode="json")
            )
            emit(
                "artifact.published",
                name="segment",
                frame_id=frame["frame_id"],
                ref=f"{frame['frame_id']}/numbered_overlay.png",
            )
            emit("progress", name="segment", current=i + 1, total=len(frames))
        write_json(output / "frames.json", {"frames": [r["frame_id"] for r in frames]})


def detections(root):
    rows = read_json(root / "instances.json")
    items = [
        Detection(
            row["candidate_ids"],
            read_mask(root / row["instance_id"] / "visible_mask.png"),
            row["score"],
            row["method"],
            row["instance_id"],
            row.get("raw_ids", []),
        )
        for row in rows
    ]
    return items, rows


def write_frame(
    output, frame_id, image_path, detections_, rows, labels, *, object_ids, task="", provenance=None
):
    """Write a canonical image perception bundle, preserving mask routing semantics."""
    output.mkdir(parents=True, exist_ok=True)
    (output / "inputs").mkdir(exist_ok=True)
    shutil.copy2(image_path, output / "inputs/frame.png")
    with Image.open(image_path) as raw:
        image = raw.convert("RGB")
    uncertain = np.zeros((image.height, image.width), dtype=bool)
    routes, effective, issues = route_masks(detections_, labels, image.size, uncertain)
    frame = {
        "frame_id": frame_id,
        "timestamp_seconds": 0.0,
        "image_ref": "inputs/frame.png",
        "source_sha256": sha256(image_path),
        "intrinsics": None,
        "camera_pose": None,
        "metric_scale": "unknown",
        "depth_ref": None,
    }
    objects = []
    for detection, row in zip(detections_, rows, strict=True):
        identity = detection.instance_id
        label = labels[identity]
        folder = output / "objects" / identity
        folder.mkdir(parents=True)
        save_mask(folder / "visible_mask.png", detection.mask)
        rgba, context, tight, context_box = make_crops(image, detection.mask, 24)
        rgba.save(folder / "rgba.png")
        context.save(folder / "context.png")
        obj = {
            **label.model_dump(mode="json"),
            "object_id": object_ids[identity],
            "identity_scope": "this input lineage",
            "identity_status": "provisional",
            "frame_id": frame_id,
            "visual_route": effective[identity],
            "proposed_visual_route": label.visual_route,
            "candidate_ids": detection.candidate_ids,
            "sam_score": detection.score,
            "visible_pixels": int(detection.mask.sum()),
            "bbox_xyxy": tight,
            "mask_ref": f"objects/{identity}/visible_mask.png",
            "rgba_ref": f"objects/{identity}/rgba.png",
            "context_ref": f"objects/{identity}/context.png",
            "rgba_to_frame": [[1, 0, tight[0]], [0, 1, tight[1]], [0, 0, 1]],
            "context_to_frame": [[1, 0, context_box[0]], [0, 1, context_box[1]], [0, 0, 1]],
            "mask_semantics": "visible prediction, not amodal completion",
            "physical_parameters": None,
            "annotation_source": provenance or "model",
            "reconstruction_input": "limited_single_view",
        }
        write_json(folder / "object.json", obj)
        objects.append(obj)
    write_json(output / "objects/index.json", objects)
    refs = {}
    for name, mask in routes.items():
        refs[name] = f"background/{name}.png"
        save_mask(output / refs[name], mask)
    write_json(
        output / "background/index.json",
        {
            "frame_id": frame_id,
            "image_ref": frame["image_ref"],
            "masks": refs,
            "unknown_is_not_background": True,
            "ready_for_3dgs": False,
        },
    )
    (output / "previews").mkdir(exist_ok=True)
    draw_overlay(image, detections_).save(output / "previews/instances.png")
    routing_preview(image, routes).save(output / "previews/routes.png")
    write_json(
        output / "quality.json",
        {
            "checks_status": "review",
            "issues": issues,
            "pixel_partition_valid": bool(np.all(sum(m.astype(int) for m in routes.values()) == 1)),
            "validation_level": "model_outputs_unverified",
            "reconstruction_ready": False,
        },
    )
    write_json(
        output / "scene.json",
        {
            "schema_version": SCHEMA_VERSION,
            "scene_id": "studio",
            "frame": frame,
            "task_context": task,
            "objects_ref": "objects/index.json",
            "background_ref": "background/index.json",
            "quality_ref": "quality.json",
            "camera_and_physics": "unknown",
        },
    )


def label(images, masks, output, config, task):
    frames = verify_run(images, "frames.json")["frames"]
    # Multi-image identity inference remains the existing run_images workflow.
    if len(frames) != 1:
        raise ValueError("多图身份关联请使用 perceive；逐步 label 当前用于单图")
    frame = frames[0]
    with new_run(
        output,
        "label",
        {"images": sha256(images / "artifacts.json"), "masks": sha256(masks / "artifacts.json")},
    ):
        segment_root = masks / frame["frame_id"]
        found, rows = detections(segment_root)
        labels, warnings = stage_labels(
            make_vlm(config.vlm),
            images / frame["image_ref"],
            found,
            rows,
            segment_root,
            task,
            output / "stages/labels",
        )
        write_json(output / "stages/labels/warnings.json", warnings)
        object_ids = {
            d.instance_id: f"{sha256(images / 'frames.json')[:16]}:{frame['frame_id']}:{d.instance_id}"
            for d in found
        }
        write_frame(
            output,
            frame["frame_id"],
            images / frame["image_ref"],
            found,
            rows,
            labels,
            object_ids=object_ids,
            task=task,
        )


def edit(source, output, params, origin):
    scene = verify_run(source, "scene.json")
    frames = perception_frames(source, scene)
    frame_id = params.get("frame_id")
    if frame_id and not any(f["frame_id"] == frame_id for f in frames):
        raise ValueError("未找到指定帧")
    changed = (
        set(params.get("exclude", []))
        | set(params.get("labels", {}))
        | set(params.get("merge", []))
    )
    known = {
        obj["instance_id"]
        for frame in frames
        if frame_id in {None, frame["frame_id"]}
        for obj in read_json(relative_file(source, frame["objects_ref"]))
    }
    if not changed or not changed <= known:
        raise ValueError("请选择本次分割中的对象")
    # Frame-local edits do not invent cross-frame identity propagation.
    if len(frames) > 1 and not frame_id:
        raise ValueError("视频或多图修正需明确 frame_id")
    with new_run(
        output, "perception_edit", {"source": sha256(source / "artifacts.json"), "origin": origin}
    ):
        exported_frames = []
        tracks = {}
        old_tracks = (
            {r["object_id"]: r for r in read_json(source / scene["tracks_ref"])}
            if scene.get("tracks_ref")
            else {}
        )
        if len(frames) > 1 and (source / "inputs").is_dir():
            shutil.copytree(source / "inputs", output / "inputs")
        for frame in frames:
            rows = read_json(relative_file(source, frame["objects_ref"]))
            active = frame_id in {None, frame["frame_id"]}
            merge = params.get("merge", []) if active else []
            if len(merge) == 1:
                raise ValueError("合并至少需要两个区域")
            masks = {
                r["instance_id"]: read_mask(relative_file(source, r["mask_ref"])) for r in rows
            }
            if merge:
                masks[merge[0]] = np.logical_or.reduce([masks[key] for key in merge])
            selected, found, labels, ids = [], [], {}, {}
            for row in rows:
                identity = row["instance_id"]
                if identity in merge[1:]:
                    continue
                changes = params.get("labels", {}).get(identity, {}) if active else {}
                allowed = {
                    "category",
                    "task_role",
                    "physical_role",
                    "visual_route",
                    "rationale",
                    "parent_instance_id",
                }
                if set(changes) - allowed:
                    raise ValueError("语义修正包含未支持字段")
                from ..contracts import InstanceLabel

                label_data = {key: row[key] for key in InstanceLabel.model_fields if key in row}
                label_data.update(changes)
                if active and identity in params.get("exclude", []):
                    label_data.update(visual_route="exclude_transient", physical_role="none")
                if label_data.get("parent_instance_id") in merge[1:]:
                    label_data["parent_instance_id"] = merge[0]
                if label_data.get("parent_instance_id") == identity:
                    label_data["parent_instance_id"] = None
                labels[identity] = InstanceLabel.model_validate(label_data)
                found.append(
                    Detection(
                        row.get("candidate_ids", []),
                        masks[identity],
                        row.get("sam_score") or 0,
                        "user_merge" if merge and identity == merge[0] else "retained",
                        identity,
                    )
                )
                selected.append(row)
                ids[identity] = row["object_id"]
            destination = output if len(frames) == 1 else output / "frames" / frame["frame_id"]
            write_frame(
                destination,
                frame["frame_id"],
                relative_file(source, frame["image_ref"]),
                found,
                selected,
                labels,
                object_ids=ids,
                task=scene.get("task_context", ""),
                provenance=origin,
            )
            sub = read_json(destination / "scene.json")
            exported_frames.append(
                {
                    **sub["frame"],
                    **frame,
                    "image_ref": str((destination / sub["frame"]["image_ref"]).relative_to(output)),
                    "objects_ref": str((destination / "objects/index.json").relative_to(output)),
                    "background_ref": str(
                        (destination / "background/index.json").relative_to(output)
                    ),
                }
            )
            if len(frames) > 1:
                prefix = str(destination.relative_to(output)) + "/"
                data = read_json(destination / "objects/index.json")
                for obj in data:
                    for key in ("mask_ref", "rgba_ref", "context_ref"):
                        obj[key] = prefix + obj[key]
                for obj in data:
                    obj["timestamp_seconds"] = frame.get("timestamp_seconds", 0)
                    observation_ref = prefix + f"objects/{obj['instance_id']}/observation.json"
                    write_json(output / observation_ref, obj)
                    original = old_tracks.get(obj["object_id"], {})
                    track = tracks.setdefault(
                        obj["object_id"],
                        {
                            **original,
                            "object_id": obj["object_id"],
                            "identity_status": "provisional",
                            "observations": [],
                        },
                    )
                    track["observations"].append(
                        {
                            "frame_id": frame["frame_id"],
                            "timestamp_seconds": obj["timestamp_seconds"],
                            "observation_ref": observation_ref,
                        }
                    )
                write_json(destination / "objects/index.json", data)
                # The exported multi-frame bundle owns the references; discard unused nested scene entry.
                (destination / "scene.json").unlink()
                exported_frames[-1].update(
                    preview_ref=prefix + "previews/instances.png",
                    quality_ref=prefix + "quality.json",
                )
                bg = read_json(destination / "background/index.json")
                bg["image_ref"] = prefix + bg["image_ref"]
                bg["masks"] = {k: prefix + v for k, v in bg["masks"].items()}
                write_json(destination / "background/index.json", bg)
        if len(frames) > 1:
            write_json(
                output / "quality.json",
                {
                    "checks_status": "review",
                    "issues": [{"code": "frame_local_correction", "severity": "review"}],
                },
            )
            for track in tracks.values():
                track["first_seen"] = track["observations"][0]["timestamp_seconds"]
                track["last_seen"] = track["observations"][-1]["timestamp_seconds"]
            write_json(output / "tracks/index.json", list(tracks.values()))
            write_json(
                output / "scene.json",
                {
                    **scene,
                    "frames": exported_frames,
                    "tracks_ref": "tracks/index.json",
                    "quality_ref": "quality.json",
                },
            )
        write_json(
            output / "correction.json",
            {
                "origin": origin,
                "changes": params,
                "scope": "explicit annotation; not new visual recognition or physical verification",
            },
        )
