from pathlib import Path

import pytest

from roboenv_perception.construction.common import new_run
from roboenv_perception.harness.run import (
    HarnessFailure,
    HarnessRequest,
    HarnessSettings,
    _stage,
    _verify,
)
from roboenv_perception.io import read_json, write_json


def test_one_media_mode_and_task_required():
    with pytest.raises(ValueError):
        HarnessRequest(task="build bike", video=Path("bike.mp4"), images=[Path("bike.jpg")])
    with pytest.raises(ValueError):
        HarnessRequest(task="build bike")
    assert HarnessRequest(task="build bike", images=[Path("bike.jpg")]).images


def test_completed_stage_resumes_only_if_bound_sentinel_unchanged(tmp_path):
    state = {"stages": {}}
    calls = []

    def work(path):
        calls.append(path)
        path.mkdir()
        write_json(path / "result.json", {"answer": 1})
        return "result.json"

    first = _stage(tmp_path, state, "evidence", work)
    assert first == _stage(tmp_path, state, "evidence", work)
    assert len(calls) == 1
    write_json(first / "result.json", {"answer": 2})
    with pytest.raises(HarnessFailure, match="changed"):
        _stage(tmp_path, state, "evidence", work)


def test_resume_rejects_changed_physical_evidence_beyond_sentinel(tmp_path):
    state = {"stages": {}}

    def work(path):
        path.mkdir()
        write_json(path / "physics.json", {"status": "passed", "report": "validation.json"})
        write_json(path / "validation.json", {"response": 0.01})
        return "physics.json"

    stage = _stage(tmp_path, state, "physics", work)
    write_json(stage / "validation.json", {"response": 99})
    with pytest.raises(HarnessFailure, match="artifact changed"):
        _stage(tmp_path, state, "physics", work)


def test_failed_stage_retries_in_fresh_directory(tmp_path):
    state = {"stages": {}}

    def broken(path):
        path.mkdir()
        raise RuntimeError("failure")

    with pytest.raises(RuntimeError):
        _stage(tmp_path, state, "research", broken)
    assert state["stages"]["research"]["status"] == "failed"

    def recovered(path):
        path.mkdir()
        write_json(path / "result.json", {"ok": True})
        return "result.json"

    second = _stage(tmp_path, state, "research", recovered)
    assert second.name == "research-001"
    assert state["stages"]["research"]["status"] == "completed"


def test_assembled_scene_can_resume_with_binary_usd_sentinel(tmp_path):
    state = {"stages": {}}

    def work(path):
        with new_run(path, "scene_assembly", {}):
            (path / "scene.usda").write_text("#usda 1.0\n")
            write_json(path / "layout.json", {"assets": []})
            write_json(path / "scene_result.json", {"status": "assembled"})
        return "scene_result.json"

    first = _stage(tmp_path, state, "scene", work)
    assert first == _stage(tmp_path, state, "scene", work)


def test_verify_checks_every_joint_and_coupling(tmp_path, monkeypatch):
    import roboenv_perception.harness.physics as physics

    asset = tmp_path / "asset"
    asset.mkdir()
    blueprint = {
        "object_id": "rotor", "kind": "articulated",
        "frame_convention": "metres; Z-up; front=+Y; local object frame",
        "parts": [
            {"name": name, "parent": parent, "role": name, "geometry": "box",
             "material_appearance": "gray", "collision_intent": "solid", "evidence_refs": ["view-001"]}
            for name, parent in (("base", None), ("crank", "base"),
                                 ("wheel", "base"), ("steer", "base"))
        ],
        "joints": [
            {"name": name, "parent": "base", "child": child, "type": kind,
             "axis_child": [0, 1, 0], "origin_hint": "axle", "evidence_kind": "assumed",
             "evidence_refs": ["view-001"],
             **({"rest": "centered"} if kind == "revolute" else {})}
            for name, child, kind in (("spin_crank", "crank", "continuous"),
                                      ("spin_wheel", "wheel", "continuous"),
                                      ("steer", "steer", "revolute"))
        ],
        "couplings": [{"driver_joint": "spin_crank", "driven_joint": "spin_wheel",
                       "ratio": {"value": 2.5, "source": "assumed", "evidence_refs": ["view-001"]},
                       "evidence_kind": "assumed", "evidence_refs": ["view-001"]}],
        "acceptance": ["rotates"]}
    write_json(asset / "blueprint.json", blueprint)
    (asset / "model.py").write_text("# model\n")
    assets = tmp_path / "assets"
    assets.mkdir()
    write_json(assets / "assets.json", {"objects": [{"asset_id": "asset-0001",
               "build_status": "visual_review_passed", "model_ref": str(asset / "model.py")} ]})
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "scene.usda").write_text("#usda 1.0\n")
    write_json(scene / "scene_result.json", {"status": "assembled"})
    write_json(scene / "layout.json", {"assets": [{"asset_id": "asset-0001",
              "prim_path": "/World/Assets/Asset_asset_0001"}]})
    calls = []

    def fake_task(task):
        calls.append((task.output_dir.name, task.protocol.family.value,
                      task.protocol.target_ratio))
        return physics.PhysicsResult("passed")

    monkeypatch.setattr(physics, "run_physics_task", fake_task)
    settings = HarnessSettings(perception_config=tmp_path / "p", construction_config=tmp_path / "c",
                               artisan_python=tmp_path / "a", isaac_python=Path(__import__("sys").executable))
    out = tmp_path / "physics"
    _verify(out, scene, assets, settings)
    assert calls == [("coupling-00", "coupled", 2.5), ("steer", "hinge", None)]
    assert read_json(out / "physics.json")["status"] == "passed"


