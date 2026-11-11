"""Protected local transactions, real component failures, and durable workflow use."""

import copy
from types import SimpleNamespace

import pytest
from test_geometric_verifier import fixture

from roboenv_perception.artifacts.registry import tree_index
from roboenv_perception.components.operations import run
from roboenv_perception.construction.assembly_blueprint import Interface
from roboenv_perception.construction.constraint_repair import (
    ConstraintRebinding,
    ConstraintRepair,
    apply_repair,
)
from roboenv_perception.construction.contracts import ConstructionConfig
from roboenv_perception.io import read_json, sha256, write_json


def case(tmp_path):
    bp, _ = fixture()
    bp.geometric_constraints[2].interface_refs = ["housing_top"]
    bp.interfaces[2].axis_local = (0, 0, 1)
    bp.interfaces[3].axis_local = (1, 0, 0)
    path = tmp_path / "source.json"
    write_json(path, bp.model_dump(mode="json"))
    constraint = ConstraintRebinding(
        id="datum_orthogonal",
        interface_refs=["housing_top", "housing_side"],
        description=bp.geometric_constraints[2].description,
    )
    proposal = ConstraintRepair(
        source_blueprint_sha256=sha256(path),
        replacements=[constraint],
        rationale="Bind the existing adjacent datum faces.",
    )
    return bp, path, proposal


def apply(bp, path, proposal):
    return apply_repair(bp, proposal, ["datum_orthogonal"], sha256(path))


def test_local_rebinding_preserves_everything_except_selected_constraint(tmp_path):
    bp, path, proposal = case(tmp_path)
    before = bp.model_dump(mode="json")
    candidate = apply(bp, path, proposal)
    after = candidate.model_dump(mode="json")
    assert after != before and bp.model_dump(mode="json") == before
    after["geometric_constraints"][2] = before["geometric_constraints"][2]
    assert after == before


@pytest.mark.parametrize("fault", ["hash", "id", "duplicate", "noop"])
def test_patch_cannot_weaken_or_expand_protected_requirements(tmp_path, fault):
    bp, path, proposal = case(tmp_path)
    before = bp.model_dump(mode="json")
    target = proposal.replacements[0]
    if fault == "hash":
        proposal.source_blueprint_sha256 = "0" * 64
    elif fault == "id":
        target.id = "new_requirement"
    elif fault == "duplicate":
        proposal.replacements.append(target.model_copy(deep=True))
    else:
        target.interface_refs = ["housing_top"]
    with pytest.raises(ValueError):
        apply(bp, path, proposal)
    assert bp.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "field,value",
    [("type", "parallel"), ("parameters", {}), ("part_refs", ["shaft"]), ("evidence_refs", [])],
)
def test_model_cannot_return_frozen_fields_even_with_the_original_values(tmp_path, field, value):
    _, _, proposal = case(tmp_path)
    data = proposal.model_dump(mode="json")
    data["replacements"][0][field] = value
    with pytest.raises(ValueError):
        ConstraintRepair.model_validate(data)


def added_interface(bp):
    return Interface(
        id="new_side",
        part="housing",
        kind="plane",
        basis="assumed",
        axis_local=(1, 0, 0),
        evidence_refs=[bp.evidence[0].id],
        description="Assumed adjacent side.",
    )


@pytest.mark.parametrize(
    "fault",
    [
        "overwrite",
        "hidden_shaft",
        "position",
        "parameters",
        "unknown_direction",
        "wrong_part",
        "unused",
        "observed",
        "invented_evidence",
    ],
)
def test_new_interfaces_cannot_change_shared_hardware_or_erase_uncertainty(tmp_path, fault):
    bp, path, proposal = case(tmp_path)
    interface = added_interface(bp)
    proposal.add_interfaces = [interface]
    proposal.replacements[0].interface_refs = ["housing_top", "new_side"]
    if fault == "overwrite":
        interface.id = "housing_side"
    elif fault == "hidden_shaft":
        interface.kind = "shaft"
    elif fault == "position":
        interface.local_frame.origin = (0, 0, 0)
    elif fault == "parameters":
        interface.parameters = copy.deepcopy(bp.geometric_constraints[1].parameters)
    elif fault == "unknown_direction":
        interface.axis_local = None
    elif fault == "wrong_part":
        interface.part = "shaft"
    elif fault == "unused":
        proposal.replacements[0].interface_refs = ["housing_top", "housing_side"]
    elif fault == "observed":
        interface.basis = "observed"
    else:
        interface.evidence_refs = ["unprovided:view"]
    with pytest.raises(ValueError):
        apply(bp, path, proposal)


