import pytest

from roboenv_perception.contracts import VLMConfig
from roboenv_perception.harness.task_scope import audit_task_scope
from roboenv_perception.io import sha256, write_json


def _run(tmp_path, *, task="Push the box horizontally", family="rigid", status="passed",
         refs=None, no_omissions=True, corrupt=False):
    designs, assets, physics, replay = [tmp_path / name for name in
                                         ("designs", "assets", "physics", "replay")]
    designs.mkdir()
    write_json(designs / "objects/box/asset_brief.json", {"purpose": "box"})
    write_json(designs / "objects/box/blueprint.json", {"parts": []})
    write_json(assets / "assets.json", {"objects": [{"asset_id": "box"}]})
    report = physics / "box/report.json"
    write_json(report, {"status": "passed"})
    write_json(physics / "physics.json", {"results": [{"asset_id": "box", "tests": [{
        "name": "push", "family": family, "status": status,
        "report": str(report), "report_sha256": "bad" if corrupt else sha256(report)}]}]})
    write_json(replay / "task-scope-audit.json", {
        "requirements": [{"requirement": "Horizontal push", "asset_id": "box",
                          "family": "rigid", "supporting_tests": refs if refs is not None else ["box/push"],
                          "reason": "Protocol evidence", "status": "covered"}],
        "no_omissions": no_omissions, "reason": "Reviewed original task"})
    return audit_task_scope(task, designs, assets, physics,
                            VLMConfig(backend="replay", replay_dir=str(replay)), tmp_path / "output")


def test_covered_with_report_and_trace(tmp_path):
    assert _run(tmp_path)["passed"]
    assert (tmp_path / "output/trace/request.json").is_file()
    assert (tmp_path / "output/task_scope_input.json").is_file()


def test_riding_cannot_be_relabelled_as_push(tmp_path):
    result = _run(tmp_path, task="Build a bicycle for riding")
    assert not result["passed"]
    assert result["requirements"][-1]["status"] == "unsupported"


def test_missing_test(tmp_path):
    assert not _run(tmp_path, refs=["box/missing"])["passed"]


def test_wrong_family(tmp_path):
    assert not _run(tmp_path, family="hinge")["passed"]


def test_not_run(tmp_path):
    assert not _run(tmp_path, status="not_run")["passed"]


def test_report_hash_mismatch(tmp_path):
    assert not _run(tmp_path, corrupt=True)["passed"]


def test_omissions(tmp_path):
    assert not _run(tmp_path, no_omissions=False)["passed"]


def test_cross_asset_reference(tmp_path):
    assert not _run(tmp_path, refs=["other/push"])["passed"]


def _virtual_run(tmp_path, family, *, tamper=None):
    import hashlib
    import json

    from roboenv_perception.io import read_json

    _run(tmp_path)
    assets = tmp_path / "assets"
    designs = tmp_path / "designs"
    scene = tmp_path / "scene"
    asset = {"asset_id": "box", "object_id": "object-box"}
    ref = "box/visual-review"
    if family == "visual":
        built = assets / "box/candidate-000/built"
        built.mkdir(parents=True)
        for name in ("model.py", "recipe.json", "object.usd", "preview.png"):
            (built / name).write_text("fixture")
        candidate = {"usd": str(built / "object.usd"), "usd_sha256": sha256(built / "object.usd"),
                     "model_sha256": sha256(built / "model.py"),
                     "recipe_sha256": sha256(built / "recipe.json"),
                     "visual_previews": [str(built / "preview.png")]}
        candidate["candidate_sha256"] = hashlib.sha256(json.dumps({
            "model": candidate["model_sha256"], "recipe": candidate["recipe_sha256"],
            "usd": candidate["usd_sha256"], "previews": {"preview.png": sha256(built / "preview.png")},
        }, sort_keys=True).encode()).hexdigest()
        review = {"decision": "PASS", "candidate_sha256": candidate["candidate_sha256"]}
        write_json(built / "candidate.json", candidate)
        write_json(built.parent / "review/review.json", review)
        asset.update(build_status="visual_review_passed", selected_revision=0,
                     model_ref=str(built / "model.py"), usd=candidate["usd"],
                     usd_sha256=candidate["usd_sha256"], attempts=[{
                         "revision": 0, "status": "reviewed", "candidate": candidate, "review": review}])
        if tamper == "preview":
            (built / "preview.png").write_text("changed")
        if tamper == "review":
            write_json(built.parent / "review/review.json", {**review, "decision": "REDO"})
        if tamper == "selection":
            asset["selected_revision"] = 1
    elif family == "route":
        family = "scene"
        ref = "box/background-route"
        asset.update(build_status="routed", status="routed", route="background")
        design = dict(asset)
        if tamper:
            design["route"] = "articulated"
        write_json(designs / "designs.json", {"objects": [design]})
    else:
        ref = "scene/layout"
        scene.mkdir()
        (scene / "scene.usda").write_text("scene fixture")
        write_json(scene / "scene_result.json", {"status": "assembled", "scene_ref": "scene.usda",
                                                "scene_sha256": sha256(scene / "scene.usda"),
                                                "issues": ["unresolved"] if tamper == "issue" else []})
        write_json(scene / "layout.json", {"scene_ref": "scene.usda", "assets": []})
        if tamper == "scene":
            (scene / "scene.usda").write_text("changed")
    write_json(assets / "assets.json", {"objects": [asset]})
    replay_path = tmp_path / "replay/task-scope-audit.json"
    review = read_json(replay_path)
    review["requirements"][0].update(family=family, supporting_tests=[ref],
                                      asset_id=None if ref == "scene/layout" else "box")
    write_json(replay_path, review)
    return audit_task_scope("Reconstruct main appearance and assemble approximate scene", designs, assets,
                            tmp_path / "physics", VLMConfig(backend="replay", replay_dir=str(tmp_path / "replay")),
                            tmp_path / "output", scene=scene if scene.is_dir() else None)


