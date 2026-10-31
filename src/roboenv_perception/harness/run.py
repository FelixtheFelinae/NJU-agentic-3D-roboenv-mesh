"""Restartable agentic construction controller over existing perception and design stages."""

import shutil
import time
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import Field, model_validator

from ..construction.common import verify_run
from ..construction.contracts import Blueprint, load_construction_config
from ..construction.design import design_objects
from ..construction.evidence import prepare_objects
from ..contracts import Contract
from ..io import load_config, read_json, relative_file, sha256, write_json
from ..pipeline import run_image
from ..validation import validate_bundle
from ..video_pipeline import run_video
from .assembly import ASSEMBLY_INTENT, AssemblyPlan, infer_connections, persist_audit
from .assembly_adapter import recipe_parts
from .geometry import build_recipe, infer_recipe, review_geometry
from .media import run_images
from .research import research_objects
from .web import WebSearchPolicy


class HarnessRequest(Contract):
    scene_id: str = Field(default="scene", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    task: str = Field(min_length=3, max_length=4000)
    video: Path | None = None
    images: list[Path] = Field(default_factory=list, max_length=32)
    assembly_overrides: Path | None = None

    @model_validator(mode="after")
    def one_media_mode(self):
        if (self.video is None) == (not self.images):
            raise ValueError("provide either one video or one or more images")
        return self


class HarnessSettings(Contract):
    perception_config: Path
    construction_config: Path
    artisan_python: Path
    blender: Path | None = None
    isaac_python: Path | None = None
    max_geometry_revisions: int = Field(default=3, ge=1, le=8)
    max_physics_revisions: int = Field(default=2, ge=0, le=5)
    max_wall_seconds: int = Field(default=7200, ge=60, le=86400)
    build_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    physics_timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    web_search: bool = True
    web_provider: Literal["duckduckgo", "wikipedia", "searxng"] = "duckduckgo"
    web_dns_resolver: Literal["system", "google"] = "system"
    searxng_endpoint: str = ""
    allow_category_search: bool = False
    official_domains: list[str] = Field(default_factory=list)
    allowed_source_domains: list[str] = Field(default_factory=list)
    enable_scene_assembly: bool = True
    enable_physics: bool = True
    background_mode: Literal["off", "masks", "auto"] = "auto"
    background_generation_config: Path | None = None

    @model_validator(mode="after")
    def generation_needs_background(self):
        if self.background_generation_config and self.background_mode == "off":
            raise ValueError("background generation cannot be requested with background_mode=off")
        return self


class HarnessFailure(RuntimeError):
    pass


def _fingerprint(request: HarnessRequest, settings: HarnessSettings) -> dict:
    paths = [settings.perception_config, settings.construction_config,
             settings.artisan_python, *(request.images or ([request.video] if request.video else []))]
    if settings.blender:
        paths.append(settings.blender)
    if request.assembly_overrides:
        paths.append(request.assembly_overrides)
    if settings.isaac_python:
        paths.append(settings.isaac_python)
    if settings.background_generation_config:
        paths.append(settings.background_generation_config)
        from ..construction.background_generation import load_background_generation_config

        generation = load_background_generation_config(settings.background_generation_config)
        paths.extend(p for p in generation.model_path.rglob("*")
                     if p.is_file() and ".cache" not in p.relative_to(generation.model_path).parts)
    perception = load_config(settings.perception_config)
    construction = load_construction_config(settings.construction_config)
    for filename in (construction.design.measurements, perception.sam.replay_manifest,
                     perception.video.replay_manifest):
        if filename:
            paths.append(Path(filename))
    for config in (perception.vlm, construction.vlm):
        if config.backend == "replay":
            paths.extend(sorted(Path(config.replay_dir).glob("*.json")))
    # Resuming under changed controller/worker code must not reuse old reviews.
    paths.extend(sorted(Path(__file__).resolve().parents[1].rglob("*.py")))
    missing = [str(path) for path in paths if not Path(path).is_file()]
    if missing:
        raise FileNotFoundError(f"harness input or configured tool missing: {missing}")
    return {str(Path(path).resolve()): sha256(Path(path)) for path in paths}


def _write_state(root: Path, state: dict):
    pending = root / "state.json.tmp"
    write_json(pending, state)
    pending.replace(root / "state.json")


def _next_path(root: Path, stage: str) -> Path:
    base = root / stage
    if not base.exists():
        return base
    i = 1
    while (root / f"{stage}-{i:03d}").exists():
        i += 1
    return root / f"{stage}-{i:03d}"


def _stage(root: Path, state: dict, name: str, func):
    entry = state["stages"].get(name)
    if entry and entry["status"] == "completed":
        path = root / entry["path"]
        if not path.exists() or not (path / entry["sentinel"]).is_file():
            raise HarnessFailure(f"completed stage {name} changed or is missing")
        if sha256(path / entry["sentinel"]) != entry["sentinel_sha256"]:
            raise HarnessFailure(f"completed stage {name} changed after acceptance")
        index_path = path / ".harness-artifacts.json"
        if not index_path.is_file() or sha256(index_path) != entry.get("artifacts_sha256"):
            raise HarnessFailure(f"completed stage {name} artifact index changed or is missing")
        for ref, expected in read_json(index_path).items():
            artifact = relative_file(path, ref)
            if not artifact.is_file() or sha256(artifact) != expected:
                raise HarnessFailure(f"completed stage {name} artifact changed or missing: {ref}")
        if name.startswith("assets"):
            for row in read_json(path / "assets.json")["objects"]:
                if row.get("usd") and sha256(Path(row["usd"])) != row["usd_sha256"]:
                    raise HarnessFailure(f"selected USD changed: {row['asset_id']}")
        if name.startswith("scene") and (path / "run.json").is_file():
            verify_run(path, "layout.json")
        return path
    path = _next_path(root, name)
    state["stages"][name] = {"status": "running", "path": str(path.relative_to(root)),
                              "started_unix": time.time()}
    _write_state(root, state)
    try:
        sentinel = func(path)
        sentinel_path = path / sentinel
        if not sentinel_path.is_file():
            raise HarnessFailure(f"stage {name} did not persist {sentinel}")
        index_path = path / ".harness-artifacts.json"
        write_json(index_path, {str(item.relative_to(path)): sha256(item)
                               for item in sorted(path.rglob("*"))
                               if item.is_file() and item != index_path})
        state["stages"][name].update(status="completed", sentinel=sentinel,
                                      sentinel_sha256=sha256(sentinel_path),
                                      artifacts_sha256=sha256(index_path),
                                      completed_unix=time.time())
        _write_state(root, state)
        return path
    except Exception as error:
        state["stages"][name].update(status="failed", error=f"{type(error).__name__}: {error}")
        _write_state(root, state)
        raise


def _perception(path: Path, request: HarnessRequest, config):
    if request.video:
        run_video(request.video, path, config, request.scene_id, request.task)
    elif len(request.images) == 1:
        run_image(request.images[0], path, config, request.scene_id, request.task)
    else:
        run_images(request.images, path, config, request.scene_id, request.task)
    report = validate_bundle(path)
    write_json(path / "harness_validation.json", report)
    if not report["valid"] or report.get("checks_status") == "fail":
        raise HarnessFailure(f"perception bundle failed: {report['errors']}")
    return "scene.json"


def _build_assets(path: Path, designs: Path, evidence: Path, research: Path,
                  settings: HarnessSettings, config, deadline: float,
                  previous_assets: Path | None = None,
                  repair_feedback: dict[str, list[str]] | None = None,
                  assembly_required: bool = False,
                  assembly_overrides: dict | None = None):
    path.mkdir(parents=True, exist_ok=False)
    design_index = read_json(designs / "designs.json")
    research_map = read_json(research / "research.json").get("objects", {})
    previous = {r["asset_id"]: r for r in read_json(previous_assets / "assets.json")["objects"]} if previous_assets else {}
    repair_feedback = repair_feedback or {}
    rows = []
    for entry in design_index["objects"]:
        if previous_assets and entry["asset_id"] not in repair_feedback:
            rows.append(previous[entry["asset_id"]])
            continue
        if entry["status"] != "design_ready":
            rows.append({**entry, "build_status": "routed" if entry["status"] == "routed" else "blocked"})
            continue
        asset_id, object_id = entry["asset_id"], entry["object_id"]
        blueprint = Blueprint.model_validate(read_json(designs / f"objects/{asset_id}/blueprint.json"))
        observation = read_json(evidence / f"objects/{asset_id}/evidence.json")
        images = [relative_file(evidence, view["context_ref"]) for view in observation["views"]]
        web_claims = research_map.get(object_id, {}).get("claims", [])
        if assembly_required:
            context = {
                'task': 'Infer mechanically plausible fastening hypotheses for this asset; '
                        'return a JSON instance, never the schema itself.',
                'blueprint': blueprint.model_dump(mode='json'),
                'allowed_refs': [v['ref_id'] for v in observation['views']],
                'image_order': [v['ref_id'] for v in observation['views']],
                'external_claims': web_claims,
            }
            try:
                reasoning = infer_connections(config.vlm, context, images,
                                               path / asset_id / 'assembly_reasoning')
                observation = {**observation, 'assembly_reasoning': reasoning.model_dump(mode='json')}
            except Exception as error:
                write_json(path / asset_id / 'assembly_reasoning/failure.json',
                           {'error': f'{type(error).__name__}: {error}',
                            'status': 'proposal_unavailable; geometry-stage reasoning still required'})
        row = {"asset_id": asset_id, "object_id": object_id, "build_status": "unverified",
               "attempts": []}
        issues = repair_feedback.get(asset_id, [])
        prior_recipe = (read_json(Path(previous[asset_id]["recipe_ref"]))
                        if asset_id in previous and previous[asset_id].get("recipe_ref") else None)
        for revision in range(settings.max_geometry_revisions):
            if time.monotonic() >= deadline:
                issues.append("task wall time budget exhausted")
                break
            candidate_dir = path / asset_id / f"candidate-{revision:03d}"
            candidate_dir.mkdir(parents=True)
            attempt = {"revision": revision, "status": "running", "issues": []}
            row["attempts"].append(attempt)
            try:
                recipe = infer_recipe(config.vlm, blueprint, observation, images,
                                      candidate_dir / "author", issues, web_claims, prior_recipe)
                prior_recipe = recipe.model_dump(mode="json")
                if assembly_overrides and object_id in assembly_overrides:
                    recipe.assembly = AssemblyPlan.model_validate(assembly_overrides[object_id])
                    write_json(candidate_dir / 'assembly_override.json', assembly_overrides[object_id])
                    write_json(candidate_dir / 'author/recipe.json', recipe.model_dump(mode='json'))
                    prior_recipe = recipe.model_dump(mode='json')
                allowed_refs = {v['ref_id'] for v in observation['views']}
                allowed_refs.update(c['source_id'] for c in web_claims if c.get('status') == 'candidate')
                assembly = persist_audit(candidate_dir / 'assembly', recipe_parts(blueprint, recipe),
                                         recipe.assembly, allowed_refs=allowed_refs,
                                         required=assembly_required)
                built = build_recipe(blueprint, recipe, candidate_dir / "built",
                                     artisan_python=settings.artisan_python,
                                     blender=settings.blender,
                                     timeout_seconds=settings.build_timeout_seconds)
                review = review_geometry(config.vlm, blueprint, observation, recipe,
                                         images, built, candidate_dir / "review")
                attempt.update(status="reviewed", candidate=built,
                               review=review.model_dump(mode="json"))
                if review.decision == "PASS":
                    row.update(build_status="visual_review_passed", selected_revision=revision,
                               usd=built["usd"], usd_sha256=built["usd_sha256"],
                               recipe_ref=str((candidate_dir / "author/recipe.json").resolve()),
                               model_ref=str((candidate_dir / "built/model.py").resolve()),
                               preview_refs=built["visual_previews"], physics_status="not_run",
                               physics_notes=built.get("physics_notes", []))
                    row.update(assembly_status=assembly['status'],
                               assembly_report=str((candidate_dir / 'assembly/assembly_validation.json').resolve()),
                               assembly_report_sha256=sha256(candidate_dir / 'assembly/assembly_validation.json'))
                    break
                issues = review.issues
            except Exception as error:
                issues = [f"{type(error).__name__}: {error}"]
                attempt.update(status="failed", issues=issues)
            write_json(path / "assets.json", {"objects": rows + [row], "all_visual_reviews_passed": False})
        if row["build_status"] != "visual_review_passed":
            row["issues"] = issues
            if previous_assets and previous.get(asset_id, {}).get("build_status") == "visual_review_passed":
                row = {**previous[asset_id], "repair_status": "failed_previous_candidate_retained",
                       "repair_attempts": row["attempts"], "repair_issues": issues}
        rows.append(row)
        write_json(path / "assets.json", {"objects": rows,
                                           "all_visual_reviews_passed": False})
    passed = bool(rows) and all(r["build_status"] in {"visual_review_passed", "routed"} for r in rows)
    write_json(path / "assets.json", {"objects": rows, "all_visual_reviews_passed": passed,
                                       "scope": "visual review and Artisan export; physics is separate"})
    return "assets.json"


def _assemble(path: Path, assets: Path, evidence: Path, request: HarnessRequest):
    from .scene import SupportSpec, assemble_scene, scene_asset_from_evidence

    inventory = read_json(assets / "assets.json")
    ready = [row for row in inventory["objects"] if row.get("build_status") == "visual_review_passed"]
    if not ready:
        path.mkdir(parents=True, exist_ok=False)
        write_json(path / "scene_result.json", {"status": "blocked", "reason": "no reviewed assets"})
        return "scene_result.json"
    observations = {row["asset_id"]: read_json(evidence / f"objects/{row['asset_id']}/evidence.json")
                    for row in ready}
    common_frames = set(v["frame_id"] for v in observations[ready[0]["asset_id"]]["views"])
    for row in ready[1:]:
        common_frames &= {v["frame_id"] for v in observations[row["asset_id"]]["views"]}
    if not common_frames:
        path.mkdir(parents=True, exist_ok=False)
        write_json(path / "scene_result.json", {"status": "blocked",
                                                "reason": "no common image frame for all asset placements"})
        return "scene_result.json"
    frame_id = sorted(common_frames)[0]
    selected = next(v for v in observations[ready[0]["asset_id"]]["views"] if v["frame_id"] == frame_id)
    with Image.open(evidence / selected["frame_ref"]) as image:
        image_size = image.size
    records = []
    for row in ready:
        built = Path(row["model_ref"]).parent
        blueprint = Blueprint.model_validate(read_json(built / "blueprint.json"))
        records.append(scene_asset_from_evidence(
            row["asset_id"], Path(row["usd"]), observations[row["asset_id"]],
            read_json(built / "visual/result.json"), frame_id, image_size,
            kind=blueprint.kind,
        ))
    support_view = None
    for entry in read_json(evidence / "objects.json")["objects"]:
        obj = read_json(relative_file(evidence, entry["evidence_ref"]))
        if any(word in obj.get("category", "").casefold() for word in ("table", "desk", "ground", "floor", "桌", "地")):
            support_view = next((v for v in obj.get("views", []) if v["frame_id"] == frame_id), None)
            if support_view:
                break
    tabletop = "table" in request.task.casefold() or "桌" in request.task
    support = SupportSpec(frame_id=frame_id, image_size_xy=image_size,
                          image_bbox_xyxy=tuple(support_view["bbox_xyxy"]) if support_view else (0, 0, *image_size),
                          size_x_m=0.8 if tabletop else 4.0,
                          size_y_m=1.2 if tabletop else 4.0,
                          top_z_m=0.75 if tabletop else 0.0,
                          dimension_source="assumed",
                          evidence_refs=[support_view["ref_id"]] if support_view else ["task:assumed_support"],
                          dimension_note="Provisional support dimensions from task context; no metric calibration.")
    assemble_scene(records, path, support)
    layout = read_json(path / "layout.json")
    write_json(path / "scene_result.json", {"status": "assembled", "scene_ref": "scene.usda",
                                            "layout_ref": "layout.json", "issues": layout["issues"],
                                            "scene_sha256": sha256(path / "scene.usda")})
    index = read_json(path / "artifacts.json")
    index["scene_result.json"] = sha256(path / "scene_result.json")
    write_json(path / "artifacts.json", index)
    scene_run = read_json(path / "run.json")
    scene_run["artifact_index_sha256"] = sha256(path / "artifacts.json")
    write_json(path / "run.json", scene_run)
    return "scene_result.json"


def _verify(path: Path, scene: Path, assets: Path, settings: HarnessSettings,
            deadline: float | None = None, frozen: Path | None = None):
    from .physics import InteractionFamily, PhysicsProtocol, PhysicsTask, run_physics_task

    path.mkdir(parents=True, exist_ok=False)
    asset_rows = read_json(assets / "assets.json")["objects"]
    scene_result = read_json(scene / "scene_result.json")
    if scene_result.get("status") != "assembled":
        write_json(path / "physics.json", {"status": "blocked", "results": []})
        return "physics.json"
    if not settings.isaac_python:
        write_json(path / "physics.json", {"status": "not_run", "reason": "Isaac Python unconfigured", "results": []})
        return "physics.json"
    layout = read_json(scene / "layout.json")
    prims = {item["asset_id"]: item["prim_path"] for item in layout["assets"]}
    output_rows = []
    frozen_tests = ({(row["asset_id"], test["name"]): test["protocol"]
                     for row in read_json(frozen / "physics.json").get("results", [])
                     for test in row.get("tests", []) if test.get("protocol")}
                    if frozen else {})
    for row in asset_rows:
        if row.get("build_status") != "visual_review_passed":
            continue
        asset_id = row["asset_id"]
        blueprint = Blueprint.model_validate(read_json(Path(row["model_ref"]).parent / "blueprint.json"))
        moving = [j for j in blueprint.joints if j.type != "fixed"]
        by_name = {joint.name: joint for joint in moving}
        protocols = []
        unsupported = []
        covered = set()
        target = prims[asset_id]
        for index, coupling in enumerate(blueprint.couplings):
            driver = by_name[coupling.driver_joint]
            protocols.append((f"coupling-{index:02d}", PhysicsProtocol(
                family=InteractionFamily.COUPLED, target_prim=target,
                joint_name=driver.name, follower_joint_name=coupling.driven_joint,
                moving_body_name=driver.child, base_body_name=driver.parent,
                torque_axis_world=driver.axis_child or (1, 0, 0),
                target_ratio=coupling.ratio.value, min_response=0.03,
                max_baseline=0.05, max_ratio_residual=0.05,
                max_disconnected_response=0.01,
                fixture_description="assembled scene with test-only fixed-base and disconnected clone",
            )))
            covered.update((coupling.driver_joint, coupling.driven_joint))
        for joint in moving:
            if joint.name in covered:
                continue
            if joint.type not in {"revolute", "continuous"}:
                unsupported.append({"name": joint.name, "status": "unsupported",
                                    "issues": [f"no generic physical worker for {joint.type} joint"]})
                continue
            protocols.append((joint.name, PhysicsProtocol(
                family=InteractionFamily.HINGE if joint.type == "revolute" else InteractionFamily.ROTARY,
                target_prim=target, joint_name=joint.name,
                moving_body_name=joint.child, base_body_name=joint.parent,
                torque_axis_world=joint.axis_child or (1, 0, 0),
                effort_Nm=-0.2 if joint.rest == "at_max" else 0.2,
                expected_joint_sign=-1 if joint.rest == "at_max" else 1,
                min_response=0.03, max_baseline=0.05,
                fixture_description="assembled scene; joint-local axis assumed world-aligned",
            )))
        if not moving:
            recipe = read_json(Path(row["recipe_ref"])) if row.get("recipe_ref") else {}
            masses = recipe.get("part_masses_kg", {})
            total_mass = sum(masses.values()) if masses else 0.15
            protocols.append(("rigid", PhysicsProtocol(
                family=InteractionFamily.RIGID, target_prim=target,
                min_response=0.002, max_baseline=0.005,
                drive_s=0.05, release_s=0.1,
                force_world_N=(2.0 * total_mass * 9.81, 0.0, 0.0),
                fixture_description="assembled scene; fixed push force = twice initial assumed mass times gravity",
            )))
        tests = list(unsupported)
        for name, protocol in protocols:
            if (asset_id, name) in frozen_tests:
                protocol = PhysicsProtocol(**frozen_tests[asset_id, name])
            try:
                remaining = settings.physics_timeout_seconds if deadline is None else deadline - time.monotonic()
                if remaining <= 0:
                    tests.append({"name": name, "family": protocol.family.value,
                                  "status": "not_run", "issues": ["task wall time budget exhausted"]})
                    continue
                task = PhysicsTask(scene_usd=scene / "scene.usda",
                                   output_dir=path / asset_id / name,
                                   python_executable=settings.isaac_python,
                                   protocol=protocol,
                                   timeout_s=min(settings.physics_timeout_seconds, remaining))
                result = run_physics_task(task)
                tests.append({"name": name, "family": protocol.family.value,
                              "protocol": protocol.as_dict(),
                              "status": result.status, "issues": list(result.issues),
                              "report": str(result.report_path) if result.report_path else None,
                              "report_sha256": result.report_sha256})
            except Exception as error:
                tests.append({"name": name, "family": protocol.family.value,
                              "protocol": protocol.as_dict(),
                              "status": "failed", "issues": [f"{type(error).__name__}: {error}"],
                              "report": None, "report_sha256": None})
            write_json(path / "physics.json", {"status": "running", "results": output_rows + [
                {"asset_id": asset_id, "status": "running", "tests": tests}]})
        status = ("passed" if tests and all(test["status"] == "passed" for test in tests)
                  else "failed" if any(test["status"] == "failed" for test in tests)
                  else "unsupported")
        failed_report = next((test for test in tests if test["status"] == "failed"
                              and test.get("report")), None)
        output_rows.append({"asset_id": asset_id, "status": status, "tests": tests,
                            "issues": [f"{test['name']}: {issue}" for test in tests
                                       for issue in test.get("issues", [])],
                            "report": failed_report["report"] if failed_report else None,
                            "report_sha256": failed_report["report_sha256"] if failed_report else None})
        write_json(path / "physics.json", {"status": "running", "results": output_rows})
    passed = bool(output_rows) and all(r["status"] == "passed" for r in output_rows)
    write_json(path / "physics.json", {"status": "passed" if passed else "unverified",
                                       "results": output_rows})
    return "physics.json"


def _background(path: Path, perception: Path, designs: Path, config,
                settings: HarnessSettings, deadline: float):
    from ..construction.background import prepare_background
    from ..construction.reconstruction import estimate_background_cameras, train_background

    path.mkdir(parents=True, exist_ok=False)
    report = {"status": "not_run", "stages": {}, "physical_collision": False}
    if settings.background_generation_config:
        report["generation_requested"] = True
        report["generation"] = {"status": "not_run"}
    if settings.background_mode == "off":
        write_json(path / "background_result.json", report)
        return "background_result.json"
    try:
        prepare_background(perception, path / "prepared", config.background, designs)
        report["stages"]["masks"] = "completed"
        report["status"] = "masks_only"
        scene = read_json(perception / "scene.json")
        if (settings.background_mode == "auto" and scene.get("kind") == "video"
                and len(scene.get("frames", [])) >= 3
                and shutil.which(config.background.colmap)
                and time.monotonic() + 300 < deadline):
            budget = max(60, min(config.background.timeout_seconds, int(deadline - time.monotonic() - 60)))
            camera_config = config.background.model_copy(update={"timeout_seconds": budget})
            estimate_background_cameras(path / "prepared", path / "cameras", camera_config)
            report["stages"]["colmap"] = "completed"
            report["status"] = "cameras_estimated"
            gaussian_root = Path(config.background.gaussian_repo)
            cuda_available = False
            try:
                import torch

                cuda_available = torch.cuda.is_available()
            except ImportError:
                pass
            if ((gaussian_root / "gaussian_renderer/__init__.py").is_file()
                    and cuda_available and time.monotonic() + 300 < deadline):
                budget = max(60, min(config.background.timeout_seconds, int(deadline - time.monotonic() - 60)))
                training_config = config.background.model_copy(update={"timeout_seconds": budget})
                train_background(path / "cameras", path / "gaussians", training_config)
                report["stages"]["3dgs"] = "completed"
                report["status"] = "appearance_trained"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["status"] = "partial" if report["stages"] else "failed"
    # Completion uses prepared masks even when optional COLMAP/GS is unavailable.
    if settings.background_generation_config and report["stages"].get("masks") == "completed":
        try:
            from ..construction.background_generation import (
                complete_prepared_background,
                load_background_generation_config,
            )

            generation = load_background_generation_config(settings.background_generation_config)
            remaining = int(deadline - time.monotonic())
            if remaining < 10:
                raise RuntimeError("wall-time budget exhausted before optional background generation")
            generation = generation.model_copy(update={"timeout_seconds": min(generation.timeout_seconds, remaining)})
            report["generation"] = complete_prepared_background(path / "prepared", path / "generated", generation)
            report["stages"]["local_generation"] = "completed"
        except Exception as error:
            report["generation"] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        # A generated image is delivered separately; no silent scene/physics replacement.
        report["generation"]["scene_integration"] = "not_run; use explicit fixed-view background display"
    write_json(path / "background_result.json", report)
    return "background_result.json"


def _package(path: Path, request: HarnessRequest, stages: dict[str, Path]):
    path.mkdir(parents=True, exist_ok=False)
    assets = read_json(stages["assets"] / "assets.json")
    scene = read_json(stages["scene"] / "scene_result.json")
    physics = read_json(stages["physics"] / "physics.json")
    physical_status = {item["asset_id"]: item["status"] for item in physics.get("results", [])}
    review_flags = []
    for asset in assets["objects"]:
        if asset.get('assembly_status') not in {None, 'not_applicable'}:
            review_flags.append({'code': 'assembly_dynamics_unverified', 'asset_id': asset['asset_id'],
                                 'reason': 'Connection geometry checked; preload, release and load transfer not verified.'})
        if asset.get("build_status") == "visual_review_passed":
            asset["physics_status"] = physical_status.get(asset["asset_id"], physics["status"])
    background = read_json(stages["background"] / "background_result.json")
    if background.get("generation_requested"):
        generated = background.get("generation", {})
        if generated.get("status") != "generated":
            review_flags.append({"code": "background_generation_incomplete", "status": generated.get("status")})
        else:
            review_flags.append({"code": "generated_background_visual_review_pending",
                                 "reason": "2D appearance candidate; not loaded or reviewed as a complete environment."})
    research = read_json(stages["research"] / "research.json")
    task_scope = read_json(stages["task_scope"] / "task_scope.json")
    design = read_json(stages["design"] / "designs.json")
    scope_plan = read_json(stages["scope"] / "scope_plan.json")
    perception = read_json(stages["perception"] / "scene.json")
    perception_run = read_json(stages["perception"] / "run.json")
    design_run = read_json(stages["design"] / "run.json")
    quality = read_json(relative_file(stages["perception"], perception["quality_ref"]))
    evidence_index = read_json(stages["evidence"] / "objects.json")
    if not scope_plan.get("all_resolved"):
        review_flags.append({"code": "object_scope_unresolved"})
    if perception_run.get("execution_mode") == "replay":
        review_flags.append({"code": "replayed_perception", "reason": "fixture output is not a live model result"})
    if design_run.get("model_execution") == "replayed":
        review_flags.append({"code": "replayed_design", "reason": "fixture output is not a live model result"})
    for issue in quality.get("issues", []):
        if issue.get("code") in {"cross_image_identity_unverified", "cross_image_identity_ambiguous",
                                 "identity_conflict", "reference_interpretation_unresolved"}:
            review_flags.append(issue)
    for issue in scene.get("issues", []):
        review_flags.append({"code": "scene_layout_review", "issue": issue})
    for entry in evidence_index["objects"]:
        if entry["status"] in {"blocked_identity", "insufficient_evidence"}:
            review_flags.append({"code": entry["status"], "asset_id": entry["asset_id"]})
    for entry in design["objects"]:
        if entry["status"] == "routed" and entry.get("route") not in {"background", "exclude"}:
            review_flags.append({"code": "design_route_review", "asset_id": entry["asset_id"],
                                 "route": entry.get("route"), "reason": entry.get("reason")})
    for asset in assets["objects"]:
        for note in asset.get("physics_notes", []):
            if any(marker in note for marker in ("convex_hull_fallback", "open mesh collider")):
                review_flags.append({"code": "collision_geometry_review", "asset_id": asset["asset_id"],
                                     "note": note})
    for object_id, record in research.get("objects", {}).items():
        if record.get("status") == "conflict":
            review_flags.append({"code": "external_source_conflict", "object_id": object_id})
    complete = (assets["all_visual_reviews_passed"] and scene.get("status") == "assembled"
                and physics.get("status") == "passed" and design["all_designs_ready"]
                and task_scope.get("status") == "passed"
                and not review_flags)
    report = {"status": "verified" if complete else "unverified",
              "scene_id": request.scene_id, "task": request.task,
              "scene_usd": str((stages["scene"] / "scene.usda").resolve()) if scene.get("status") == "assembled" else None,
              "assets": assets["objects"], "physics": physics,
              "background": background,
              "task_scope": task_scope,
              "object_scope": scope_plan,
              "review_flags": review_flags,
              "web_sources": {key: {"status": value["status"], "sources": value.get("sources", []),
                                      "claims": value.get("claims", [])}
                              for key, value in research.get("objects", {}).items()},
              "known_limits": ["Dimensions and placement may be estimates rather than measured geometry.",
                               "A generic interaction test does not validate robot task completion.",
                               "Unsupported interaction families remain unverified."]}
    write_json(path / "delivery.json", report)
    return "delivery.json"


def run_harness(request: HarnessRequest, settings: HarnessSettings, output: Path) -> dict:
    """Run or resume a task. A stage completes only after its persisted output exists."""
    output = output.resolve()
    fingerprint = _fingerprint(request, settings)
    public_settings = {k: v for k, v in settings.model_dump(mode="json").items()
                       if k not in {"perception_config", "construction_config"}}
    if output.exists():
        state = read_json(output / "state.json")
        if (state["fingerprint"] != fingerprint
                or state["request"] != request.model_dump(mode="json")
                or state.get("settings") != public_settings):
            raise HarnessFailure("request or configured input changed; start a new run")
    else:
        output.mkdir(parents=True)
        state = {"schema_version": "roboenv.harness.v1",
                 "request": request.model_dump(mode="json"),
                 "settings": public_settings,
                 "fingerprint": fingerprint, "stages": {}, "status": "running",
                 "started_unix": time.time()}
        _write_state(output, state)
    deadline = time.monotonic() + settings.max_wall_seconds
    perception_config = load_config(settings.perception_config)
    construction_config = load_construction_config(settings.construction_config)
    paths = {}
    assembly_overrides = read_json(request.assembly_overrides) if request.assembly_overrides else {}
    assembly_required = bool(ASSEMBLY_INTENT.search(request.task))
    try:
        paths["perception"] = _stage(output, state, "perception",
                                     lambda path: _perception(path, request, perception_config))
        paths["evidence"] = _stage(output, state, "evidence",
                                   lambda path: (prepare_objects(paths["perception"], path,
                                                                 construction_config.evidence), "objects.json")[1])
        from .scope_plan import plan_object_scope

        paths["scope"] = _stage(output, state, "scope",
                                lambda path: (plan_object_scope(request.task, paths["evidence"],
                                                                construction_config.vlm, path),
                                              "scope_plan.json")[1])
        policy = WebSearchPolicy(enabled=settings.web_search, provider=settings.web_provider,
                                 dns_resolver=settings.web_dns_resolver,
                                 searxng_endpoint=settings.searxng_endpoint,
                                 allow_category_search=settings.allow_category_search,
                                 official_domains=tuple(settings.official_domains),
                                 allowed_source_domains=tuple(settings.allowed_source_domains))
        paths["research"] = _stage(output, state, "research",
                                   lambda path: research_objects(paths["evidence"], path,
                                                                 construction_config.vlm, policy,
                                                                 task=request.task,
                                                                 object_scope=paths["scope"] / "scope_plan.json").name)
        paths["design"] = _stage(output, state, "design",
                                 lambda path: (design_objects(paths["evidence"], path,
                                                              construction_config,
                                                              paths["research"] / "research.json",
                                                              task=request.task,
                                                              object_scope=paths["scope"] / "scope_plan.json"), "designs.json")[1])
        paths["assets"] = _stage(output, state, "assets",
                                 lambda path: _build_assets(path, paths["design"], paths["evidence"],
                                                            paths["research"], settings,
                                                            construction_config, deadline,
                                                            assembly_required=assembly_required,
                                                            assembly_overrides=assembly_overrides))
        if settings.enable_scene_assembly:
            paths["scene"] = _stage(output, state, "scene",
                                    lambda path: _assemble(path, paths["assets"], paths["evidence"], request))
        else:
            def no_scene(path):
                path.mkdir(parents=True, exist_ok=False)
                write_json(path / "scene_result.json", {"status": "not_run"})
                return "scene_result.json"
            paths["scene"] = _stage(output, state, "scene", no_scene)
        if settings.enable_physics:
            paths["physics"] = _stage(output, state, "physics",
                                      lambda path: _verify(path, paths["scene"], paths["assets"], settings, deadline))
        else:
            def no_physics(path):
                path.mkdir(parents=True, exist_ok=False)
                write_json(path / "physics.json", {"status": "not_run", "results": []})
                return "physics.json"
            paths["physics"] = _stage(output, state, "physics", no_physics)
        initial_physics = paths["physics"]
        for revision in range(settings.max_physics_revisions):
            report = read_json(paths["physics"] / "physics.json")
            if report["status"] == "passed" or time.monotonic() >= deadline:
                break
            feedback = {item["asset_id"]: item.get("issues", [])
                        for item in report.get("results", [])
                        if item.get("status") == "failed" and item.get("report")}
            if not feedback:
                break
            suffix = f"repair-{revision + 1:02d}"
            previous_assets = paths["assets"]
            paths["assets"] = _stage(output, state, f"assets-{suffix}",
                                     lambda path: _build_assets(path, paths["design"], paths["evidence"],
                                                                paths["research"], settings,
                                                                construction_config, deadline,
                                                                previous_assets, feedback,
                                                                assembly_required=assembly_required,
                                                                assembly_overrides=assembly_overrides))
            paths["scene"] = _stage(output, state, f"scene-{suffix}",
                                    lambda path: _assemble(path, paths["assets"], paths["evidence"], request))
            paths["physics"] = _stage(output, state, f"physics-{suffix}",
                                      lambda path: _verify(path, paths["scene"], paths["assets"], settings,
                                                           deadline, initial_physics))
        paths["background"] = _stage(output, state, "background",
                                     lambda path: _background(path, paths["perception"], paths["design"],
                                                              construction_config, settings, deadline))
        from .task_scope import audit_task_scope

        def scope(path):
            try:
                audit_task_scope(request.task, paths["design"], paths["assets"], paths["physics"],
                                 construction_config.vlm, path, scene=paths["scene"])
            except Exception as error:
                write_json(path / "task_scope.json", {"status": "unverified", "passed": False,
                           "original_task": request.task, "requirements": [],
                           "reason": f"task coverage audit unavailable: {type(error).__name__}: {error}"})
            return "task_scope.json"

        paths["task_scope"] = _stage(output, state, "task_scope", scope)
        def package(path):
            if _fingerprint(request, settings) != fingerprint:
                raise HarnessFailure("input, configuration or controller changed during the run; start a new run")
            return _package(path, request, paths)

        paths["delivery"] = _stage(output, state, "delivery", package)
        delivery = read_json(paths["delivery"] / "delivery.json")
        state["status"] = delivery["status"]
        state["completed_unix"] = time.time()
        state["delivery_path"] = str((paths["delivery"] / "delivery.json").resolve())
        _write_state(output, state)
        return {**delivery, "delivery_path": state["delivery_path"]}
    except Exception as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        _write_state(output, state)
        write_json(output / "failure.json", {
            "status": "failed", "scene_id": request.scene_id, "task": request.task,
            "error": state["error"], "stages": state["stages"],
            "scene_usd": None, "physics_status": "unverified",
            "state_ref": "state.json",
            "scope": "Partial artifacts remain available in the recorded stage directories.",
        })
        raise
