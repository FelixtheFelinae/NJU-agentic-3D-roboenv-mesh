"""Bounded component adapters. Reuse domain implementations, never run a model loop."""

import json
import shutil
import time
from pathlib import Path

from ..construction.assembly_blueprint import AssemblyBlueprint, parse_blueprint
from ..construction.assembly_design import ASSEMBLY_INSTRUCTION, author_assembly, selfcheck_assembly
from ..construction.common import verify_run
from ..construction.contracts import AssetBrief, DesignReview, load_construction_config
from ..construction.design import (
    DESIGN_REVIEW_SYSTEM,
    DESIGN_SYSTEM,
    infer_model,
    validate_blueprint_evidence,
)
from ..execution.context import span
from ..io import load_config, read_json, relative_file, sha256, write_json
from ..vlm import make_vlm
from .catalog import CATALOG
from .sources import source_path


def observation(evidence, object_id):
    index = verify_run(evidence, "objects.json")
    rows = [r for r in index["objects"] if object_id in {r["asset_id"], r["object_id"]}]
    if len(rows) != 1:
        raise ValueError("请明确选择一个目标对象（asset_id 或 object_id）")
    row = rows[0]
    data = read_json(relative_file(evidence, row["evidence_ref"]))
    if data.get("status") != "ready_for_design":
        raise ValueError("对象证据不足或身份尚不明确，请先修正识别结果")
    images = [relative_file(evidence, v["context_ref"]) for v in data["views"]]
    return row, data, images


def claims_for(paths, object_id):
    return (
        read_json(paths["research"] / "research.json")
        .get("objects", {})
        .get(object_id, {})
        .get("claims", [])
        if "research" in paths
        else []
    )


