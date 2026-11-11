"""Evidence fusion and explicit hypotheses: deterministic fixtures, no model/physics."""

from pathlib import Path

import pytest
from PIL import Image

from roboenv_perception.construction.assembly_blueprint import (
    AssemblyBlueprint,
    migrate_envcreator,
)
from roboenv_perception.construction.common import new_run, verify_run
from roboenv_perception.harness import workflow_evidence as evidence
from roboenv_perception.harness import workflow_hypothesis as hypothesis
from roboenv_perception.harness.geometry import GeometryRecipe
from roboenv_perception.io import read_json, sha256, write_json

FIXTURES = Path(__file__).parent / "fixtures/assembly_blueprint"


def origin(root, number, times, *, object_id=None, video_hash="a" * 64):
    track = object_id or f"track-{number}"
    perception, observed = root / f"perception-{number}", root / f"evidence-{number}"
    with new_run(perception, "fixture-perception", {}):
        frames = []
        for i, seconds in enumerate(times):
            ref = f"frames/{i}.png"
            (perception / "frames").mkdir(exist_ok=True)
            Image.new("RGB", (32, 24), (int(seconds * 20) % 256, 100, 40)).save(perception / ref)
            frames.append({
                "frame_id": f"frame-{i}", "source_pts": int(seconds * 1000),
                "source_time_base": "1/1000", "timestamp_seconds": seconds,
                "image_sha256": sha256(perception / ref), "image_ref": ref,
            })
        write_json(perception / "inputs/video.json", {"frames": frames, "source_sha256": video_hash})
        write_json(perception / "scene.json", {"kind": "video", "frames": frames})
    with new_run(observed, "fixture-evidence", {}):
        views = []
        for i, frame in enumerate(frames):
            folder = observed / f"views/{i}"
            folder.mkdir(parents=True)
            image = Image.open(perception / frame["image_ref"])
            image.save(folder / "frame.png")
            image.save(folder / "context.png")
            image.convert("RGBA").save(folder / "rgba.png")
            Image.new("L", image.size, 255).save(folder / "mask.png")
            views.append({
                "ref_id": f"view-{i + 1:03}", "frame_id": frame["frame_id"],
                "timestamp_seconds": times[i], "quality": {},
                **{key + "_ref": f"views/{i}/{key}.png" for key in ("frame", "context", "rgba", "mask")},
            })
        write_json(observed / "object.json", {
            "object_id": track, "asset_id": "asset-0001", "status": "ready_for_design",
            "category": "laptop", "views": views,
        })
        write_json(observed / "objects.json", {"objects": [{
            "object_id": track, "asset_id": "asset-0001",
            "status": "ready_for_design", "evidence_ref": "object.json",
        }]})
    return {"evidence": observed, "perception": perception, "round": number,
            "object_id": track, "video": True}


def laptop():
    bp = migrate_envcreator(read_json(FIXTURES / "laptop-legacy.json"),
                            read_json(FIXTURES / "laptop-intake.json"))
    value = bp.model_dump(mode="json")
    value["joints"][0]["rest"] = None
    return AssemblyBlueprint.model_validate(value)


def plan(bp, *, source_hash="b" * 64):
    ref = bp.asset_brief.evidence_refs[0]
    joint = bp.joints[0].name
    return {
        "source_blueprint_sha256": source_hash,
        "parameters": [
            {"path": "/target_dimensions_m/x", "value": .3, "unit": "m",
             "rationale": "test scale only, not recovered metres", "evidence_refs": [ref]},
            {"path": f"/joints/{joint}/rest", "value": "at_min", "unit": "convention",
             "rationale": "test reference convention only", "evidence_refs": [ref]},
            {"path": f"/joints/{joint}/travel", "value": 2., "unit": "rad",
             "rationale": "trial travel, no observed opening motion", "evidence_refs": [ref]},
        ],
    }