def test_visual_review_bound_candidate(tmp_path):
    assert _virtual_run(tmp_path, "visual")["passed"]


def test_visual_changed_preview_rejected(tmp_path):
    assert not _virtual_run(tmp_path, "visual", tamper="preview")["passed"]


def test_visual_changed_review_rejected(tmp_path):
    assert not _virtual_run(tmp_path, "visual", tamper="review")["passed"]


def test_visual_wrong_selected_candidate_rejected(tmp_path):
    assert not _virtual_run(tmp_path, "visual", tamper="selection")["passed"]


def test_background_route_consistency(tmp_path):
    assert _virtual_run(tmp_path, "route")["passed"]


def test_background_route_mismatch_rejected(tmp_path):
    assert not _virtual_run(tmp_path, "route", tamper="mismatch")["passed"]


def test_scene_layout(tmp_path):
    assert _virtual_run(tmp_path, "scene")["passed"]


def test_scene_issue_rejected(tmp_path):
    assert not _virtual_run(tmp_path, "scene", tamper="issue")["passed"]


def test_scene_changed_usd_rejected(tmp_path):
    assert not _virtual_run(tmp_path, "scene", tamper="scene")["passed"]


def _grouped_background_run(tmp_path, *, invalid=None):
    _run(tmp_path)
    assets, designs = tmp_path / "assets", tmp_path / "designs"
    objects = [{"asset_id": f"bg-{i}", "object_id": f"object-{i}", "status": "routed",
                "build_status": "routed", "route": "background"} for i in range(4)]
    write_json(assets / "assets.json", {"objects": objects})
    design_objects = [dict(row) for row in objects]
    if invalid == "route":
        design_objects[2]["route"] = "articulated"
    write_json(designs / "designs.json", {"objects": design_objects})
    refs = [f"bg-{i}/background-route" for i in range(4)]
    if invalid == "duplicate":
        refs.append(refs[0])
    elif invalid == "unknown":
        refs.append("unknown/background-route")
    elif invalid in {"physical", "layout", "visual"}:
        refs.append({"physical": "box/push", "layout": "scene/layout",
                     "visual": "bg-0/visual-review"}[invalid])
    write_json(tmp_path / "replay/task-scope-audit.json", {
        "requirements": [{"requirement": "Treat laptop, mouse and other objects as background",
                          "asset_id": "bg-0" if invalid == "explicit_asset" else None,
                          "family": "rigid" if invalid == "physical_family" else "scene",
                          "supporting_tests": refs, "reason": "Explicit background routing",
                          "status": "covered"}], "no_omissions": True, "reason": "All represented"})
    return audit_task_scope("Keep these objects as background", designs, assets, tmp_path / "physics",
                            VLMConfig(backend="replay", replay_dir=str(tmp_path / "replay")),
                            tmp_path / "output")


def test_grouped_background_is_split_into_individually_bound_assets(tmp_path):
    result = _grouped_background_run(tmp_path)
    assert result["passed"]
    assert len(result["requirements"]) == 4
    for index, row in enumerate(result["requirements"]):
        assert row["asset_id"] == f"bg-{index}"
        assert row["family"] == "scene"
        assert row["supporting_tests"] == [f"bg-{index}/background-route"]
        assert row["status"] == "covered"




@pytest.mark.parametrize("invalid", ["route", "duplicate", "unknown", "physical", "layout",
                                     "visual", "explicit_asset", "physical_family"])
def test_background_normalization_cannot_relax_other_evidence_gates(tmp_path, invalid):
    result = _grouped_background_run(tmp_path, invalid=invalid)
    assert not result["passed"]
    assert len(result["requirements"]) == 1
    assert result["requirements"][0]["status"] == "unverified"