def run(
    operation,
    sources,
    output,
    settings,
    task,
    *,
    inputs=(),
    params=None,
    object_id=None,
    origin="agent",
    workflow=False,
):
    from . import perception

    params = params or {}
    paths = {key: source_path(value) for key, value in sources.items()}
    exports, status = {}, "unverified"
    allowed = {
        "segment": {"candidates"},
        "perceive": {"sampling"} if workflow else set(),
        "perception_edit": {"frame_id", "merge", "exclude", "labels"},
        "scope_apply": {"objects"},
        "blueprint": {"blueprint", "measurements", "feedback"},
        "geometry": {"recipe", "feedback"},
        "gaussian": {"iterations", "resume"},
        "overview": {"frame_id"},
        "physics": {"assembly_validation"},
        "reference_fit": {"evidence_policy"},
        "reference_code": {"asset_id", "feedback"},
        "reference_validate": {"stage"},
        "reference_review": {"asset_id"},
    }.get(operation, set())
    if set(params) - allowed:
        raise ValueError("此操作包含未支持的参数：" + ", ".join(set(params) - allowed))
    if operation == 'reference_fit' and params.get('evidence_policy', 'historical_reference') not in {'historical_reference', 'video_only'}:
        raise ValueError('evidence_policy must be historical_reference or video_only')
    feedback = params.get("feedback", [])
    if (
        not isinstance(feedback, list)
        or len(feedback) > 16
        or any(not isinstance(v, str) or len(v) > 4000 for v in feedback)
    ):
        raise ValueError("修订反馈应为至多 16 项具体问题，每项不超过 4000 字符")
    perception_config = (
        load_config(settings.perception_config)
        if operation in {"inventory", "segment", "label", "perceive"}
        else None
    )
    config = (
        load_construction_config(settings.construction_config)
        if "construction_config" in CATALOG[operation][3]
        else None
    )
    output.mkdir(parents=True, exist_ok=True)
    with span(operation, object_id=object_id):
        if operation.startswith("reference_"):
            from .reference_workflow import execute

            return execute(operation, paths, output, settings, params, feedback)
        elif operation == "ingest":
            perception.ingest(inputs, output / "images")
            exports = {"images": "images"}
        elif operation == "inventory":
            perception.inventory(paths["images"], output / "inventory", perception_config, task)
            exports = {"inventory": "inventory"}
        elif operation == "segment":
            perception.segment(
                paths["images"],
                paths["inventory"],
                output / "masks",
                perception_config,
                params.get("candidates"),
            )
            exports = {"masks": "masks"}
        elif operation == "label":
            perception.label(
                paths["images"], paths["masks"], output / "perception", perception_config, task
            )
            exports = {"perception": "perception"}
        elif operation == "perceive":
            from ..harness.media import run_images
            from ..pipeline import run_image
            from ..video_pipeline import run_video

            videos = [
                r
                for r in inputs
                if Path(r["name"]).suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
            ]
            if videos:
                if len(inputs) != 1:
                    raise ValueError("视频与图片不能混合")
                if "sampling" in params:
                    from ..contracts import VideoConfig

                    video_config = VideoConfig.model_validate({
                        **perception_config.video.model_dump(), **params["sampling"],
                    })
                    perception_config = perception_config.model_copy(update={"video": video_config})
                run_video(
                    Path(videos[0]["path"]),
                    output / "perception",
                    perception_config,
                    "studio",
                    task,
                )
            elif len(inputs) == 1:
                run_image(
                    Path(inputs[0]["path"]),
                    output / "perception",
                    perception_config,
                    "studio",
                    task,
                )
            else:
                run_images(
                    [Path(r["path"]) for r in inputs],
                    output / "perception",
                    perception_config,
                    "studio",
                    task,
                )
            exports = {"perception": "perception"}
        elif operation == "perception_edit":
            perception.edit(paths["perception"], output / "perception", params, origin)
            exports = {"perception": "perception"}
        elif operation == "evidence":
            from ..construction.evidence import prepare_objects

            prepare_objects(paths["perception"], output / "evidence", config.evidence)
            exports = {"evidence": "evidence"}
        elif operation == "scope":
            from ..harness.scope_plan import plan_object_scope

            result = plan_object_scope(task, paths["evidence"], config.vlm, output / "scope")
            status = "review_passed" if result["all_resolved"] else "unverified"
            exports = {"scope": "scope"}
        elif operation == "scope_apply":
            from ..harness.scope_plan import ObjectPlan

            plan = ObjectPlan.model_validate({"objects": params.get("objects", [])})
            ids = {r["asset_id"] for r in verify_run(paths["evidence"], "objects.json")["objects"]}
            if len(plan.objects) != len(ids) or {r.asset_id for r in plan.objects} != ids:
                raise ValueError("范围修正必须包含每个现有对象，不能重复或遗漏")
            resolved = all(r.action != "unresolved" for r in plan.objects)
            write_json(
                output / "scope/scope_plan.json",
                {
                    "status": "explicit_selection",
                    "all_resolved": resolved,
                    "objects": plan.model_dump(mode="json")["objects"],
                    "original_task": task,
                    "origin": origin,
                    "independent_review": "not_run",
                    "attempts": [],
                },
            )
            exports = {"scope": "scope"}
            status = "explicit_selection" if resolved else "unverified"
        elif operation == "research":
            from ..harness.research import research_objects
            from ..harness.web import WebSearchPolicy

            research_objects(
                paths["evidence"],
                output / "research",
                config.vlm,
                WebSearchPolicy(enabled=settings.web_search),
                task=task,
                object_scope=paths["scope"] / "scope_plan.json",
            )
            exports = {"research": "research"}
        elif operation == "blueprint":
            row, data, images = observation(paths["evidence"], object_id)
            scope = read_json(paths["scope"] / "scope_plan.json")
            selected = next((r for r in scope["objects"] if r["asset_id"] == row["asset_id"]), {})
            if not scope.get("all_resolved") or selected.get("action") != "build":
                raise ValueError("该对象尚未确定为重建目标，请先解决范围选择")
            measurements = params.get("measurements", {})
            if any(
                k not in {"measurement:x", "measurement:y", "measurement:z"}
                or not isinstance(v, (float, int))
                or isinstance(v, bool)
                or not 0 < v < 100
                for k, v in measurements.items()
            ):
                raise ValueError("用户尺寸需以 measurement:x/y/z 提供正数米值")
            claims = claims_for(paths, data["object_id"])
            context = {
                "task": task,
                "previous_findings": feedback,
                "quantity_contract": {
                    "format": "All Quantity fields are objects, never null. Unknown uses {value:null, source:'unknown', evidence_refs:[], rationale:'...'}.",
                    "provenance": "Every non-null value, including estimated/assumed dimensions and joint limits, needs actual supplied view/source refs. A view supports the assumption context, not a measured number.",
                    "motion": "Unknown rest and travel remain null in descriptions. Separate reference and observed states; never invent observed motion or measurements.",
                },
                "evidence": data,
                "external_claims": claims,
                "measurements": measurements,
                "previous": read_json(paths["previous"]) if "previous" in paths else None,
            }
            if "previous" in paths:
                from ..construction.assembly_readiness import analyze_readiness

                context["previous_readiness"] = analyze_readiness(parse_blueprint(context["previous"]))
            if 'geometric_verification' in paths:
                from ..harness.geometric_verifier import bound_feedback

                if 'previous' not in paths:
                    raise ValueError('几何检查反馈需同时提供对应的 previous 蓝图')
                context['geometric_feedback'] = bound_feedback(
                    read_json(paths['geometric_verification'] / 'geometric_verification.json'),
                    parse_blueprint(read_json(paths['previous'])),
                )
            author = make_vlm(config.vlm, system=DESIGN_SYSTEM)
            intake_feedback = ""
            intake_refs = {v["ref_id"] for v in data["views"]} | {
                c["source_id"] for c in claims if c.get("status") == "candidate"
            }
            for attempt in range(config.design.max_revisions + 1):
                suffix = "" if attempt == 0 else f"-{attempt}"
                try:
                    brief = infer_model(
                        author,
                        "component-intake" + suffix,
                        "Understand this ONE object; do not decompose yet. Copy this exact "
                        f"object_id verbatim: {json.dumps(data['object_id'])}. "
                        "asset_id is a directory label, not object identity. Distinguish "
                        "viewpoints from observed motion. Preserve unknowns.\n"
                        + json.dumps(context, ensure_ascii=False)
                        + ("\nRequired correction: " + intake_feedback if intake_feedback else ""),
                        images,
                        AssetBrief,
                        output / ("blueprint/intake" + suffix),
                    )
                    if brief.object_id != data["object_id"]:
                        raise ValueError("Intake must copy evidence.object_id exactly")
                    if not set(brief.evidence_refs) <= intake_refs:
                        raise ValueError("Intake references evidence not supplied for this object")
                    break
                except ValueError as error:
                    intake_feedback = str(error)
                    write_json(output / ("blueprint/intake" + suffix) / "rejected.json",
                               {"error": intake_feedback})
                    if attempt == config.design.max_revisions:
                        raise
            if brief.build_route not in {"rigid", "articulated"}:
                raise ValueError("该对象未被 Intake 选择为独立重建目标，请检查语义与任务")
            if params.get("blueprint"):
                blueprint = AssemblyBlueprint.model_validate(params["blueprint"])
            else:
                blueprint = author_assembly(
                    author, {**context, "asset_brief": brief.model_dump(mode="json"),
                             "assembly_instruction": ASSEMBLY_INSTRUCTION}, images,
                    output / "blueprint", schema_revisions=1 if workflow else 0,
                )
            validate_blueprint_evidence(
                blueprint,
                brief,
                data,
                measurements,
                [c["source_id"] for c in claims if c.get("status") == "candidate"],
            )
            checked = selfcheck_assembly(
                author, blueprint, images, output / "blueprint", stage="component-blueprint-selfcheck",
                brief=brief, allowed_refs={v["ref_id"] for v in data["views"]} | set(measurements)
                | {c["source_id"] for c in claims if c.get("status") == "candidate"},
                context=json.dumps(context, ensure_ascii=False),
                previous=parse_blueprint(context["previous"]) if context["previous"] else None,
            )
            if checked["status"] == "needs_revision" and not workflow:
                raise ValueError("装配描述自检查需要修订：" + "; ".join(checked["issues"]))
            status = checked["status"]
            write_json(output / "blueprint/identity.json", row)
            exports = {"blueprint": "blueprint/blueprint.json"}
        elif operation == "blueprint_review":
            _, data, images = observation(paths["evidence"], object_id)
            blueprint = parse_blueprint(read_json(paths["blueprint"]))
            if blueprint.object_id != data["object_id"]:
                raise ValueError("结构与对象身份不匹配")
            digest = sha256(paths["blueprint"])
            review = infer_model(
                make_vlm(config.vlm, system=DESIGN_REVIEW_SYSTEM),
                "component-blueprint-review",
                json.dumps(
                    {
                        "task": task,
                        "blueprint_sha256": digest,
                        "blueprint": blueprint.model_dump(mode="json"),
                        "evidence": data,
                    },
                    ensure_ascii=False,
                ),
                images,
                DesignReview,
                output / "review/trace",
            )
            if review.blueprint_sha256 != digest:
                raise ValueError("审核没有绑定当前结构方案")
            write_json(output / "review/review.json", review.model_dump(mode="json"))
            exports = {"blueprint_review": "review/review.json"}
            status = "review_passed" if review.decision == "PASS" else "unverified"
        elif operation == "geometry":
            from ..construction.assembly_readiness import require_readiness
            from ..harness.geometry import GeometryRecipe, infer_recipe

            _, data, images = observation(paths["evidence"], object_id)
            blueprint = parse_blueprint(read_json(paths["blueprint"]))
            if blueprint.object_id != data["object_id"]:
                raise ValueError("结构与对象身份不匹配")
            require_readiness(blueprint, output)
            claims = claims_for(paths, data["object_id"])
            geometric_feedback = []
            if 'geometric_verification' in paths:
                from ..harness.geometric_verifier import bound_feedback

                if 'previous' not in paths:
                    raise ValueError('几何检查反馈需同时提供对应的 previous 配方')
                geometric_feedback = bound_feedback(
                    read_json(paths['geometric_verification'] / 'geometric_verification.json'),
                    blueprint, GeometryRecipe.model_validate(read_json(paths['previous'])),
                )
            if params.get("recipe"):
                recipe = GeometryRecipe.model_validate(params["recipe"])
                recipe.check_against(
                    blueprint,
                    {v["ref_id"] for v in data["views"]}
                    | {c["source_id"] for c in claims if c.get("status") == "candidate"},
                )
                write_json(output / "geometry/recipe.json", recipe.model_dump(mode="json"))
            else:
                issues = ([task] if task else []) + feedback
                if "build_hypothesis" in paths:
                    from ..harness.workflow_hypothesis import bound_report

                    adopted = bound_report(paths["build_hypothesis"], paths["blueprint"])
                    issues.append("Construction-only adopted assumptions, never measurements: " +
                                  json.dumps(adopted["plan"], ensure_ascii=False))
                recipe = infer_recipe(
                    config.vlm,
                    blueprint,
                    data,
                    images,
                    output / "geometry",
                    issues,
                    external_claims=claims,
                    previous_recipe=read_json(paths["previous"]) if "previous" in paths else None,
                    geometric_feedback=geometric_feedback,
                    schema_revisions=1 if workflow else 0,
                )
            from ..harness.repair_policy import enforce_standard_recipe

            frozen = (
                enforce_standard_recipe(
                    GeometryRecipe.model_validate(read_json(paths["previous"])), {}
                )
                if "previous" in paths
                else {}
            )
            enforce_standard_recipe(recipe, frozen)
            write_json(
                output / "geometry/evidence_refs.json",
                {
                    "object_id": data["object_id"],
                    "refs": sorted(
                        {v["ref_id"] for v in data["views"]}
                        | {c["source_id"] for c in claims if c.get("status") == "candidate"}
                    ),
                },
            )
            exports = {"recipe": "geometry/recipe.json"}
        elif operation in {"assembly_audit", "geometry_audit", "build", "render", "geometry_review"}:
            exports, status = geometry_operation(
                operation, paths, output, settings, task, object_id, config, workflow=workflow
            )
        elif operation == "assets_merge":
            evidence = verify_run(paths["evidence"], "objects.json")
            identities = {r["asset_id"]: r["object_id"] for r in evidence["objects"]}
            rows = []
            for key, folder in paths.items():
                if key.startswith("assets_"):
                    rows.extend(read_json(folder / "assets.json")["objects"])
            ids = [r["asset_id"] for r in rows]
            if (
                not rows
                or len(ids) != len(set(ids))
                or any(identities.get(r["asset_id"]) != r["object_id"] for r in rows)
            ):
                raise ValueError("合并资产有重复对象或与当前证据身份不一致")
            write_json(
                output / "assets/assets.json",
                {
                    "objects": rows,
                    "all_visual_reviews_passed": all(
                        r.get("build_status") == "visual_review_passed" for r in rows
                    ),
                },
            )
            exports = {"assets": "assets"}
        elif operation in {"scene", "physics", "task_review", "export"}:
            exports, status = scene_operation(operation, paths, output, settings, task, params)
        elif operation == "background":
            from ..construction.background import prepare_background

            prepare_background(paths["perception"], output / "background", config.background)
            exports = {"background": "background"}
        elif operation == "cameras":
            from ..construction.reconstruction import estimate_background_cameras

            estimate_background_cameras(paths["background"], output / "cameras", config.background)
            exports = {"cameras": "cameras"}
        elif operation == "gaussian":
            from ..construction.reconstruction import train_background

            iterations = int(params.get("iterations", config.background.iterations))
            if not 1 <= iterations <= 100000:
                raise ValueError("训练迭代数需在 1 到 100000 之间")
            if params.get("resume") and "checkpoint" not in paths:
                raise ValueError("恢复训练必须提供当前完整检查点来源")
            train_background(
                paths["cameras"],
                output / "gaussian",
                config.background.model_copy(update={"iterations": iterations}),
                **(
                    {"resume": paths["checkpoint"]}
                    if params.get("resume") and "checkpoint" in paths
                    else {}
                ),
            )
            exports = {"gaussian": "gaussian"}
        elif operation == "overview":
            from .overview import render_overview

            render_overview(
                paths["gaussian"],
                paths["cameras"],
                output / "overview",
                config.background,
                params.get("frame_id"),
            )
            exports = {"overview": "overview"}
        else:
            raise ValueError("未实现的组件操作")
    for value in sources.values():
        source_path(value)
    return exports, status


