"""Single-image fixed runner. Public stage functions are future controller boundaries."""

import contextlib
import importlib.metadata
import platform
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .contracts import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    InstanceLabel,
    Inventory,
    Labels,
    PipelineConfig,
)
from .io import normalize_image, sha256, write_json
from .masks import (
    clean_detections,
    draw_overlay,
    make_crops,
    route_masks,
    routing_preview,
    save_mask,
)
from .prompts import inventory_prompt, labels_prompt
from .segmentation import make_segmenter
from .vlm import make_vlm


def stage_inventory(vlm, image_path: Path, task: str | None, stage_dir: Path):
    result = vlm.infer(
        "inventory", inventory_prompt(task), [image_path], Inventory.model_json_schema(), stage_dir
    )
    inventory = Inventory.model_validate(result)
    write_json(stage_dir / "inventory.json", inventory.model_dump(mode="json"))
    return inventory


def stage_segment(segmenter, image, inventory, config, stage_dir):
    raw, provenance = segmenter.segment(image, inventory)
    write_json(
        stage_dir / "raw_detections.json",
        [
            {
                "raw_id": f"r{index + 1:04d}",
                "candidate_ids": item.candidate_ids,
                "score": item.score,
                "method": item.method,
                "shape": list(item.mask.shape),
                "mask_ref": f"raw_masks/r{index + 1:04d}.png",
            }
            for index, item in enumerate(raw)
        ],
    )
    for index, item in enumerate(raw):
        save_mask(stage_dir / "raw_masks" / f"r{index + 1:04d}.png", item.mask)
    # Validate first; even replay masks must have original-frame geometry.
    detections, uncertain, issues = clean_detections(
        raw, image.size, config.processing, config.sam.confidence_threshold
    )
    rows = []
    for detection in detections:
        folder = stage_dir / detection.instance_id
        save_mask(folder / "visible_mask.png", detection.mask)
        rgba, context, tight, context_box = make_crops(
            image, detection.mask, config.processing.crop_padding
        )
        rgba.save(folder / "rgba.png")
        context.save(folder / "context.png")
        rows.append(
            {
                "instance_id": detection.instance_id,
                "candidate_ids": detection.candidate_ids,
                "bbox_xyxy": tight,
                "context_bbox_xyxy": context_box,
                "score": detection.score,
                "method": detection.method,
                "raw_ids": detection.raw_ids,
            }
        )
    write_json(stage_dir / "instances.json", rows)
    write_json(stage_dir / "provenance.json", provenance)
    write_json(stage_dir / "issues.json", issues)
    save_mask(stage_dir / "uncertain_mask.png", uncertain)
    draw_overlay(image, detections).save(stage_dir / "numbered_overlay.png")
    return detections, uncertain, issues, rows


def stage_labels(vlm, image_path, detections, rows, segment_dir, task, stage_dir):
    # Even an empty segmentation produces a predictable empty stage without API calls.
    if detections:
        images = [image_path, segment_dir / "numbered_overlay.png"] + [
            segment_dir / d.instance_id / "context.png" for d in detections
        ]
        result = vlm.infer(
            "labels", labels_prompt(rows, task), images, Labels.model_json_schema(), stage_dir
        )
        labels = Labels.model_validate(result)
    else:
        labels = Labels(
            instances=[], warnings=["No retained instances to label; second VLM call skipped."]
        )
    valid_ids = {d.instance_id for d in detections}
    mapping = {label.instance_id: label for label in labels.instances}
    if not mapping.keys() <= valid_ids:
        raise ValueError("VLM returned labels for nonexistent instances")
    for label in labels.instances:
        if label.parent_instance_id and (
            label.parent_instance_id not in valid_ids
            or label.parent_instance_id == label.instance_id
        ):
            raise ValueError("invalid parent instance reference")
        seen = {label.instance_id}
        parent = label.parent_instance_id
        while parent and parent in mapping:
            if parent in seen:
                raise ValueError("cyclic parent instance relation")
            seen.add(parent)
            parent = mapping[parent].parent_instance_id
    for missing in sorted(valid_ids - mapping.keys()):
        mapping[missing] = InstanceLabel(
            instance_id=missing, category="unknown", rationale="VLM omitted this instance"
        )
        labels.warnings.append(f"Missing semantic label: {missing}")
    labels.instances = list(mapping.values())
    write_json(stage_dir / "labels.json", labels.model_dump(mode="json"))
    return mapping, labels.warnings