def test_adding_explicit_assumed_plane_retains_all_existing_interfaces(tmp_path):
    bp, path, proposal = case(tmp_path)
    proposal.add_interfaces = [added_interface(bp)]
    proposal.replacements[0].interface_refs = ["housing_top", "new_side"]
    # Supply the original top normal so the relation is deterministically checkable.
    bp.interfaces[2].axis_local = (0, 0, 1)
    write_json(path, bp.model_dump(mode="json"))
    proposal.source_blueprint_sha256 = sha256(path)
    candidate = apply(bp, path, proposal)
    assert candidate.interfaces[:-1] == bp.interfaces
    assert candidate.mates == bp.mates and candidate.joints == bp.joints


def component(tmp_path, monkeypatch, fault=None):
    bp, path, proposal = case(tmp_path)
    calls = []

    class Model:
        def infer(self, stage, prompt, images, schema, trace):
            calls.append((stage, prompt))
            if fault == "transport":
                raise TimeoutError("network interrupted")
            if stage == "component-constraint-repair":
                if fault == "type":
                    data = proposal.model_dump(mode="json")
                    data["replacements"][0]["type"] = "parallel"
                    return data
                return (
                    {"full_blueprint": True}
                    if fault == "schema"
                    else proposal.model_dump(mode="json")
                )
            if fault == "review_hash":
                return {
                    "blueprint_sha256": "short...hash",
                    "decision": "PASS",
                    "findings": [],
                    "summary": "bad hash",
                }
            if fault == "review_redo":
                return {
                    "blueprint_sha256": sha256(trace.parent / "blueprint.json"),
                    "decision": "REDO",
                    "findings": ["synthetic semantic defect"],
                    "summary": "requires evidence review",
                }
            return {
                "blueprint_sha256": sha256(trace.parent / "blueprint.json"),
                "decision": "PASS",
                "findings": [],
                "summary": "synthetic description only",
            }

    monkeypatch.setattr(
        "roboenv_perception.components.operations.make_vlm", lambda *a, **k: Model()
    )
    monkeypatch.setattr(
        "roboenv_perception.components.operations.load_construction_config",
        lambda p: ConstructionConfig(),
    )
    monkeypatch.setattr(
        "roboenv_perception.components.operations.observation",
        lambda *a: ({}, {"object_id": bp.object_id, "views": [{"ref_id": bp.evidence[0].id}]}, []),
    )
    write_json(tmp_path / "evidence/objects.json", {})
    sources = {
        "blueprint": {"root": str(tmp_path), "ref": path.name, "files": {".": sha256(path)}},
        "evidence": {
            "root": str(tmp_path),
            "ref": "evidence",
            "files": tree_index(tmp_path / "evidence"),
        },
    }
    return bp, path, calls, sources