def boxes(bp):
    masses = {p.name: .2 for p in bp.part_instances}
    return GeometryRecipe.model_validate({
        "object_id": bp.object_id,
        "solids": [
            {"name": "solid_" + p.name, "part": p.name, "shape": "box",
             "size_m": [.3, .2, .02], "source": "assumed", "evidence_refs": ["view-001"]}
            for p in bp.part_instances
        ],
        "joint_placements": [{"joint": j.name, "origin_parent_m": [0, 0, .03],
                              "source": "assumed", "evidence_refs": ["view-001"]} for j in bp.joints],
        "part_masses_kg": masses,
    })


def test_fusion_preserves_front_and_rear_and_deduplicates_actual_frames(tmp_path):
    origins = [origin(tmp_path, 0, [0., 1., 2.]), origin(tmp_path, 1, [2., 3., 4.])]
    snapshots = [sha256(o["evidence"] / "artifacts.json") for o in origins]
    output = tmp_path / "fused"
    evidence.fuse(origins, None, output, stable_target="stable-laptop", identity_reason="continuous camera path",
                  video_sha256="a" * 64)
    packet = verify_run(output, "objects.json")
    assert packet["objects"][0]["object_id"] == "stable-laptop"
    obj = evidence.observations(output, None, False)[0]
    assert len(obj["views"]) == 5
    assert [v["seconds"] for v in obj["views"]] == [0., 1., 2., 3., 4.]
    assert len({v["ref"] for v in obj["views"]}) == 5
    assert obj["views"][1]["source_frame"].endswith(":1")
    record = read_json(output / "evidence.json")
    assert len({view["frame_id"] for view in record["views"]}) == 5
    assert len(record["fusion"]["track_bindings"]) == 2
    assert {v["source_observation"]["role"] for v in record["views"]} == {"real_observation"}
    assert snapshots == [sha256(o["evidence"] / "artifacts.json") for o in origins]


def test_fusion_rejects_other_video_and_empty_identity_reason(tmp_path):
    origins = [origin(tmp_path, 0, [0.]), origin(tmp_path, 1, [1.], video_hash="c" * 64)]
    with pytest.raises(ValueError, match="其他视频"):
        evidence.fuse(origins, None, tmp_path / "bad", stable_target="x",
                      identity_reason="same class", video_sha256="a" * 64)
    with pytest.raises(ValueError, match="身份"):
        evidence.fuse(origins, None, tmp_path / "empty", stable_target="x", identity_reason=" ")


def test_fusion_detects_changed_source_and_non_source_pixel_copy(tmp_path):
    origins = [origin(tmp_path, 0, [0.])]
    (origins[0]["evidence"] / "views/0/context.png").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact changed"):
        evidence.fuse(origins, None, tmp_path / "bad", stable_target="x", identity_reason="continuous")


def test_fusion_budget_requires_agent_selection_and_new_selected_frames(tmp_path):
    origins = [origin(tmp_path, 0, list(range(8))), origin(tmp_path, 1, list(range(8, 18)))]
    pool = evidence.pool(origins)
    with pytest.raises(ValueError, match="16"):
        evidence.selection(pool)
    prior = [p["source_frame"] for p in pool[:8]]
    with pytest.raises(ValueError, match="没有增加"):
        evidence.selection(pool, ["e0:view-001"], previous_frames=prior)
    selected = evidence.selection(pool, ["e0:view-001", "e1:view-001"], previous_frames=prior)
    assert [v["seconds"] for v in selected] == [0, 8]
    with pytest.raises(ValueError, match="不存在"):
        evidence.selection(pool, ["candidate-render:front"])
    with pytest.raises(ValueError, match="不重复"):
        evidence.selection(pool, ["e0:view-001", "e0:view-001"])