def run_image(
    image_path: Path,
    output: Path,
    config: PipelineConfig,
    scene_id="scene",
    task=None,
    vlm=None,
    segmenter=None,
):
    """Create a NEW run; never silently replace an existing bundle or model backend."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", scene_id):
        raise ValueError("scene_id must be a short alphanumeric identifier")
    if not image_path.is_file():
        raise ValueError("input image does not exist")
    output.mkdir(parents=True, exist_ok=False)
    run_id = "run-" + uuid.uuid4().hex[:12]
    run = {
        "schema_version": SCHEMA_VERSION,
        "scene_id": scene_id,
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": "fixed-single-image",
        "status": "running",
        "stages": [],
        "task_context": task or "not_provided",
        "model_execution": {"vlm": "not_run", "sam3": "not_run"},
    }
    write_json(output / "run.json", run)
    # Never persist expanded authorization headers. Local config files are gitignored.
    safe_config = config.model_dump(mode="json")
    safe_config["vlm"]["headers"] = {name: "<redacted>" for name in config.vlm.headers}
    write_json(output / "config.json", safe_config)
    (output / "inputs").mkdir()

    @contextlib.contextmanager
    def stage(name):
        entry = {"name": name, "status": "running"}
        run["stages"].append(entry)
        write_json(output / "run.json", run)
        started = time.perf_counter()
        try:
            yield
        except Exception as error:
            entry.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        else:
            entry["status"] = "completed"
        finally:
            entry["elapsed_seconds"] = time.perf_counter() - started
            write_json(output / "run.json", run)

    try:
        with stage("ingest"):
            suffix = image_path.suffix.lower() or ".bin"
            shutil.copyfile(image_path, output / "inputs" / ("original" + suffix))
            frame_path = output / "inputs/frame.png"
            image, geometry = normalize_image(image_path, frame_path)
            source_hash = sha256(image_path)
            frame = {
                "frame_id": "frame-000000",
                "timestamp_seconds": None,
                "image_ref": "inputs/frame.png",
                "source_sha256": source_hash,
                "normalized_sha256": sha256(frame_path),
                "geometry": geometry,
                "intrinsics": None,
                "camera_pose": None,
                "metric_scale": "unknown",
                "depth_ref": None,
            }
            write_json(output / "inputs/frame.json", frame)
        with stage("backend_setup"):
            if config.sam.backend != "replay" or config.vlm.backend != "replay":
                from .runtime import cuda_info

                run["cuda"] = cuda_info()
            # Check SAM before incurring an external VLM call.
            segmenter = segmenter if segmenter is not None else make_segmenter(config.sam)
            vlm = vlm if vlm is not None else make_vlm(config.vlm)
        with stage("inventory"):
            inventory = stage_inventory(vlm, frame_path, task, output / "stages/inventory")
            run["model_execution"]["vlm"] = (
                "replayed" if config.vlm.backend == "replay" else "executed"
            )
        with stage("segment"):
            segment_dir = output / "stages/segment"
            detections, uncertain, issues, rows = stage_segment(
                segmenter, image, inventory, config, segment_dir
            )
            run["model_execution"]["sam3"] = (
                "replayed" if config.sam.backend == "replay" else "executed"
            )
        with stage("labels"):
            labels, label_warnings = stage_labels(
                vlm, frame_path, detections, rows, segment_dir, task, output / "stages/labels"
            )
        with stage("route_export"):
            for text in inventory.warnings + label_warnings:
                issues.append({"code": "model_warning", "severity": "review", "detail": text})
            if not detections:
                issues.append({"code": "no_instances", "severity": "review"})
            for candidate in inventory.objects:
                count = sum(candidate.candidate_id in d.candidate_ids for d in detections)
                missing = (count == 0 and candidate.expected_count != 0) or (
                    candidate.expected_count is not None and count < candidate.expected_count
                )
                if missing:
                    issues.append(
                        {
                            "code": "candidate_missing",
                            "severity": "fail" if candidate.critical else "review",
                            "candidate_id": candidate.candidate_id,
                            "retained": count,
                            "expected": candidate.expected_count,
                        }
                    )
                    if candidate.bbox_xyxy_norm:
                        x0, y0, x1, y1 = candidate.bbox_xyxy_norm
                        uncertain[
                            int(y0 * image.height) : int(np.ceil(y1 * image.height)),
                            int(x0 * image.width) : int(np.ceil(x1 * image.width)),
                        ] = True
            routes, effective, route_issues = route_masks(detections, labels, image.size, uncertain)
            issues.extend(route_issues)
            objects = []
            for detection, row in zip(detections, rows, strict=True):
                identifier = detection.instance_id
                label = labels[identifier]
                folder = output / "objects" / identifier
                folder.mkdir(parents=True)
                for filename in ["visible_mask.png", "rgba.png", "context.png"]:
                    shutil.copyfile(segment_dir / identifier / filename, folder / filename)
                tight, context = row["bbox_xyxy"], row["context_bbox_xyxy"]
                if effective[identifier] == "unresolved":
                    issues.append(
                        {
                            "code": "unresolved_route",
                            "severity": "review",
                            "instance_id": identifier,
                        }
                    )
                touches_edge = (
                    tight[0] == 0
                    or tight[1] == 0
                    or tight[2] == image.width
                    or tight[3] == image.height
                )
                obj = {
                    **label.model_dump(mode="json"),
                    "object_id": f"{scene_id}:{run_id}:{identifier}",
                    "identity_scope": "this single-image run only",
                    "identity_status": "provisional",
                    "visual_route": effective[identifier],
                    "proposed_visual_route": label.visual_route,
                    "candidate_ids": detection.candidate_ids,
                    "sam_score": detection.score,
                    "frame_id": frame["frame_id"],
                    "bbox_xyxy": tight,
                    "visible_pixels": int(detection.mask.sum()),
                    "mask_ref": f"objects/{identifier}/visible_mask.png",
                    "rgba_ref": f"objects/{identifier}/rgba.png",
                    "context_ref": f"objects/{identifier}/context.png",
                    "mask_semantics": "visible prediction, not amodal completion",
                    "touches_image_edge": touches_edge,
                    "rgba_to_frame": [[1, 0, tight[0]], [0, 1, tight[1]], [0, 0, 1]],
                    "context_to_frame": [[1, 0, context[0]], [0, 1, context[1]], [0, 0, 1]],
                    "reconstruction_input": "limited_single_view",
                    "physical_parameters": None,
                }
                write_json(folder / "object.json", obj)
                objects.append(obj)
            bg_refs = {}
            for name, mask in routes.items():
                ref = f"background/{name}.png"
                save_mask(output / ref, mask)
                bg_refs[name] = ref
            unknown_pixels = int(routes["unknown"].sum())
            if unknown_pixels:
                issues.append(
                    {"code": "unknown_pixels", "severity": "review", "pixels": unknown_pixels}
                )
            replayed = config.vlm.backend == "replay" or config.sam.backend == "replay"
            if replayed:
                issues.append({"code": "replayed_model_outputs", "severity": "review"})
            status = (
                "fail"
                if any(i["severity"] == "fail" for i in issues)
                else ("review" if any(i["severity"] == "review" for i in issues) else "pass")
            )
            quality = {
                "schema_version": SCHEMA_VERSION,
                "checks_status": status,
                "validation_level": "fixture_or_recording_replay"
                if replayed
                else "model_outputs_unverified",
                "ground_truth_metrics": {
                    "status": "not_evaluated",
                    "recall": None,
                    "mean_iou": None,
                    "foreground_leakage": None,
                },
                "issues": issues,
                "pixel_partition_valid": bool(
                    np.all(sum(m.astype(int) for m in routes.values()) == 1)
                ),
                "coverage_pixels": {name: int(mask.sum()) for name, mask in routes.items()},
                "reconstruction_ready": False,
                "background_ready_for_3dgs": False,
                "limitations": [
                    "Single visible view; no recovered geometry, depth, pose or metric scale.",
                    "Checks do not establish semantic correctness or robot task success.",
                ],
            }
            write_json(output / "objects/index.json", objects)
            write_json(
                output / "background/index.json",
                {
                    "frame_id": frame["frame_id"],
                    "image_ref": frame["image_ref"],
                    "masks": bg_refs,
                    "pixel_values": {"selected": 255, "other": 0},
                    "inpainted": False,
                    "checks_status": status,
                    "ready_for_3dgs": False,
                    "unknown_is_not_background": True,
                },
            )
            (output / "previews").mkdir()
            draw_overlay(image, detections).save(output / "previews/instances.png")
            routing_preview(image, routes).save(output / "previews/routes.png")
            write_json(output / "quality.json", quality)
            write_json(
                output / "scene.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "scene_id": scene_id,
                    "run_id": run_id,
                    "task_context": task or "not_provided",
                    "frame": frame,
                    "objects_ref": "objects/index.json",
                    "background_ref": "background/index.json",
                    "quality_ref": "quality.json",
                    "prompt_version": PROMPT_VERSION,
                    "camera_and_physics": "unknown",
                },
            )
            report = [
                "# 单图感知运行报告",
                "",
                f"程序检查状态：**{status}**",
                "",
                f"保留实例：{len(objects)}；验证级别：{quality['validation_level']}",
                "",
                "未测量真实分割准确率；未执行三维重建或机器人任务。",
                "",
                "路由图：红=mesh 候选，绿=背景有效区域，蓝=临时排除，灰=未知。",
                "",
                "![实例](previews/instances.png)",
                "",
                "![分流](previews/routes.png)",
                "",
                "问题：",
                "",
            ]
            report += [f"- {item['severity']}: {item['code']}" for item in issues] or [
                "- 无程序检查问题；语义准确性仍待独立评估。"
            ]
            (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        run.update(
            status="completed",
            checks_status=status,
            execution_mode="replay" if replayed else "live",
            prompt_version=PROMPT_VERSION,
            python_version=platform.python_version(),
            dependencies={
                name: importlib.metadata.version(name) for name in ["numpy", "Pillow", "pydantic"]
            },
        )
        # Relative hashes cover all emitted inputs, masks, responses and metadata.
        index = {
            str(path.relative_to(output)): sha256(path)
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name not in {"artifacts.json", "run.json"}
        }
        write_json(output / "artifacts.json", index)
        run["artifact_index_sha256"] = sha256(output / "artifacts.json")
    except Exception as error:
        run.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        run["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(output / "run.json", run)
    return output