@pytest.mark.parametrize("fault", [None, "schema", "type"])
def test_native_component_persists_success_and_transaction_rejections(tmp_path, monkeypatch, fault):
    bp, path, calls, sources = component(tmp_path, monkeypatch, fault)
    before = sha256(path)
    exports, status = run(
        "blueprint_repair",
        sources,
        tmp_path / "result",
        SimpleNamespace(construction_config=None),
        "fix only datum relation",
        params={"constraint_ids": ["datum_orthogonal"]},
        workflow=True,
    )
    report = read_json(
        tmp_path / "result" / exports["constraint_repair"] / "constraint_repair.json"
    )
    candidate = read_json(tmp_path / "result" / exports["blueprint"])
    assert sha256(path) == before
    assert report["source_blueprint_sha256"] == before
    assert report["status"] == ("applied" if fault is None else "rejected")
    assert (status == "needs_revision") == (fault is not None)
    assert len(calls) == (2 if fault is None else 1)
    if fault:
        assert candidate == bp.model_dump(mode="json") and not report["candidate_published"]
    # The proposal prompt does not re-author dimensions, intake, or the full blueprint.
    assert '"part_definitions"' not in calls[0][1].split("\nJSON schema:\n")[0]


def test_transport_failure_is_not_a_semantic_rejection_or_retry(tmp_path, monkeypatch):
    _, _, calls, sources = component(tmp_path, monkeypatch, "transport")
    with pytest.raises(TimeoutError):
        run(
            "blueprint_repair",
            sources,
            tmp_path / "result",
            SimpleNamespace(construction_config=None),
            "repair",
            params={"constraint_ids": ["datum_orthogonal"]},
            workflow=True,
        )
    assert len(calls) == 1 and not (tmp_path / "result/blueprint/blueprint.json").exists()


@pytest.mark.parametrize("fault", ["review_hash", "review_redo"])
def test_bad_review_preserves_candidate_but_never_claims_success(tmp_path, monkeypatch, fault):
    _, _, calls, sources = component(tmp_path, monkeypatch, fault)
    exports, status = run(
        "blueprint_repair",
        sources,
        tmp_path / "result",
        SimpleNamespace(construction_config=None),
        "repair",
        params={"constraint_ids": ["datum_orthogonal"]},
        workflow=True,
    )
    report = read_json(
        tmp_path / "result" / exports["constraint_repair"] / "constraint_repair.json"
    )
    assert status == report["status"] == "needs_revision"
    assert report["candidate_published"] and report["issues"] and len(calls) == 2


def test_existing_measurement_is_preserved_without_being_relabelled_or_remeasured(
    tmp_path, monkeypatch
):
    bp, path, _, sources = component(tmp_path, monkeypatch)
    from roboenv_perception.construction.assembly_blueprint import Evidence

    bp.evidence.append(
        Evidence(
            id="measurement:x", origin="measurement", description="Historical explicit user size"
        )
    )
    bp.target_dimensions_m.x.value = 0.1
    bp.target_dimensions_m.x.source = "measured"
    bp.target_dimensions_m.x.evidence_refs = ["measurement:x"]
    write_json(path, bp.model_dump(mode="json"))
    sources["blueprint"]["files"] = {".": sha256(path)}
    # A proposal must bind the revised file; use the existing typed proposal.
    _, _, proposal = case(tmp_path / "patch")
    proposal.source_blueprint_sha256 = sha256(path)

    class Model:
        def infer(self, stage, prompt, images, schema, trace):
            if stage == "component-constraint-repair":
                return proposal.model_dump(mode="json")
            return {
                "blueprint_sha256": sha256(trace.parent / "blueprint.json"),
                "decision": "PASS",
                "findings": [],
                "summary": "retained",
            }

    monkeypatch.setattr(
        "roboenv_perception.components.operations.make_vlm", lambda *a, **k: Model()
    )
    exports, status = run(
        "blueprint_repair",
        sources,
        tmp_path / "result",
        SimpleNamespace(construction_config=None),
        "repair",
        params={"constraint_ids": ["datum_orthogonal"]},
        workflow=True,
    )
    assert status != "needs_revision"
    candidate = read_json(tmp_path / "result" / exports["blueprint"])
    assert candidate["target_dimensions_m"] == bp.model_dump(mode="json")["target_dimensions_m"]


