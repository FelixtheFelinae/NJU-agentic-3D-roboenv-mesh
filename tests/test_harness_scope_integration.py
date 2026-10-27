"""Accepted object scope must gate downstream work before any model call."""

import pytest
from PIL import Image

from roboenv_perception.construction import design
from roboenv_perception.construction.common import new_run
from roboenv_perception.construction.contracts import ConstructionConfig
from roboenv_perception.contracts import VLMConfig
from roboenv_perception.harness import research
from roboenv_perception.harness.web import WebSearchPolicy
from roboenv_perception.io import read_json, sha256, write_json


def _evidence_and_scope(tmp_path, *, resolved, action):
    evidence = tmp_path / "evidence"
    with new_run(evidence, "object_evidence", {}):
        write_json(evidence / "objects.json", {"scene_id": "test-scene", "objects": [{
            "asset_id": "laptop", "object_id": "object-laptop",
            "evidence_ref": "objects/laptop/evidence.json"}]})
        write_json(evidence / "objects/laptop/evidence.json", {
            "asset_id": "laptop", "object_id": "object-laptop", "category": "laptop",
            "physical_role": "rigid_body_candidate", "task_role": "manipulation_candidate",
            "status": "ready_for_design", "views": [{"ref_id": "view-001", "context_ref": "objects/laptop/context.png"}]})
        Image.new("RGB", (4, 4)).save(evidence / "objects/laptop/context.png")
    scope = tmp_path / "scope_plan.json"
    write_json(scope, {"all_resolved": resolved, "objects": [{
        "asset_id": "laptop", "action": action, "reason": "Explicit task background"}]})
    return evidence, scope


def _forbid_models_and_search(monkeypatch):
    class ForbiddenVLM:
        def infer(self, *args, **kwargs):
            raise AssertionError("Non-build scope reached VLM")

    monkeypatch.setattr(design, "make_vlm", lambda *args, **kwargs: ForbiddenVLM())
    monkeypatch.setattr(research, "make_vlm", lambda *args, **kwargs: ForbiddenVLM())

    def forbidden_search(*args, **kwargs):
        raise AssertionError("Non-build scope reached public search")

    monkeypatch.setattr(research.WebEvidenceClient, "search", forbidden_search)


@pytest.mark.parametrize("action", ["background", "exclude"])
def test_explicit_nonbuild_scope_bypasses_design_intake_and_product_research(tmp_path, monkeypatch, action):
    evidence, scope = _evidence_and_scope(tmp_path, resolved=True, action=action)
    _forbid_models_and_search(monkeypatch)
    output = tmp_path / "design"
    design.design_objects(evidence, output, ConstructionConfig(), task="Reconstruct bottle only",
                          object_scope=scope)
    row = read_json(output / "designs.json")["objects"][0]
    assert row["status"] == "routed" and row["route"] == action
    assert not (output / "objects/laptop/intake.json").exists()
    assert read_json(output / "run.json")["sources"]["object_scope_sha256"] == sha256(scope)
    assert (output / "object_scope.json").read_bytes() == scope.read_bytes()
    research.research_objects(evidence, tmp_path / "research", VLMConfig(), WebSearchPolicy(),
                              task="Reconstruct bottle only", object_scope=scope)
    record = read_json(tmp_path / "research/research.json")["objects"]["object-laptop"]
    assert record["status"] == "skipped_scope"
    assert record["sources"] == [] and record["claims"] == []


@pytest.mark.parametrize("resolved,action", [(False, "build"), (True, "unresolved")])
def test_unaccepted_or_unresolved_scope_cannot_become_design_ready(tmp_path, monkeypatch, resolved, action):
    evidence, scope = _evidence_and_scope(tmp_path, resolved=resolved, action=action)
    _forbid_models_and_search(monkeypatch)
    output = tmp_path / "design"
    design.design_objects(evidence, output, ConstructionConfig(), object_scope=scope)
    result = read_json(output / "designs.json")
    assert not result["all_designs_ready"]
    assert result["objects"][0]["status"] == "blocked"
    assert "scope" in result["objects"][0]["reason"]
    research.research_objects(evidence, tmp_path / "research", VLMConfig(), WebSearchPolicy(),
                              object_scope=scope)
    record = read_json(tmp_path / "research/research.json")["objects"]["object-laptop"]
    assert record["status"] == "skipped_scope"