def _rigid_disk_fixture(tmp_path):
    built = tmp_path / "built"
    blueprint = {"object_id": "box", "kind": "rigid",
                 "frame_convention": "metres; Z-up; front=+Y; local object frame",
                 "parts": [{"name": "body", "parent": None, "role": "body", "geometry": "box",
                            "material_appearance": "gray", "collision_intent": "solid",
                            "evidence_refs": ["view-001"]}], "joints": [], "acceptance": ["push"]}
    write_json(built / "blueprint.json", blueprint)
    (built / "model.py").write_text("# model\n")
    write_json(built / "recipe.json", {"part_masses_kg": {"body": 0.2, "detail": 0.1}})
    assets = tmp_path / "assets"
    row = {"asset_id": "asset-0001", "object_id": "box", "build_status": "visual_review_passed",
           "model_ref": str(built / "model.py"), "recipe_ref": str(built / "recipe.json"),
           "usd": str(built / "object.usd"), "usd_sha256": "a" * 64,
           "selected_revision": 0, "attempts": [{"revision": 0, "status": "reviewed"}],
           "physics_status": "not_run"}
    write_json(assets / "assets.json", {"objects": [row]})
    scene = tmp_path / "scene"
    write_json(scene / "scene_result.json", {"status": "assembled"})
    write_json(scene / "layout.json", {"assets": [{"asset_id": "asset-0001",
                                                 "prim_path": "/World/Assets/Box"}]})
    (scene / "scene.usda").write_text("#usda 1.0\n")
    settings = HarnessSettings(perception_config=tmp_path / "p", construction_config=tmp_path / "c",
                               artisan_python=tmp_path / "a", isaac_python=Path(__import__("sys").executable))
    return assets, scene, settings, blueprint, row


def test_verify_freezes_initial_protocol_across_mass_repair(tmp_path, monkeypatch):
    import roboenv_perception.harness.physics as physics

    assets, scene, settings, _, row = _rigid_disk_fixture(tmp_path)
    calls = []

    def fake_task(task):
        calls.append(task.protocol.as_dict())
        # A failed trial must also freeze its protocol; repairs cannot reduce the challenge.
        return physics.PhysicsResult("failed", issues=("insufficient response",))

    monkeypatch.setattr(physics, "run_physics_task", fake_task)
    initial = tmp_path / "physics-initial"
    _verify(initial, scene, assets, settings)
    persisted = read_json(initial / "physics.json")["results"][0]["tests"][0]["protocol"]
    assert persisted == calls[0]
    assert persisted["force_world_N"] == pytest.approx([2 * (0.2 + 0.1) * 9.81, 0, 0])
    assert persisted["drive_s"] == 0.05
    assert persisted["release_s"] == 0.1
    assert persisted["min_response"] == 0.002
    write_json(Path(row["recipe_ref"]), {"part_masses_kg": {"body": 0.001}})
    repaired = tmp_path / "physics-repaired"
    _verify(repaired, scene, assets, settings, frozen=initial)
    assert calls[1] == calls[0]
    repaired_test = read_json(repaired / "physics.json")["results"][0]["tests"][0]
    assert repaired_test["protocol"] == persisted
    assert repaired_test["status"] == "failed"


def test_failed_repair_retains_previous_visual_candidate_without_physics_pass(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    import roboenv_perception.harness.run as run

    assets, _, settings, blueprint, previous = _rigid_disk_fixture(tmp_path)
    designs, evidence, research = [tmp_path / name for name in ("designs", "evidence", "research")]
    write_json(designs / "designs.json", {"objects": [{"asset_id": "asset-0001", "object_id": "box",
                                                       "status": "design_ready"}]})
    write_json(designs / "objects/asset-0001/blueprint.json", blueprint)
    write_json(evidence / "objects/asset-0001/evidence.json", {"views": []})
    write_json(research / "research.json", {"objects": {}})
    failures = []

    def broken_recipe(*args):
        failures.append(args)
        raise ValueError("repair recipe invalid")

    monkeypatch.setattr(run, "infer_recipe", broken_recipe)
    out = tmp_path / "repair"
    run._build_assets(out, designs, evidence, research, settings, SimpleNamespace(vlm=None),
                      time.monotonic() + 30, previous_assets=assets,
                      repair_feedback={"asset-0001": ["physics response failed"]})
    row = read_json(out / "assets.json")["objects"][0]
    assert len(failures) == settings.max_geometry_revisions
    assert row["build_status"] == "visual_review_passed"
    assert row["repair_status"] == "failed_previous_candidate_retained"
    assert len(row["repair_attempts"]) == settings.max_geometry_revisions
    assert all(attempt["status"] == "failed" for attempt in row["repair_attempts"])
    for key in ("model_ref", "recipe_ref", "usd", "usd_sha256", "selected_revision", "attempts"):
        assert row[key] == previous[key]
    assert row["physics_status"] == "not_run"
    assert "repair recipe invalid" in row["repair_issues"][0]