def test_axis_edge_relation_cannot_be_rebound_as_two_planes(tmp_path):
    from test_functional_features import case as features

    bp, _ = features()
    path = tmp_path / "source.json"
    write_json(path, bp.model_dump(mode="json"))
    proposal = ConstraintRepair(
        source_blueprint_sha256=sha256(path),
        rationale="invalid substitution",
        replacements=[
            ConstraintRebinding(
                id="axis_border",
                interface_refs=["housing_top", "housing_side"],
                description="fake equivalent",
            )
        ],
    )
    with pytest.raises(ValueError, match="轴线—边缘"):
        apply_repair(bp, proposal, ["axis_border"], sha256(path))


def test_workflow_local_repair_preserves_upstream_and_resumes_after_interruption(tmp_path):
    from test_fixed_workflow import advance, setup, target

    w, c = setup(tmp_path)
    bp, _, _ = case(tmp_path)
    data = bp.model_dump(mode="json")
    data["object_id"] = data["asset_brief"]["object_id"] = "laptop"
    c.canonical = data
    original = w.execute
    calls = []

    def execute(operation, sources, output, *args, **kwargs):
        if operation != "blueprint_repair":
            return original(operation, sources, output, *args, **kwargs)
        calls.append(operation)
        if len(calls) == 1:
            raise TimeoutError("interruption")
        candidate = copy.deepcopy(data)
        candidate["geometric_constraints"][2]["interface_refs"] = ["housing_top", "housing_side"]
        write_json(output / "blueprint/blueprint.json", candidate)
        write_json(output / "blueprint/constraint_repair.json", {"status": "applied", "issues": []})
        return {
            "blueprint": "blueprint/blueprint.json",
            "constraint_repair": "blueprint",
        }, "ready_with_assumptions"

    w.execute = execute
    target(w, tmp_path)
    prior = w.ref(w.state["blueprint"], "blueprint")
    prior_hash = sha256(prior)
    with pytest.raises(ValueError, match="不存在"):
        advance(w, tmp_path, "repair_constraints", constraint_ids=["missing"], reason="wrong id")
    with pytest.raises(TimeoutError):
        advance(
            w,
            tmp_path,
            "repair_constraints",
            constraint_ids=["datum_orthogonal"],
            reason="specific binding error",
        )
    result = advance(w, tmp_path, "resume")
    assert result["phase"] == "structure"
    assert sha256(prior) == prior_hash
    assert c.calls.count("perceive") == c.calls.count("blueprint") == 1
    assert c.calls.count("blueprint_review") == 2
    assert "geometry" not in c.calls and result["history"][-1]["constraint_ids"] == [
        "datum_orthogonal"
    ]
    assert any(r["stage"] == "blueprint_repair" for r in result["critic"])


def test_rejected_workflow_patch_returns_to_recovery_without_review_or_geometry(tmp_path):
    from test_fixed_workflow import advance, setup, target

    w, c = setup(tmp_path)
    bp, _, _ = case(tmp_path)
    data = bp.model_dump(mode="json")
    data["object_id"] = data["asset_brief"]["object_id"] = "laptop"
    c.canonical = data
    original = w.execute

    def execute(operation, sources, output, *args, **kwargs):
        if operation != "blueprint_repair":
            return original(operation, sources, output, *args, **kwargs)
        write_json(output / "blueprint/blueprint.json", data)
        write_json(output / "blueprint/selfcheck.json", {"issues": ["invalid patch"]})
        write_json(
            output / "blueprint/constraint_repair.json",
            {"status": "rejected", "issues": ["invalid patch"]},
        )
        return {
            "blueprint": "blueprint/blueprint.json",
            "constraint_repair": "blueprint",
        }, "needs_revision"

    w.execute = execute
    target(w, tmp_path)
    result = advance(
        w,
        tmp_path,
        "repair_constraints",
        constraint_ids=["datum_orthogonal"],
        reason="local repair",
    )
    assert result["phase"] == "recovery"
    assert c.calls.count("blueprint_review") == 1 and "geometry" not in c.calls
    assert result["critic"][-1]["stage"] == "blueprint_repair"
    assert result["critic"][-1]["issues"] == ["invalid patch"]
