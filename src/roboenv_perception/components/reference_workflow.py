"""Registered, staged replay of an audited workflow using newly generated candidates.

This adapter supports one historical desktop case. It never imports an old model,
USD or passing verdict. Historical human annotations and camera fits stay labelled.
DSH selects each stage; there is no task-model loop here.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from ..artifacts.registry import tree_index
from ..construction.asset_agents import (
    child_environment,
    code_asset,
    compile_review_qa,
    review_asset,
    review_scene,
    run_artisan,
)
from ..construction.asset_fit import CalibratedViews, fit_rigid_silhouettes, write_fit
from ..construction.contracts import load_construction_config
from ..construction.video_asset_workflow import appearance, evaluate, prepare
from ..execution.context import emit, span
from ..io import read_json, sha256, write_json

CASE_ID = "laptop-desktop-20261023"
VIDEO_SHA256 = "08be6039615c0b0807ddad022272f8cbd27204024e3d7cdd02604c516df9f34e"
BLUEPRINT_SHA256 = "fe4ed9a3af10c5a251536230e3758a987562557aeb5cd20b35f27914e810ea2d"
ASSETS = ("asset-0001", "asset-0002", "asset-0003")
STAGES = ("appearance", "geometry", "qa", "simulation", "reload", "hinge_primary", "hinge_followup")


def _review_summary(report):
    return {"decision": report.get("decision"),
            "candidate_sha256": report.get("candidate_sha256"),
            "issues": [str(x)[:600] for x in report.get("issues", [])[:8]],
            "observations": [str(x)[:600] for x in report.get("observations", [])[:10]],
            "limitations": [str(x)[:600] for x in report.get("limitations", [])[:8]],
            "supplementary_hinge": {
                name: {k: value[k] for k in ("status", "checks", "report_sha256") if k in value}
                for name, value in report.get("supplementary_hinge", {}).items()
            }}


def _invalidate_scene_review(root):
    (root / "scene-review.json").unlink(missing_ok=True)


def verify_pins(pins):
    for name, row in pins.items():
        path = Path(row["path"])
        actual = tree_index(path) if path.is_dir() else {".": sha256(path)}
        if actual != row["files"]:
            raise ValueError(f"参考观测发生变化，拒绝复用：{name}")


def _pin(path):
    return {"path": str(path.resolve()), "files": tree_index(path) if path.is_dir() else {".": sha256(path)}}


def _summary(root, receipt):
    """Bounded facts for DSH; the GUI uses a separate human projection."""
    result = {k: receipt[k] for k in ("case_id", "evidence_origin", "known_limitations", "history")}
    result["workflow_steps"] = [
        "reference_fit: re-fit historical human corners and actual multi-view masks; freeze build specs",
        "reference_code: asset_id asset-0001, asset-0002, asset-0003, one per call; generates NEW model.py",
        "reference_validate: stage appearance, geometry, qa, simulation, reload, hinge_primary, hinge_followup",
        "reference_review: asset_id asset-0001/2/3, then scene; independent VLM plus executable QA",
    ]
    result["latest"] = receipt["history"][-1]
    result["geometry"] = read_json(root / "fit/geometry-validation.json") if (root / "fit/geometry-validation.json").is_file() else None
    if result["geometry"]:
        result["geometry"]["assets"] = {
            k: {f: v for f, v in row.items() if f != "views"}
            for k, row in result["geometry"]["assets"].items()
        }
    result["assets"] = {}
    for asset in ASSETS:
        work = root / "objects" / asset
        result["assets"][asset] = {
            "compiled": bool(read_json(work / "build.json").get("passed")) if (work / "build.json").exists() else False,
            "qa": read_json(work / "qa.json") if (work / "qa.json").exists() else None,
            "review": read_json(work / "review.json").get("decision") if (work / "review.json").exists() else None,
            "review_details": _review_summary(read_json(work / "review.json")) if (work / "review.json").exists() else None,
            "build_spec": read_json(work / "build-spec.json"),
        }
    for name in ("simulation", "scene-reload", "hinge-primary", "hinge-followup"):
        path = root / name / "validation.json"
        data = read_json(path) if path.exists() else {}
        result[name] = {k: data[k] for k in ("status", "checks", "scene_sha256", "failed_checks") if k in data}
        if not result[name] and (root / name / "failure.json").exists():
            result[name] = {"status": "failed", "failure": read_json(root / name / "failure.json")}
    result["scene_review"] = read_json(root / "scene-review.json") if (root / "scene-review.json").exists() else None
    result["available_images"] = [str(p.relative_to(root)) for p in (root / "simulation").glob("*.png")]
    return result


def _fit(paths, root, settings):
    archive = settings.reference_case_root
    if not archive or archive.name != "user-scene-assets-20261023":
        raise ValueError("仅支持已注册的 laptop-desktop-20261023 参考流程")
    video = paths["perception"] / "inputs/source.mp4"
    if not video.is_file() or sha256(video) != VIDEO_SHA256:
        raise ValueError("参考流程仅适用于相同原视频，不能向新输入复制历史角点或坐标")
    if sha256(archive / "objects/asset-0002/blueprint.json") != BLUEPRINT_SHA256:
        raise ValueError("已审查参考蓝图发生变化")
    source = archive.parent / "user-video-build-20261022"
    geometry = archive.parent / "user-video-isaac-20261023/input/geometry.json"
    pins = {
        "video": _pin(video), "annotations": _pin(archive / "fit/annotations.json"),
        "camera_model": _pin(source / "cameras-visible-tracks/colmap_original"),
        "evidence": _pin(source / "retry-evidence"),
        "perception": _pin(source / "perception"), "provisional_geometry": _pin(geometry),
        "table_plane": _pin(archive.parent / "user-video-table-20261023/table-plane.json"),
        "table_texture": _pin(archive.parent / "user-video-table-20261023/assets/simple/table.png"),
    }
    root.mkdir(parents=True)
    (root / "fit").mkdir()
    shutil.copyfile(archive / "fit/annotations.json", root / "fit/annotations.json")
    with span("reference.multiview_fit", human_annotations_reused=True):
        write_fit(root / "fit", source / "cameras-visible-tracks", source / "retry-evidence", geometry, root / "fit/annotations.json")
        views = CalibratedViews(source / "cameras-visible-tracks", read_json(geometry))
        for asset, category in (("asset-0001", "bottle"), ("asset-0003", "mouse")):
            result, vertices, faces = fit_rigid_silhouettes(views, source / "retry-evidence", asset, category)
            write_json(root / f"fit/{asset}-fit.json", result)
            np.savez_compressed(root / f"fit/{asset}-mesh.npz", vertices=vertices, faces=faces)
    prepare(root, source / "retry-evidence")
    for asset in ("asset-0001", "asset-0003"):
        run_artisan(settings.artisan_python, root / "objects" / asset, mode="union")
    table = root / "context/table"
    (table / "assets/simple").mkdir(parents=True)
    shutil.copyfile(Path(pins["table_plane"]["path"]), table / "table-plane.json")
    shutil.copyfile(Path(pins["table_texture"]["path"]), table / "assets/simple/table.png")
    spec = read_json(root / "scene-build-spec.json")
    spec["table_source_ref"] = "context/table"
    write_json(root / "scene-build-spec.json", spec)
    return {
        "case_id": CASE_ID, "pins": pins,
        "source_root": str(source.resolve()), "geometry_path": str(geometry.resolve()),
        "evidence_origin": "Same-video historical human corners, user-confirmed 14-inch model family, archived COLMAP/masks; optimization executed anew. Not new automatic recognition or metric measurement.",
        "known_limitations": [
            "Contact-oriented two-body laptop with photo projection; hidden electronics not reconstructed.",
            "Metric scale from catalog prior; mass/friction/working range assumed, not measured.",
            "All camera views participate in SfM; contour checks are not independent metrology.",
            "Historical 15/30/35 degree hold failures must be retested and retained; no all-angle hold claim.",
            "Case-specific desktop workflow, not a generic arbitrary-object capability. No robot actions.",
        ],
        "history": [],
    }


def _clear_downstream(root):
    # Only mutate this new snapshot; original runs remain immutable.
    for name in ("simulation", "scene-reload", "hinge-primary", "hinge-followup"):
        shutil.rmtree(root / name, ignore_errors=True)
    (root / "scene-review.json").unlink(missing_ok=True)
    for asset in ASSETS:
        (root / "objects" / asset / "review.json").unlink(missing_ok=True)
        (root / "objects" / asset / "qa.json").unlink(missing_ok=True)


def _run_sim(root, settings, stage):
    if not settings.isaac_python or not settings.isaac_python.is_file():
        raise ValueError("Isaac Python 未配置；不能借用旧验证成绩")
    repo = Path(__file__).resolve().parents[3]
    worker, src, destination = {
        "simulation": ("assets", root, root / "simulation"),
        "reload": ("scene-check", root / "simulation", root / "scene-reload"),
        "hinge_primary": ("hinge", root / "simulation", root / "hinge-primary"),
        "hinge_followup": ("hinge", root / "simulation", root / "hinge-followup"),
    }[stage]
    if stage != "simulation" and not (src / "scene.usda").exists():
        raise ValueError("必须先生成本轮实际场景")
    argv = [str(Path(sys.executable)), str(repo / "scripts/run_table_sim.py"), "--worker", worker,
            "--input", str(src), "--output", str(destination), "--python", str(settings.isaac_python),
            "--timeout", str(min(settings.max_wall_seconds - 30, 1200))]
    if stage.startswith("hinge_"):
        argv += ["--suite", "primary" if stage.endswith("primary") else "followup"]
    with (root / (stage + ".log")).open("w") as log:
        completed = subprocess.run(argv, cwd=repo, env=child_environment(), stdout=log,
                                   stderr=subprocess.STDOUT, timeout=settings.max_wall_seconds - 10)
    report = read_json(destination / "validation.json") if (destination / "validation.json").exists() else {}
    return {"exit_code": completed.returncode, "status": report.get("status", "failed"),
            "report": str((destination / "validation.json").relative_to(root)),
            "checks": report.get("checks", {}), "log": stage + ".log"}


def execute(operation, paths, output, settings, params, feedback):
    video_only = params.get('evidence_policy') == 'video_only' if operation == 'reference_fit' else (
        read_json(paths['reference']/'reference.json').get('evidence_policy') == 'video_only'
    )
    if video_only:
        from .video_only_workflow import execute as video_execute
        return video_execute(operation, paths, output, settings, params, feedback)
    root = output / "reference"
    record = {"operation": operation, "params": params, "started_unix": time.time()}
    if operation == "reference_fit":
        receipt = _fit(paths, root, settings)
    else:
        previous = paths["reference"]
        receipt = read_json(previous / "reference.json")
        if receipt.get("case_id") != CASE_ID:
            raise ValueError("未注册的参考流程")
        verify_pins(receipt["pins"])
        shutil.copytree(previous, root, ignore=shutil.ignore_patterns("kit-runtime", "__pycache__"))
        source = Path(receipt["source_root"])
        evidence = source / "retry-evidence"
        asset = params.get("asset_id", "asset-0002")
        if asset not in (*ASSETS, "scene"):
            raise ValueError("请选择已注册对象 asset-0001/2/3 或 scene")
        config = load_construction_config(settings.construction_config).vlm if operation in {"reference_code", "reference_review"} else None
        docs = settings.artisan_python.parents[3] / "sdk/_docs" if settings.artisan_python else None
        try:
            if operation == "reference_code":
                if asset not in ASSETS:
                    raise ValueError("scene 不是可编码对象")
                _clear_downstream(root)
                (root / 'fit/geometry-validation.json').unlink(missing_ok=True)
                work = root / "objects" / asset
                sdk_docs = (docs / "common/30_articulated_object.md").read_text()[:7000] + (docs / "common/20_core_types.md").read_text()[:13000]
                code_asset(config, read_json(work / "build-spec.json"),
                           [evidence / f"objects/{asset}/references.png"], work,
                           settings.artisan_python, sdk_docs, max_revisions=2,
                           review_feedback="\n".join(feedback))
            elif operation == "reference_validate":
                stage = params.get("stage")
                if stage not in STAGES:
                    raise ValueError("stage 应为 " + ", ".join(STAGES))
                if stage == "appearance":
                    _clear_downstream(root)
                    appearance(root, evidence, geometry_path=Path(receipt["geometry_path"]))
                elif stage == "geometry":
                    report = evaluate(root, source, geometry_path=Path(receipt["geometry_path"]))
                    record["metrics"] = {k: {f: v for f, v in row.items() if f != "views"} for k, row in report["assets"].items()}
                    if report["assets"]["asset-0002"]["mean_iou"] < 0.94:
                        raise ValueError("笔记本拟合轮廓低于参考约定 0.94；该指标不是实际接触精度")
                elif stage == "qa":
                    for identity in ASSETS:
                        work = root / "objects" / identity
                        spec = read_json(work / "build-spec.json")
                        plan = ["topology", "clearance"] + (["hinge"] if spec["kind"] == "articulated" else [])
                        (work / "qa_tests.py").write_text(compile_review_qa(plan, spec))
                        report = run_artisan(settings.artisan_python, work, mode="qa")
                        if not report.get("passed"):
                            raise ValueError(f"{identity} 具体蓝图 QA 未通过：{report}")
                else:
                    if stage == "simulation":
                        for identity in ASSETS:
                            if not read_json(root / "objects" / identity / "qa.json").get("passed"):
                                raise ValueError("仿真前必须先通过本轮具体蓝图 QA")
                    record["simulation"] = _run_sim(root, settings, stage)
                    if record["simulation"]["status"] != "passed":
                        record["status"] = "unverified"
            elif operation == "reference_review":
                # Both a changed object review and a failed scene attempt supersede
                # the old scene verdict in this new snapshot, never in its parent.
                _invalidate_scene_review(root)
                if asset == "scene":
                    images = [evidence / "objects/asset-0002/views/view-001/frame.png",
                              evidence / "objects/asset-0002/views/view-004/frame.png",
                              root / "simulation/frame-initial.png", root / "simulation/frame-final.png"]
                    report = review_scene(config, root, images)
                else:
                    physical = read_json(root / "simulation/validation.json")
                    reload = read_json(root / "scene-reload/validation.json")
                    scene_sha = sha256(root / "simulation/scene.usda")
                    if reload.get("status") != "passed" or physical.get("scene_sha256") != scene_sha or reload.get("scene_sha256") != scene_sha:
                        raise ValueError("当前场景尚未通过独立重载或来源发生变化")
                    physical["report_sha256"] = sha256(root / "simulation/validation.json")
                    physical["scene_reload"] = reload
                    physical["geometry_validation"] = read_json(root / "fit/geometry-validation.json")
                    work = root / "objects" / asset
                    category = read_json(work / "build-spec.json")["category"]
                    images = [evidence / f"objects/{asset}/references.png",
                              root / f"simulation/asset-{category}.png", root / "simulation/frame-initial.png"]
                    report = review_asset(config, work, images, physical, settings.artisan_python,
                                          (docs / "common/80_testing.md").read_text()[:15000])
                record["decision"] = report["decision"]
                record["review"] = _review_summary(report)
                if report["decision"] != "PASS":
                    record["status"] = "unverified"
        except (ValueError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as error:
            # Publish truthful partial state so DSH can inspect and choose a repair.
            record.update(status="unverified", error=f"{type(error).__name__}: {error}"[:16000])
    verify_pins(receipt["pins"])
    record.setdefault("status", "completed")
    record["ended_unix"] = time.time()
    receipt["history"].append(record)
    write_json(root / "reference.json", receipt)
    write_json(root / "observation.json", _summary(root, receipt))
    emit("reference.stage", **record)
    return {"reference": "reference"}, "unverified"  # No aggregate success without scoped evidence.