def test_hypothesis_keeps_canonical_unknowns_and_observed_states(tmp_path):
    bp = laptop()
    source = tmp_path / "canonical.json"
    write_json(source, bp.model_dump(mode="json"))
    old_hash = sha256(source)
    raw = plan(bp, source_hash=old_hash)
    output = tmp_path / "hypothesis"
    hypothesis.materialize(source, raw, output, bp.asset_brief.evidence_refs)
    result = AssemblyBlueprint.model_validate(read_json(output / "blueprint.json"))
    assert sha256(source) == old_hash
    assert bp.target_dimensions_m.x.value is None and bp.joints[0].travel.value is None
    assert result.target_dimensions_m.x.source == "assumed" and result.target_dimensions_m.x.value == .3
    assert result.joints[0].travel.source == "assumed" and result.joints[0].travel.value == 2
    assert result.asset_brief == bp.asset_brief and result.states == bp.states
    assert result.unresolved == bp.unresolved and result.interfaces == bp.interfaces and result.mates == bp.mates
    assert result.evidence[-1].origin == "prior"
    report = hypothesis.bound_report(output / "hypothesis.json", output / "blueprint.json")
    assert report["measurement_status"] == "not_measured" and report["unresolved_backend"] == []


@pytest.mark.parametrize("fault", ["stale", "known", "unit", "graph", "source", "nan", "bool", "relative"])
def test_hypothesis_rejects_silent_overwrite_invalid_units_and_fake_evidence(tmp_path, fault):
    bp = laptop()
    raw = plan(bp)
    source = tmp_path / "canonical.json"
    write_json(source, bp.model_dump(mode="json"))
    if fault == "stale":
        with pytest.raises(ValueError, match="过期"):
            hypothesis.materialize(source, raw, tmp_path / "stale", bp.asset_brief.evidence_refs)
        return
    if fault == "known":
        data = bp.model_dump(mode="json")
        data["joints"][0]["rest"] = "at_max"
        bp = AssemblyBlueprint.model_validate(data)
    elif fault == "unit":
        raw["parameters"][2]["unit"] = "m"
    elif fault == "graph":
        raw["parameters"][1]["path"] = f"/joints/{bp.joints[0].name}/type"
        raw["parameters"][1]["value"] = "fixed"
    elif fault == "source":
        raw["parameters"][0]["evidence_refs"] = ["candidate:front"]
    elif fault == "nan":
        raw["parameters"][0]["value"] = float("nan")
    elif fault == "bool":
        raw["parameters"][0]["value"] = True
    else:
        data = bp.model_dump(mode="json")
        data["coordinate_units"] = "relative_width"
        for interface in data["interfaces"]:
            interface["local_frame"]["position_unit"] = "relative_width"
        bp = AssemblyBlueprint.model_validate(data)
    with pytest.raises(ValueError):
        hypothesis.apply(bp, raw, bp.asset_brief.evidence_refs)


def test_hypothesis_audit_tests_real_recipe_and_hard_negative():
    source = laptop()
    raw = plan(source)
    bp = hypothesis.apply(source, raw, source.asset_brief.evidence_refs)
    recipe = boxes(bp)
    assert hypothesis.audit(bp, recipe, raw)["status"] == "passed"
    wrong = recipe.model_dump(mode="json")
    wrong["solids"][0]["size_m"][0] = .35
    report = hypothesis.audit(bp, GeometryRecipe.model_validate(wrong), raw)
    assert report["status"] == "failed"
    assert report["issues"][0]["actual"] == .35
    wrong = recipe.model_dump(mode="json")
    wrong["solids"][0]["source"] = "observed"
    assert hypothesis.audit(bp, GeometryRecipe.model_validate(wrong), raw)["status"] == "failed"


def test_hypothesis_missing_parameters_remain_unresolved():
    source = laptop()
    raw = plan(source)
    raw["parameters"] = raw["parameters"][:1]
    bp = hypothesis.apply(source, raw, source.asset_brief.evidence_refs)
    paths = {p["path"] for p in hypothesis.requirements(bp)}
    assert f"/joints/{source.joints[0].name}/travel" in paths
    assert f"/joints/{source.joints[0].name}/rest" in paths