def geometry_operation(operation, paths, output, settings, task, object_id, config, *, workflow=False):
    from ..harness.assembly import ASSEMBLY_INTENT, persist_audit, require_audit
    from ..harness.assembly_adapter import candidate_hashes, recipe_parts
    from ..harness.connection_compiler import compile_plan
    from ..harness.geometric_verifier import persist_verification
    from ..harness.geometry import GeometryRecipe, _preview_offsets, build_recipe, review_geometry

    blueprint = parse_blueprint(read_json(paths["blueprint"]))
    recipe = GeometryRecipe.model_validate(read_json(paths["recipe"]))
    adopted = None
    if "build_hypothesis" in paths:
        from ..harness.workflow_hypothesis import audit, bound_report

        adopted = bound_report(paths["build_hypothesis"], paths["blueprint"])
        conformance = audit(blueprint, recipe, adopted["plan"])
        if operation == "build":
            write_json(output / "build_hypothesis_audit.json", conformance)
            if conformance["status"] != "passed":
                raise ValueError("配方未通过构建假设审核，禁止构建")
    # Validate even explicitly supplied recipes against the actual blueprint.
    allowed = (
        set(read_json(paths["recipe"].parent / "evidence_refs.json").get("refs", []))
        if (paths["recipe"].parent / "evidence_refs.json").is_file()
        else set()
    )
    if "evidence" in paths:
        _, evidence, _ = observation(paths["evidence"], object_id)
        allowed.update(v["ref_id"] for v in evidence["views"])
        allowed.update(
            c["source_id"]
            for c in claims_for(paths, evidence["object_id"])
            if c.get("status") == "candidate"
        )
    if not allowed:
        raise ValueError("缺少几何证据目录；历史 recipe 需提供 evidence 来源")
    recipe.check_against(blueprint, allowed)
    if operation == 'geometry_audit':
        result = persist_verification(output / 'geometric_check', blueprint, recipe)
        return {'geometric_verification': 'geometric_check'}, result['status']
    if operation == "assembly_audit":
        result = persist_audit(
            output / "assembly",
            recipe_parts(blueprint, recipe),
            recipe.assembly,
            allowed_refs=allowed,
            required=bool(ASSEMBLY_INTENT.search(task)),
            candidate_hashes=candidate_hashes(blueprint, recipe),
            require_pass=not workflow,
        )
        compiled = (
            compile_plan(recipe.assembly, recipe_parts(blueprint, recipe), allowed_refs=allowed)
            if result["status"] != "failed" else None
        )
        if compiled:
            write_json(output / "assembly/compiled_assembly.json", compiled)
        return {"assembly": "assembly"}, result.get("status", "unverified")
    if operation == "build":
        if isinstance(blueprint, AssemblyBlueprint):
            persist_verification(output / 'geometric_check', blueprint, recipe, require_pass=True)
        if "blueprint_review" in paths:
            review = read_json(paths["blueprint_review"])
            if (
                review.get("blueprint_sha256") != sha256(paths["blueprint"])
                or review.get("decision") != "PASS"
            ):
                raise ValueError("结构独立审核尚未通过或不对应当前版本")
        parts = recipe_parts(blueprint, recipe)
        if (recipe.assembly or ASSEMBLY_INTENT.search(task) or "assembly" in paths
                or any(p["category"] == "fastener" for p in parts)):
            if "assembly" not in paths:
                raise ValueError("装配对象构建前必须执行 assembly_audit")
            report = read_json(paths["assembly"] / "assembly_validation.json")
            require_audit(report, parts, recipe.assembly,
                          allowed_refs=allowed, required=bool(ASSEMBLY_INTENT.search(task)),
                          candidate_hashes=candidate_hashes(blueprint, recipe))
        build_recipe(
            blueprint,
            recipe,
            output / "built",
            artisan_python=settings.artisan_python,
            blender=settings.blender,
            timeout_seconds=min(settings.max_wall_seconds, 900),
        )
        if adopted:
            candidate_path = output / "built/candidate.json"
            candidate = read_json(candidate_path)
            candidate["construction_hypothesis"] = adopted
            candidate["source_description_status"] = "canonical_unknowns_preserved"
            write_json(candidate_path, candidate)
        return {"built": "built"}, "unverified"
    if operation == "render":
        from ..construction.blender_backend import build_blender_meshes

        build_blender_meshes(
            Path(__file__).parents[1] / "harness/blender_recipe.py",
            output / "renders",
            parameters={
                **recipe.model_dump(mode="json"),
                "preview_offsets": _preview_offsets(blueprint, recipe),
            },
            blender=settings.blender,
            timeout=min(settings.max_wall_seconds, 900),
        )
        return {"renders": "renders"}, "unverified"
    row, evidence, images = observation(paths["evidence"], object_id)
    candidate = read_json(paths["built"] / "candidate.json")
    review = review_geometry(
        config.vlm, blueprint, evidence, recipe, images, candidate, output / "review"
    )
    passed = review.decision == "PASS"
    asset = {
        "asset_id": row["asset_id"],
        "object_id": row["object_id"],
        "build_status": "visual_review_passed" if passed else "unverified",
        "usd": candidate["usd"],
        "usd_sha256": candidate["usd_sha256"],
        "model_ref": str(paths["built"] / "model.py"),
        "recipe_ref": str(paths["recipe"]),
        "preview_refs": candidate["visual_previews"],
        "physics_status": "not_run",
        "review": review.model_dump(mode="json"),
    }
    for key in ("compiled_assembly_ref", "standard_bundle", "physics_notes",
                "construction_hypothesis", "source_description_status"):
        if key in candidate:
            asset[key] = candidate[key]
    write_json(
        output / "assets/assets.json", {"objects": [asset], "all_visual_reviews_passed": passed}
    )
    return {"assets": "assets"}, "review_passed" if passed else "unverified"


