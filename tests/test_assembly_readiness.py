"""Early structural failures must not consume geometry/model or renderer calls."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from roboenv_perception.construction.assembly_blueprint import AssemblyBlueprint, parse_blueprint
from roboenv_perception.construction.assembly_design import check_assembly
from roboenv_perception.construction.assembly_readiness import analyze_readiness
from roboenv_perception.io import read_json, sha256, write_json

FIXTURE = Path(__file__).parent / "fixtures/mechanical_geometry/blueprint.json"


def blueprint():
    return AssemblyBlueprint.model_validate(read_json(FIXTURE))


def test_supported_description_is_compatible_without_claiming_geometry_pass():
    bp = blueprint()
    before = bp.model_dump(mode="json")
    result = analyze_readiness(bp)
    assert result["status"] == "compatible" and result["issues"] == []
    assert result["geometry_status"] == result["physics_status"] == "not_run"
    assert bp.model_dump(mode="json") == before


@pytest.mark.parametrize("fault,code", [
    ("parent", "parent_tree_conflict"),
    ("part_only", "ambiguous_constraint_surfaces"),
    ("one_plane", "ambiguous_constraint_surfaces"),
    ("duplicate", "ambiguous_constraint_surfaces"),
    ("region", "nonfunctional_interface"),
    ("unsupported", "unsupported_relation"),
])
def test_concrete_description_errors_preserved_with_paths_and_correct_targets(fault, code):
    bp = blueprint()
    if fault == "parent":
        bp.part_instances[1].parent = None
    elif fault == "part_only":
        bp.geometric_constraints[2].interface_refs = []
        bp.geometric_constraints[2].part_refs = ["housing"]
    elif fault == "one_plane":
        bp.geometric_constraints[2].interface_refs = ["housing_top"]
    elif fault == "duplicate":
        bp.geometric_constraints[2].interface_refs = ["housing_top", "housing_top"]
    elif fault == "region":
        bp.interfaces[0].kind = "hinge_region"
    else:
        bp.geometric_constraints[0].type = "equal_width"
    before = bp.model_dump(mode="json")
    report = analyze_readiness(bp)
    assert report["status"] == "blocked"
    row = next(r for r in report["issues"] if r["code"] == code)
    assert row["path"].startswith("/")
    assert row["repair_target"] == ("verifier" if fault == "unsupported" else "blueprint")
    assert bp.model_dump(mode="json") == before
    # Unknown or unsupported construction is still a valid description.
    assert check_assembly(bp)["status"] != "needs_revision"


def test_single_root_is_not_enough_to_allow_a_joint_cycle():
    from roboenv_perception.construction.assembly_blueprint import PartInstance

    bp = blueprint()
    extra = bp.joints[0].model_copy(deep=True)
    extra.name, extra.parent, extra.child = "return", "shaft", "housing"
    bp.joints.append(extra)
    bp.part_instances.append(PartInstance(name="loose", definition_id=bp.part_instances[0].definition_id))
    report = analyze_readiness(bp)
    assert any(r["code"] == "joint_cycle" for r in report["issues"])


def test_legacy_description_remains_compatible():
    bp = parse_blueprint(read_json(Path(__file__).parent / "fixtures/assembly_blueprint/laptop-legacy.json"))
    assert analyze_readiness(bp)["status"] == "compatible"


def test_changed_structure_audit_cannot_be_reused_before_geometry(tmp_path):
    from test_fixed_workflow import advance, setup, target

    w, c = setup(tmp_path)
    target(w, tmp_path)
    path = w.ref(w.state["structure_readiness"], "construction_readiness")
    write_json(path, {"status": "compatible", "issues": []})
    with pytest.raises(ValueError, match="发生变化"):
        advance(w, tmp_path, "accept_structure", reason="try changed audit")
    assert "geometry" not in c.calls


def test_native_geometry_gate_prevents_model_call_even_with_passed_review(tmp_path, monkeypatch):
    from roboenv_perception.components.operations import run
    from roboenv_perception.construction.contracts import ConstructionConfig

    bp = blueprint()
    bp.part_instances[1].parent = None
    source = tmp_path / "source"
    write_json(source / "blueprint.json", bp.model_dump(mode="json"))
    write_json(source / "evidence/objects.json", {})
    monkeypatch.setattr("roboenv_perception.components.operations.load_construction_config", lambda p: ConstructionConfig())
    monkeypatch.setattr("roboenv_perception.components.operations.observation",
                        lambda *a: ({}, {"object_id": bp.object_id, "views": []}, []))
    monkeypatch.setattr("roboenv_perception.harness.geometry.make_vlm",
                        lambda *a, **kw: pytest.fail("invalid structure reached geometry model"))
    sources = {"blueprint": {"root": str(source), "ref": "blueprint.json", "files": {".": sha256(source/"blueprint.json")}}}
    from roboenv_perception.artifacts.registry import tree_index

    sources["evidence"] = {"root": str(source), "ref": "evidence", "files": tree_index(source/"evidence")}
    with pytest.raises(ValueError, match="结构构建前检查"):
        run("geometry", sources, tmp_path / "blocked", SimpleNamespace(construction_config=None), "fit", workflow=True)
    assert read_json(tmp_path / "blocked/construction_readiness.json")["status"] == "blocked"


def test_workflow_saves_audit_resumes_then_revision_can_reach_geometry(tmp_path, monkeypatch):
    from test_fixed_workflow import advance, setup, target

    w, c = setup(tmp_path)
    bp = blueprint().model_dump(mode="json")
    bp["object_id"] = bp["asset_brief"]["object_id"] = "laptop"
    bp["part_instances"][1]["parent"] = None
    c.canonical = bp
    target(w, tmp_path)
    receipt = w.state["structure_readiness"]
    first_hash = sha256(w.ref(receipt, "construction_readiness"))
    result = advance(w, tmp_path, "accept_structure", reason="try existing description")
    assert result["phase"] == "recovery" and "geometry" not in c.calls
    assert result["critic"][-1]["suggested_scope"] == "structure"
    assert advance(w, tmp_path, "resume")["phase"] == "recovery"
    assert sha256(w.ref(receipt, "construction_readiness")) == first_hash
    # Simulates a new author output; the controller itself never changes parents.
    c.canonical = {**bp, "part_instances": blueprint().model_dump(mode="json")["part_instances"]}
    result = advance(w, tmp_path, "revise_structure", reason="explicit author correction from original evidence")
    assert result["construction_readiness"]["status"] == "compatible"
    assert c.calls.count("perceive") == 1
    monkeypatch.setattr(w, "preflight_build", lambda: None)
    assert advance(w, tmp_path, "accept_structure", reason="new compatible structure")["phase"] == "completed"
    assert sha256(w.ref(receipt, "construction_readiness")) == first_hash


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_schema_correction_is_bounded_and_keeps_unknowns_and_failed_trace(tmp_path, repair_succeeds):
    import json

    from pydantic import ValidationError

    from roboenv_perception.construction.assembly_design import author_assembly

    valid = blueprint().model_dump(mode="json")
    prompts = []

    class Model:
        def infer(self, stage, prompt, images, schema, trace):
            prompts.append(prompt.split("\nJSON schema:\n")[0])
            output = json.loads(json.dumps(valid))
            if len(prompts) == 1 or not repair_succeeds:
                output["interfaces"][0]["kind"] = "planar"
            return output

    if repair_succeeds:
        bp = author_assembly(Model(), {"original_evidence": "view-real"}, [], tmp_path, schema_revisions=1)
        assert bp.model_dump(mode="json") == valid
    else:
        with pytest.raises(ValidationError):
            author_assembly(Model(), {"original_evidence": "view-real"}, [], tmp_path, schema_revisions=1)
        assert (tmp_path / "author-repair-1/rejected.json").is_file()
    assert len(prompts) == 2
    assert (tmp_path / "author/rejected.json").is_file()
    assert 'contract_feedback' in prompts[1] and 'view-real' in prompts[1]


def test_schema_retry_does_not_hide_transport_failures(tmp_path):
    from roboenv_perception.construction.assembly_design import author_assembly

    calls = []

    class Model:
        def infer(self, *args):
            calls.append(args)
            raise TimeoutError("transport interrupted")

    with pytest.raises(TimeoutError):
        author_assembly(Model(), {}, [], tmp_path, schema_revisions=1)
    assert len(calls) == 1


@pytest.mark.parametrize("fault,code", [("delete", "dropped_requirement"),
                                      ("type", "changed_relation_type"),
                                      ("planes", "axis_replaced_by_planes")])
def test_revision_cannot_hide_original_requirements_behind_helper_planes(fault, code):
    before = blueprint()
    before.geometric_constraints[0].type = "parallel"
    candidate = before.model_copy(deep=True)
    if fault == "delete":
        candidate.geometric_constraints.pop(0)
    elif fault == "type":
        candidate.geometric_constraints[0].type = "coplanar"
    else:
        for interface in candidate.interfaces[:2]:
            interface.kind = "plane"
    assert any(r["code"] == code for r in check_assembly(candidate, previous=before)["revision_findings"])
    assert check_assembly(candidate, previous=before)["status"] == "needs_revision"
    assert check_assembly(before, previous=before)["status"] != "needs_revision"