def test_hypothesis_audit_mass_and_unsupported_geometry():
    source = laptop()
    raw = plan(source)
    raw["parameters"].append({
        "path": f"/part_definitions/{source.part_definitions[0].id}/mass_kg",
        "value": .4, "unit": "kg", "rationale": "trial mass, not measured",
        "evidence_refs": ["view-001"],
    })
    bp = hypothesis.apply(source, raw, source.asset_brief.evidence_refs)
    recipe = boxes(bp)
    assert hypothesis.audit(bp, recipe, raw)["status"] == "failed"
    value = recipe.model_dump(mode="json")
    value["solids"][0]["shape"] = "ellipsoid"
    assert any(row["status"] == "unresolved" for row in hypothesis.audit(
        bp, GeometryRecipe.model_validate(value), raw)["checks"])


def test_hypothesis_audit_detects_tampered_rest_or_travel_claim():
    source = laptop()
    raw = plan(source)
    bp = hypothesis.apply(source, raw, source.asset_brief.evidence_refs)
    altered = {**raw, "parameters": [dict(row) for row in raw["parameters"]]}
    altered["parameters"][2]["value"] = 3.
    report = hypothesis.audit(bp, boxes(bp), altered)
    assert report["status"] == "failed"
    assert report["issues"][0]["actual"] == 2.


def test_each_same_definition_instance_must_match_assumed_dimensions():
    from roboenv_perception.construction.assembly_blueprint import PartInstance

    source = laptop()
    source = source.model_copy(update={"part_instances": [
        *source.part_instances,
        PartInstance(name="spare_body", definition_id=source.part_instances[0].definition_id, parent=None),
    ]})
    raw = plan(source)
    raw["parameters"].append({
        "path": f"/part_definitions/{source.part_definitions[0].id}/dimensions_m/x",
        "value": .3, "unit": "m", "rationale": "candidate width for each instance",
        "evidence_refs": ["view-001"],
    })
    bp = hypothesis.apply(source, raw, source.asset_brief.evidence_refs)
    recipe = boxes(bp).model_dump(mode="json")
    recipe["solids"][-1]["size_m"][0] = .2
    report = hypothesis.audit(bp, GeometryRecipe.model_validate(recipe), raw)
    assert report["status"] == "failed"
    assert any(r["path"].startswith("/part_definitions/") and r["status"] == "failed" for r in report["checks"])


def test_component_recomputes_hypothesis_gate_before_any_renderer(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from roboenv_perception.components.operations import geometry_operation

    source = tmp_path / "canonical.json"
    bp = laptop()
    write_json(source, bp.model_dump(mode="json"))
    raw = plan(bp, source_hash=sha256(source))
    hypothesis.materialize(source, raw, tmp_path / "hypothesis", bp.asset_brief.evidence_refs)
    derivative = tmp_path / "hypothesis/blueprint.json"
    candidate = AssemblyBlueprint.model_validate(read_json(derivative))
    recipe = boxes(candidate).model_dump(mode="json")
    recipe["solids"][0]["size_m"][0] = .4
    write_json(tmp_path / "recipe.json", recipe)
    paths = {"blueprint": derivative, "recipe": tmp_path / "recipe.json",
             "build_hypothesis": tmp_path / "hypothesis/hypothesis.json"}
    calls = []
    monkeypatch.setattr("roboenv_perception.harness.geometry.build_recipe",
                        lambda *args, **kwargs: calls.append(True))
    settings = SimpleNamespace(artisan_python=tmp_path / "unused", blender=None, max_wall_seconds=10)
    with pytest.raises(ValueError, match="假设审核"):
        geometry_operation("build", paths, tmp_path / "build", settings, "laptop", None, None, workflow=True)
    assert not calls
    assert read_json(tmp_path / "build/build_hypothesis_audit.json")["status"] == "failed"