def scene_operation(operation, paths, output, settings, task, params):
    from ..harness.run import HarnessRequest, HarnessSettings, _assemble, _verify

    # Existing scene helpers accept a request; the placeholder is metadata only, never ingested.
    request = HarnessRequest(task=task or "检查当前资产", images=[output / "unused"])
    if operation == "scene":
        _assemble(
            output / "scene",
            paths["assets"],
            paths["evidence"],
            request,
            usd_python=settings.artisan_python,
        )
        result = read_json(output / "scene/scene_result.json")
        return {"scene": "scene"}, "unverified" if result.get(
            "status"
        ) == "assembled" else "blocked"
    if operation == "physics":
        mode = params.get("assembly_validation", "off")
        if mode not in {"off", "preflight", "functional"}:
            raise ValueError("未支持的装配验证模式")
        cfg = HarnessSettings(
            perception_config=settings.perception_config or output / "unused",
            construction_config=settings.construction_config or output / "unused",
            artisan_python=settings.artisan_python or output / "unused",
            isaac_python=settings.isaac_python,
            assembly_validation=mode,
            physics_timeout_seconds=min(max(settings.max_wall_seconds, 30), 7200),
        )
        _verify(
            output / "physics",
            paths["scene"],
            paths["assets"],
            cfg,
            time.monotonic() + settings.max_wall_seconds,
        )
        report = read_json(output / "physics/physics.json")
        return {"physics": "physics"}, "physics_passed" if report["status"] == "passed" else report[
            "status"
        ]
    if operation == "task_review":
        from ..harness.task_scope import audit_task_scope

        config = load_construction_config(settings.construction_config)
        blueprint = parse_blueprint(read_json(paths["blueprint"]))
        rows = read_json(paths["assets"] / "assets.json")["objects"]
        row = next((r for r in rows if r["object_id"] == blueprint.object_id), None)
        if row is None:
            raise ValueError("任务审核与资产版本不匹配")
        design = output / "design"
        folder = design / "objects" / row["asset_id"]
        folder.mkdir(parents=True)
        shutil.copy2(paths["blueprint"], folder / "blueprint.json")
        write_json(
            design / "designs.json",
            {"objects": [{**row, "status": "design_ready"}], "all_designs_ready": True},
        )
        audit_task_scope(
            task,
            design,
            paths["assets"],
            paths["physics"],
            config.vlm,
            output / "review",
            scene=paths.get("scene"),
        )
        report = read_json(output / "review/task_scope.json")
        return {"task_review": "review"}, "review_passed" if report.get("passed") else "unverified"
    from ..integration.bundle import export_bundle

    scene_result = read_json(paths["scene"] / "scene_result.json")
    if scene_result.get("status") != "assembled":
        raise ValueError("场景尚未生成，不能导出")
    physics = (
        read_json(paths["physics"] / "physics.json")
        if "physics" in paths
        else {"status": "not_run"}
    )
    write_json(
        output / "delivery.json",
        {
            "status": "unverified",
            "task": task,
            "scene_usd": str(paths["scene"] / scene_result["scene_ref"]),
            "assets": read_json(paths["assets"] / "assets.json")["objects"],
            "physics": physics,
            "scope": "asset package; inspect bound validation conditions; no robot execution",
        },
    )
    export_bundle(output / "delivery.json", output / "bundle", settings.artisan_python)
    return {"delivery": "bundle"}, "unverified"
