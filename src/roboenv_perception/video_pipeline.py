"""Fixed offline video runner: two VLM stages on one reference frame, native SAM tracking."""

import contextlib
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .contracts import SCHEMA_VERSION, InstanceLabel
from .io import sha256, write_json
from .masks import (
    clean_detections,
    draw_overlay,
    make_crops,
    route_masks,
    routing_preview,
    save_mask,
)
from .pipeline import stage_inventory, stage_labels, stage_segment
from .runtime import cuda_info
from .tracking import TrackRegistry
from .video_backend import ReplayVideoBackend, SAM3VideoBackend, load_frame_detections
from .video_io import decode_video, encode_preview
from .vlm import make_vlm


def run_video(
    source: Path, output: Path, config, scene_id="scene", task=None, vlm=None, backend=None
):
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", scene_id):
        raise ValueError("invalid scene_id")
    if not source.is_file():
        raise ValueError("input video does not exist")
    output.mkdir(parents=True, exist_ok=False)
    fixture = config.sam.backend == "replay" and config.vlm.backend == "replay"
    replayed = config.sam.backend == "replay" or config.vlm.backend == "replay"
    run_id = "run-" + uuid.uuid4().hex[:12]
    run = {
        "schema_version": SCHEMA_VERSION,
        "pipeline": "fixed-offline-video",
        "run_id": run_id,
        "scene_id": scene_id,
        "status": "running",
        "stages": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "replay" if replayed else "live",
        "model_execution": {"vlm": "not_run", "sam3": "not_run"},
    }
    write_json(output / "run.json", run)
    safe_config = config.model_dump(mode="json")
    safe_config["vlm"]["headers"] = {key: "<redacted>" for key in config.vlm.headers}
    write_json(output / "config.json", safe_config)

    @contextlib.contextmanager
    def stage(name):
        row = {"name": name, "status": "running"}
        run["stages"].append(row)
        write_json(output / "run.json", run)
        start = time.perf_counter()
        try:
            yield
        except Exception as error:
            row.update(status="failed", error=f"{type(error).__name__}: {error}")
            raise
        else:
            row["status"] = "completed"
        finally:
            row["elapsed_seconds"] = time.perf_counter() - start
            write_json(output / "run.json", run)

    try:
        with stage("environment"):
            if not fixture:
                run["cuda"] = cuda_info()
            backend = backend or (
                ReplayVideoBackend(config.video.replay_manifest)
                if config.sam.backend == "replay"
                else SAM3VideoBackend(config.sam)
            )
            vlm = vlm or make_vlm(config.vlm)
        with stage("decode"):
            (output / "inputs").mkdir(exist_ok=True)
            shutil.copyfile(source, output / "inputs" / ("source" + source.suffix.lower()))
            video = decode_video(source, output, config.video)
            frames = video["frames"]
            reference_index = config.video.reference_frame
            reference_path = output / frames[reference_index]["image_ref"]
        with stage("inventory"):
            inventory = stage_inventory(vlm, reference_path, task, output / "stages/inventory")
            run["model_execution"]["vlm"] = (
                "replayed" if config.vlm.backend == "replay" else "executed"
            )
        with stage("tracking"):
            manifest = backend.track(output, frames, inventory, reference_index)
            run["model_execution"]["sam3"] = (
                "replayed" if config.sam.backend == "replay" else "executed"
            )
        with stage("reference_labels"):

            class ReferenceSegmenter:
                def segment(self, image, inventory):
                    return load_frame_detections(manifest, reference_index, output), {
                        "backend": manifest["backend"]
                    }

            with Image.open(reference_path) as image:
                reference_image = image.convert("RGB")
            ref_detections, _, _, rows = stage_segment(
                ReferenceSegmenter(),
                reference_image,
                inventory,
                config,
                output / "stages/reference",
            )
            ref_labels, warnings = stage_labels(
                vlm,
                reference_path,
                ref_detections,
                rows,
                output / "stages/reference",
                task,
                output / "stages/labels",
            )
            registry = TrackRegistry(f"{scene_id}:{run_id}")
            reference_to_track, semantic_labels = {}, {}
            used = set()
            for detection in ref_detections:
                object_id, ambiguous = registry.assign(detection.track_keys, used)
                reference_to_track[detection.instance_id] = object_id
                semantic_labels[object_id] = ref_labels[detection.instance_id]
            for identifier, label in semantic_labels.items():
                registry.tracks[identifier]["reference_label"] = label.model_dump(mode="json")
                registry.tracks[identifier]["parent_object_id"] = reference_to_track.get(
                    label.parent_instance_id
                )
        with stage("frame_export"):
            frame_records, all_issues = [], []
            if video["frame_budget_reached"]:
                all_issues.append({"code": "frame_budget_reached", "severity": "review"})
            for warning in inventory.warnings + warnings:
                all_issues.append(
                    {"code": "model_warning", "severity": "review", "detail": warning}
                )
            for index, frame in enumerate(frames):
                frame_id = frame["frame_id"]
                base = Path("frames") / frame_id
                folder = output / base
                with Image.open(output / frame["image_ref"]) as image:
                    image = image.convert("RGB")
                detections, uncertain, issues = clean_detections(
                    load_frame_detections(manifest, index, output),
                    image.size,
                    config.processing,
                    config.sam.confidence_threshold,
                )
                labels, object_ids, used = {}, {}, set()
                for detection in detections:
                    object_id, ambiguous = registry.assign(detection.track_keys, used)
                    object_ids[detection.instance_id] = object_id
                    source_label = semantic_labels.get(object_id)
                    if source_label and not ambiguous:
                        label = source_label.model_copy(
                            update={
                                "instance_id": detection.instance_id,
                                "parent_instance_id": None,
                            }
                        )
                    else:
                        label = InstanceLabel(
                            instance_id=detection.instance_id,
                            category="unknown",
                            rationale="No unambiguous reference semantics",
                        )
                        issues.append(
                            {
                                "code": "identity_conflict" if ambiguous else "unseen_in_reference",
                                "severity": "review",
                                "object_id": object_id,
                            }
                        )
                    labels[detection.instance_id] = label
                for instance_id, label in labels.items():
                    parent_object = registry.tracks[object_ids[instance_id]].get("parent_object_id")
                    label.parent_instance_id = next(
                        (i for i, obj_id in object_ids.items() if obj_id == parent_object), None
                    )
                for candidate in inventory.objects:
                    count = sum(candidate.candidate_id in d.candidate_ids for d in detections)
                    if (
                        candidate.critical
                        and candidate.expected_count
                        and count < candidate.expected_count
                    ):
                        issues.append(
                            {
                                "code": "critical_candidate_not_visible",
                                "severity": "review",
                                "candidate_id": candidate.candidate_id,
                            }
                        )
                routes, effective, route_issues = route_masks(
                    detections, labels, image.size, uncertain
                )
                issues.extend(route_issues)
                observations = []
                for detection in detections:
                    instance_id = detection.instance_id
                    object_id = object_ids[instance_id]
                    obs_dir = folder / "objects" / instance_id
                    save_mask(obs_dir / "visible_mask.png", detection.mask)
                    rgba, context, tight, context_box = make_crops(
                        image, detection.mask, config.processing.crop_padding
                    )
                    rgba.save(obs_dir / "rgba.png")
                    context.save(obs_dir / "context.png")
                    obj_ref = base / "objects" / instance_id
                    obs = {
                        **labels[instance_id].model_dump(mode="json"),
                        "object_id": object_id,
                        "frame_id": frame_id,
                        "observation_id": f"{frame_id}:{instance_id}",
                        "native_track_keys": detection.track_keys,
                        "candidate_ids": detection.candidate_ids,
                        "sam_score": detection.score,
                        "bbox_xyxy": tight,
                        "mask_ref": str(obj_ref / "visible_mask.png"),
                        "rgba_ref": str(obj_ref / "rgba.png"),
                        "context_ref": str(obj_ref / "context.png"),
                        "visual_route": effective[instance_id],
                        "rgba_to_frame": [[1, 0, tight[0]], [0, 1, tight[1]], [0, 0, 1]],
                        "context_to_frame": [
                            [1, 0, context_box[0]],
                            [0, 1, context_box[1]],
                            [0, 0, 1],
                        ],
                        "timestamp_seconds": frame["timestamp_seconds"],
                        "identity_status": registry.tracks[object_id]["identity_status"],
                    }
                    write_json(obs_dir / "observation.json", obs)
                    observations.append(obs)
                    registry.tracks[object_id]["observations"].append(
                        {
                            "frame_id": frame_id,
                            "timestamp_seconds": frame["timestamp_seconds"],
                            "observation_ref": str(obj_ref / "observation.json"),
                        }
                    )
                bg_refs = {}
                for name, mask in routes.items():
                    ref = base / "background" / f"{name}.png"
                    save_mask(output / ref, mask)
                    bg_refs[name] = str(ref)
                if routes["unknown"].any():
                    issues.append(
                        {
                            "code": "unknown_pixels",
                            "severity": "review",
                            "pixels": int(routes["unknown"].sum()),
                        }
                    )
                if not detections:
                    issues.append({"code": "no_visible_instances", "severity": "review"})
                write_json(folder / "objects/index.json", observations)
                write_json(
                    folder / "background/index.json",
                    {
                        "frame_id": frame_id,
                        "image_ref": frame["image_ref"],
                        "masks": bg_refs,
                        "ready_for_3dgs": False,
                    },
                )
                write_json(
                    folder / "quality.json",
                    {"issues": issues, "ground_truth_metrics": "not_evaluated"},
                )
                preview = base / "instances.png"
                from dataclasses import replace

                preview_detections = [
                    replace(d, instance_id=object_ids[d.instance_id].rsplit(":", 1)[-1])
                    for d in detections
                ]
                draw_overlay(image, preview_detections).save(output / preview)
                routing_preview(image, routes).save(folder / "routes.png")
                frame_records.append(
                    {
                        **frame,
                        "objects_ref": str(base / "objects/index.json"),
                        "background_ref": str(base / "background/index.json"),
                        "preview_ref": str(preview),
                        "quality_ref": str(base / "quality.json"),
                    }
                )
                all_issues.extend({**issue, "frame_id": frame_id} for issue in issues)
            for track in registry.tracks.values():
                visible = sorted(track["observations"], key=lambda obs: obs["timestamp_seconds"])
                track["observations"] = visible
                track["first_seen"] = visible[0]["timestamp_seconds"] if visible else None
                track["last_seen"] = visible[-1]["timestamp_seconds"] if visible else None
            write_json(output / "tracks/index.json", list(registry.tracks.values()))
        with stage("preview"):
            try:
                preview_info = encode_preview(
                    [output / f["preview_ref"] for f in frame_records],
                    output / "previews/tracking.mp4",
                    config.video.preview_fps,
                    fixture,
                )
            except Exception as error:
                preview_info = {
                    "error": f"{type(error).__name__}: {error}",
                    "codec_requested": "h264_nvenc",
                }
                all_issues.append(
                    {
                        "code": "preview_encoder_unavailable",
                        "severity": "review",
                        "detail": preview_info["error"],
                    }
                )
                if (output / "previews/tracking.mp4").exists():
                    (output / "previews/tracking.mp4").unlink()
            write_json(output / "previews/encoding.json", preview_info)
        status = "review" if replayed or all_issues else "pass"
        quality = {
            "schema_version": SCHEMA_VERSION,
            "checks_status": status,
            "issues": all_issues,
            "ground_truth_metrics": {"status": "not_evaluated"},
            "reconstruction_ready": False,
            "identity_scope": "clip-local model identity, not verified physical identity",
            "tracking_backend": manifest["backend"],
            "reference_semantics_frame": frames[reference_index]["frame_id"],
        }
        write_json(output / "quality.json", quality)
        write_json(
            output / "scene.json",
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "video",
                "scene_id": scene_id,
                "run_id": run_id,
                "task_context": task or "not_provided",
                "frames": frame_records,
                "tracks_ref": "tracks/index.json",
                "quality_ref": "quality.json",
            },
        )
        (output / "report.md").write_text(
            f"# 离线视频感知报告\n\n处理帧：{len(frames)}；轨迹：{len(registry.tracks)}；检查：{status}。\n\n执行模式：{'含回放输出，模型执行情况见 run.json' if replayed else 'CUDA 模型流程'}。\n\n原始 PTS 和采样映射在 inputs/video.json；未处理帧没有生成 mask。\n\n本报告不证明真实分割准确率或三维重建成功。\n",
            encoding="utf-8",
        )
        run.update(status="completed", checks_status=status)
        index = {
            str(p.relative_to(output)): sha256(p)
            for p in sorted(output.rglob("*"))
            if p.is_file() and p.name not in {"artifacts.json", "run.json"}
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
