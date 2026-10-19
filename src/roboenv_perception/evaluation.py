"""Frozen, independent mask evaluation. This is NOT a robot task success evaluator."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from .contracts import EvaluationManifest, TruthDocument
from .io import read_json, relative_file, sha256, write_json
from .runtime import cuda_info, require_cuda
from .validation import validate_bundle


def binary_mask(path: Path, size):
    with Image.open(path) as image:
        array = np.array(image)
    if array.shape != (size[1], size[0]) or not np.isin(array, [0, 255]).all():
        raise ValueError(f"mask must be original-size single-channel binary 0/255: {path.name}")
    return array > 0


def truth_files(manifest_path: Path):
    manifest = EvaluationManifest.model_validate(read_json(manifest_path))
    files = {manifest_path.resolve()}
    visible_count = 0
    for sample in manifest.samples:
        path = relative_file(manifest_path.parent, sample.truth)
        truth = TruthDocument.model_validate(read_json(path))
        files.add(path)
        if sample.kind == "image" and len(truth.frames) != 1:
            raise ValueError("image samples must contain exactly one truth frame")
        for frame in truth.frames:
            image_path = relative_file(path.parent, frame.image)
            if sha256(image_path) != frame.image_sha256:
                raise ValueError("truth image hash mismatch")
            files.add(image_path)
            with Image.open(image_path) as image:
                size = image.size
            ignore = (
                binary_mask(relative_file(path.parent, frame.ignore_mask), size)
                if frame.ignore_mask
                else np.zeros((size[1], size[0]), bool)
            )
            if frame.ignore_mask:
                files.add(relative_file(path.parent, frame.ignore_mask))
            for obj in frame.objects:
                if obj.mask:
                    mask_path = relative_file(path.parent, obj.mask)
                    files.add(mask_path)
                    mask = binary_mask(mask_path, size)
                    if obj.visible and not (mask & ~ignore).any():
                        raise ValueError(
                            "visible GT mask is empty after ignore; do not silently exclude it"
                        )
                visible_count += obj.visible
    if not visible_count:
        raise ValueError(
            "dataset has no visible annotated objects; complete independent annotations before freezing"
        )
    return manifest, files


def prepare_truth(bundle: Path, output: Path, sample_id="scene-001"):
    """Export only source images and empty records, NEVER turn predictions into truth."""
    import shutil

    frames = prediction_frames(bundle)
    scene = read_json(bundle / "scene.json")
    output.mkdir(parents=True, exist_ok=False)
    truth_frames = []
    for index, (frame_id, frame) in enumerate(frames.items()):
        ref = f"images/{index:06d}.png"
        (output / ref).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(relative_file(bundle, frame["image_ref"]), output / ref)
        truth_frames.append(
            {
                "frame_id": frame_id,
                "image": ref,
                "image_sha256": sha256(output / ref),
                "objects": [],
            }
        )
    write_json(
        output / "truth.json",
        {
            "schema_version": EvaluationManifest.model_fields["schema_version"].default,
            "frames": truth_frames,
        },
    )
    write_json(
        output / "dataset.json",
        {
            "dataset_id": sample_id + "-v1",
            "annotation_source": "human",
            "samples": [
                {
                    "sample_id": sample_id,
                    "kind": "video" if scene.get("kind") == "video" else "image",
                    "truth": "truth.json",
                }
            ],
        },
    )
    (output / "ANNOTATE.md").write_text(
        "# 待独立标注\n\n仅复制原始观测图片，未复制预测 mask 或语义。请独立填写 truth.json 的 objects、可见 mask、稳定 gt_id 和角色后再 freeze。空模板不能作为评测真值。\n",
        encoding="utf-8",
    )
    return output


def freeze_dataset(manifest_path: Path, output: Path):
    manifest, files = truth_files(manifest_path)
    root = manifest_path.parent.resolve()
    lock = {
        "dataset_id": manifest.dataset_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": sha256(manifest_path),
        "files": {str(p.relative_to(root)): sha256(p) for p in sorted(files)},
        "annotation_source": manifest.annotation_source,
        "split": manifest.split,
        "scope": "dataset, annotations and thresholds; prediction bundles are bound at evaluation time",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(lock, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return output


def verify_lock(manifest_path, lock_path):
    manifest, files = truth_files(manifest_path)
    lock = read_json(lock_path)
    actual = {str(p.relative_to(manifest_path.parent.resolve())): sha256(p) for p in sorted(files)}
    if lock.get("manifest_sha256") != sha256(manifest_path) or lock.get("files") != actual:
        raise ValueError("frozen dataset changed; create a new dataset version/lock")
    return manifest


def pairwise_iou(gt, predictions, torch=None):
    if not gt or not predictions:
        return np.zeros((len(gt), len(predictions)), dtype=np.float64)
    if torch is None:
        # Explicit fixture-only test oracle. Production always supplies CUDA torch.
        g = np.stack(gt).reshape(len(gt), -1).astype(np.float64)
        p = np.stack(predictions).reshape(len(predictions), -1).astype(np.float64)
        intersection = g @ p.T
        union = g.sum(1)[:, None] + p.sum(1)[None, :] - intersection
        return np.divide(intersection, union, out=np.zeros_like(union), where=union > 0)
    g = torch.as_tensor(np.stack(gt).reshape(len(gt), -1), dtype=torch.float64, device="cuda")
    p = torch.as_tensor(
        np.stack(predictions).reshape(len(predictions), -1), dtype=torch.float64, device="cuda"
    )
    intersection = g @ p.T
    union = g.sum(1)[:, None] + p.sum(1)[None, :] - intersection
    return torch.where(union > 0, intersection / union.clamp_min(1), 0).cpu().numpy()


def match_instances(ious, gt_categories, pred_categories, threshold):
    from scipy.optimize import linear_sum_assignment

    if not ious.size:
        return []
    compatible = np.array(
        [
            [g.strip().casefold() == p.strip().casefold() for p in pred_categories]
            for g in gt_categories
        ]
    )
    valid = compatible & (ious >= threshold)
    # First maximize the number of threshold-valid matches, then their total IoU.
    weights = valid * (min(ious.shape) + 1 + ious)
    rows, cols = linear_sum_assignment(weights, maximize=True)
    return [
        (int(r), int(c), float(ious[r, c])) for r, c in zip(rows, cols, strict=True) if valid[r, c]
    ]


def prediction_frames(root):
    checked = validate_bundle(root)
    if not checked["valid"]:
        raise ValueError(
            "prediction bundle failed artifact validation: " + "; ".join(checked["errors"][:3])
        )
    if checked["checks_status"] == "fail":
        raise ValueError("prediction has critical pipeline check failures")
    scene = read_json(root / "scene.json")
    if scene.get("kind") == "video":
        return {f["frame_id"]: f for f in scene["frames"]}
    frame = scene["frame"]
    return {
        frame["frame_id"]: {
            **frame,
            "objects_ref": scene["objects_ref"],
            "background_ref": scene["background_ref"],
        }
    }


def evaluate(manifest_path: Path, lock_path: Path, output: Path, predictions=None, fixture=False):
    manifest = verify_lock(manifest_path, lock_path)
    if fixture and manifest.annotation_source != "synthetic_fixture":
        raise ValueError("fixture CPU oracle is forbidden for human-annotated datasets")
    torch = None if fixture else require_cuda()
    output.mkdir(parents=True, exist_ok=False)
    overrides = read_json(predictions) if predictions else {}
    if (
        not isinstance(overrides, dict)
        or not set(overrides) <= {s.sample_id for s in manifest.samples}
        or any(value is not None and not isinstance(value, str) for value in overrides.values())
    ):
        raise ValueError("prediction map must map known sample IDs to bundle directories or null")
    started = time.perf_counter()
    total = {
        "gt_visible": 0,
        "critical_gt": 0,
        "matched": 0,
        "critical_matched": 0,
        "false_positives": 0,
        "iou_sum": 0.0,
        "foreground_pixels_measured": 0,
        "leaked_pixels": 0,
        "foreground_pixels_unavailable": 0,
        "id_switches": 0,
        "reacquisition_events": 0,
        "reacquisition_successes": 0,
        "failed_samples": 0,
        "failed_frames": 0,
        "total_samples": len(manifest.samples),
    }
    sample_reports, all_live = [], True
    try:
        for sample in manifest.samples:
            truth_path = relative_file(manifest_path.parent, sample.truth)
            truth = TruthDocument.model_validate(read_json(truth_path))
            raw_prediction = overrides.get(sample.sample_id, sample.prediction)
            bundle = None
            pred_frames, error = {}, sample.failure_reason
            if raw_prediction:
                base = (
                    predictions.parent
                    if predictions and sample.sample_id in overrides
                    else manifest_path.parent
                )
                bundle = (base / raw_prediction).resolve()
                try:
                    pred_frames = prediction_frames(bundle)
                    run = read_json(bundle / "run.json")
                    all_live &= run.get("execution_mode") == "live" and run.get(
                        "model_execution"
                    ) == {"vlm": "executed", "sam3": "executed"}
                    if (sample.kind == "video") != (
                        read_json(bundle / "scene.json").get("kind") == "video"
                    ):
                        raise ValueError("prediction/truth sample kind mismatch")
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    pred_frames = {}
                    all_live = False
            else:
                all_live = False
                error = error or "prediction unavailable"
            report = {
                "sample_id": sample.sample_id,
                "failure": error,
                "frames": [],
                "prediction": str(bundle) if bundle else None,
            }
            if bundle and (bundle / "run.json").is_file():
                report["prediction_run_sha256"] = sha256(bundle / "run.json")
                try:
                    recorded_run = read_json(bundle / "run.json")
                    report["performance"] = {
                        "stage_elapsed_seconds": {
                            s["name"]: s.get("elapsed_seconds")
                            for s in recorded_run.get("stages", [])
                        },
                        "gpu": recorded_run.get("cuda"),
                        "provider_cost": None,
                        "vlm_calls": [
                            read_json(path)
                            for path in [
                                bundle / "stages/inventory/call.json",
                                bundle / "stages/labels/call.json",
                            ]
                            if path.is_file()
                        ],
                    }
                except (ValueError, KeyError, TypeError) as exc:
                    error = f"Invalid prediction metadata: {exc}"
                    report["failure"] = error
                    pred_frames, all_live = {}, False
                if (bundle / "artifacts.json").is_file():
                    report["prediction_artifacts_sha256"] = sha256(bundle / "artifacts.json")
                if (bundle / "config.json").is_file():
                    report["prediction_config_sha256"] = sha256(bundle / "config.json")
            last_identity, was_invisible = {}, set()
            sample_failed = bool(error)
            for frame in sorted(truth.frames, key=lambda f: f.frame_id):
                with Image.open(relative_file(truth_path.parent, frame.image)) as image:
                    size = image.size
                ignore = (
                    binary_mask(relative_file(truth_path.parent, frame.ignore_mask), size)
                    if frame.ignore_mask
                    else np.zeros((size[1], size[0]), bool)
                )
                visible = [obj for obj in frame.objects if obj.visible]
                gt_masks = [
                    binary_mask(relative_file(truth_path.parent, obj.mask), size) & ~ignore
                    for obj in visible
                ]
                for obj in frame.objects:
                    if not obj.visible:
                        was_invisible.add(obj.gt_id)
                total["gt_visible"] += len(visible)
                total["critical_gt"] += sum(obj.critical for obj in visible)
                foreground = np.zeros((size[1], size[0]), bool)
                for obj, mask in zip(visible, gt_masks, strict=True):
                    if obj.exclude_from_background:
                        foreground |= mask
                rows, pred_masks, background, frame_error = [], [], None, error
                try:
                    if frame.frame_id not in pred_frames:
                        raise ValueError("prediction frame unavailable")
                    prediction = pred_frames[frame.frame_id]
                    image_path = relative_file(bundle, prediction["image_ref"])
                    if sha256(image_path) != frame.image_sha256:
                        raise ValueError("prediction does not correspond to the frozen truth image")
                    rows = read_json(relative_file(bundle, prediction["objects_ref"]))
                    valid_rows = []
                    for row in rows:
                        mask = binary_mask(relative_file(bundle, row["mask_ref"]), size) & ~ignore
                        if mask.any():
                            valid_rows.append(row)
                            pred_masks.append(mask)
                    rows = valid_rows
                    bg = read_json(relative_file(bundle, prediction["background_ref"]))
                    background = (
                        binary_mask(relative_file(bundle, bg["masks"]["background"]), size)
                        & ~ignore
                    )
                    frame_error = None
                except Exception as exc:
                    frame_error = f"{type(exc).__name__}: {exc}"
                    rows, pred_masks, background = [], [], None
                    sample_failed = True
                    total["failed_frames"] += 1
                matches = match_instances(
                    pairwise_iou(gt_masks, pred_masks, torch),
                    [o.category for o in visible],
                    [r["category"] for r in rows],
                    manifest.match_iou,
                )
                total["matched"] += len(matches)
                total["critical_matched"] += sum(visible[g].critical for g, _, _ in matches)
                total["false_positives"] += len(rows) - len(matches)
                total["iou_sum"] += sum(iou for _, _, iou in matches)
                mapping = {visible[g].gt_id: rows[p]["object_id"] for g, p, _ in matches}
                if sample.kind == "video":
                    for obj in visible:
                        matched_id = mapping.get(obj.gt_id)
                        if obj.gt_id in was_invisible and obj.gt_id in last_identity:
                            total["reacquisition_events"] += 1
                            total["reacquisition_successes"] += (
                                matched_id == last_identity[obj.gt_id]
                            )
                        was_invisible.discard(obj.gt_id)
                        if matched_id:
                            if (
                                obj.gt_id in last_identity
                                and last_identity[obj.gt_id] != matched_id
                            ):
                                total["id_switches"] += 1
                            last_identity[obj.gt_id] = matched_id
                if background is None:
                    total["foreground_pixels_unavailable"] += int(foreground.sum())
                else:
                    total["foreground_pixels_measured"] += int(foreground.sum())
                    total["leaked_pixels"] += int((foreground & background).sum())
                report["frames"].append(
                    {
                        "frame_id": frame.frame_id,
                        "gt_visible": len(visible),
                        "matches": len(matches),
                        "misses": len(visible) - len(matches),
                        "false_positives": len(rows) - len(matches),
                        "associations": mapping,
                        "failure": frame_error,
                    }
                )
            total["failed_samples"] += sample_failed
            sample_reports.append(report)

        def ratio(n, d):
            return n / d if d else None

        metrics = {
            "object_recall": ratio(total["matched"], total["gt_visible"]),
            "critical_recall": ratio(total["critical_matched"], total["critical_gt"]),
            "precision": ratio(total["matched"], total["matched"] + total["false_positives"]),
            "mean_iou_misses_zero": ratio(total["iou_sum"], total["gt_visible"]),
            "foreground_leakage": ratio(
                total["leaked_pixels"], total["foreground_pixels_measured"]
            ),
            "id_switches": total["id_switches"],
            "reacquisition_rate": ratio(
                total["reacquisition_successes"], total["reacquisition_events"]
            ),
        }
        gates = {
            "all_samples_available": total["failed_samples"] == 0,
            "visible_ground_truth_present": total["gt_visible"] > 0,
            "critical_recall": metrics["critical_recall"] is None
            or metrics["critical_recall"] >= manifest.target_critical_recall,
            "mean_iou": metrics["mean_iou_misses_zero"] is not None
            and metrics["mean_iou_misses_zero"] >= manifest.target_mean_iou,
            "foreground_leakage": total["foreground_pixels_unavailable"] == 0
            and (
                metrics["foreground_leakage"] is None
                or metrics["foreground_leakage"] <= manifest.max_foreground_leakage
            ),
            "id_switches": total["id_switches"] <= manifest.max_id_switches,
        }
        eligible = manifest.annotation_source == "human" and all_live and not fixture
        result = {
            "dataset_id": manifest.dataset_id,
            "annotation_source": manifest.annotation_source,
            "manifest_sha256": sha256(manifest_path),
            "lock_sha256": sha256(lock_path),
            "metrics": metrics,
            "counts": total,
            "gates": gates,
            "metric_gates_passed": all(gates.values()),
            "eligible_for_acceptance": eligible,
            "acceptance_passed": eligible and all(gates.values()),
            "samples": sample_reports,
            "elapsed_seconds": time.perf_counter() - started,
            "compute": {"backend": "numpy_fixture_oracle"} if fixture else cuda_info(),
            "scope": "perception baseline only, not robot Benchmark task success",
        }
        write_json(output / "evaluation.json", result)
        (output / "report.md").write_text(
            "# 感知基线评估\n\n"
            + f"数据集：{manifest.dataset_id}；标注：{manifest.annotation_source}。\n\n"
            + "```json\n"
            + json.dumps(metrics, ensure_ascii=False, indent=2)
            + "\n```\n\n"
            + f"失败样本：{total['failed_samples']}/{total['total_samples']}；正式接纳：{result['acceptance_passed']}。\n\n"
            + "漏检/失败的真值对象计入分母，IoU 记 0。背景缺失单独计数并使门槛失败。合成与回放数据不用于正式接纳。\n",
            encoding="utf-8",
        )
        return result
    except Exception as error:
        write_json(output / "error.json", {"error": f"{type(error).__name__}: {error}"})
        raise
