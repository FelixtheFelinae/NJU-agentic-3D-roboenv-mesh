import json

import pytest
from PIL import Image

from roboenv_perception.contracts import VLMConfig
from roboenv_perception.harness import scope_plan
from roboenv_perception.io import read_json, write_json


def _fixture(tmp_path, monkeypatch, responses):
    evidence = tmp_path / "evidence"
    entries = []
    for asset_id, category in (("bottle", "bottle"), ("laptop", "laptop"), ("mouse", "mouse")):
        base = f"objects/{asset_id}"
        entries.append({"asset_id": asset_id, "evidence_ref": f"{base}/evidence.json"})
        write_json(evidence / base / "evidence.json", {
            "asset_id": asset_id, "category": category, "physical_role": "rigid_body_candidate",
            "task_role": "manipulation_candidate", "views": [
                {"rgba_ref": f"{base}/rgba.png", "context_ref": f"{base}/context.png"}]})
        for name in ("rgba.png", "context.png"):
            Image.new("RGBA", (4, 4)).save(evidence / base / name)
    write_json(evidence / "objects.json", {"objects": entries})
    calls = []
    stream = iter(responses)

    class FakeVLM:
        def infer(self, stage, prompt, images, schema, trace):
            calls.append({"stage": stage, "prompt": json.loads(prompt.split("\nJSON schema:\n")[0]), "images": images})
            value = next(stream)
            if isinstance(value, Exception):
                raise value
            write_json(trace / "request.json", {"prompt": prompt})
            write_json(trace / "response.json", value)
            return value

    monkeypatch.setattr(scope_plan, "make_vlm", lambda *args, **kwargs: FakeVLM())
    return evidence, calls


def _plan(actions=("build", "background", "background")):
    return {"objects": [{"asset_id": asset_id, "action": action, "reason": "original task intent"}
                        for asset_id, action in zip(("bottle", "laptop", "mouse"), actions, strict=True)]}


PASS = {"decision": "PASS", "issues": []}


def _run(tmp_path, evidence):
    return scope_plan.plan_object_scope("Build only bottle; laptop and mouse are background", evidence,
                                        VLMConfig(), tmp_path / "out")


def test_explicit_background_plan_and_hashed_visual_inputs(tmp_path, monkeypatch):
    evidence, calls = _fixture(tmp_path, monkeypatch, [_plan(), PASS])
    result = _run(tmp_path, evidence)
    assert result["all_resolved"]
    assert [r["action"] for r in result["objects"]] == ["build", "background", "background"]
    assert [c["stage"] for c in calls] == ["object-scope-plan", "object-scope-review"]
    manifest = read_json(tmp_path / "out/input.json")["image_manifest"]
    assert [m["asset_id"] for m in manifest] == ["bottle", "bottle", "laptop", "laptop", "mouse", "mouse"]
    assert len(calls[0]["images"]) == 6
    assert len(result["source_hashes"]) == 10
    assert (tmp_path / "out/attempt-000/review/response.json").is_file()
    assert result["input_sha256"] and result["schemas_sha256"]


def test_review_redo_feeds_back_and_corrects_background(tmp_path, monkeypatch):
    evidence, calls = _fixture(tmp_path, monkeypatch, [
        _plan(("build", "build", "build")), {"decision": "REDO", "issues": ["laptop/mouse must be background"]},
        _plan(), PASS])
    result = _run(tmp_path, evidence)
    assert result["all_resolved"] and len(result["attempts"]) == 2
    assert calls[2]["prompt"]["feedback"] == ["laptop/mouse must be background"]


@pytest.mark.parametrize("invalid", [
    {"objects": _plan()["objects"][:2]},
    {"objects": _plan()["objects"] + [_plan()["objects"][0]]},
    {"objects": [{**r, "asset_id": "invented"} if r["asset_id"] == "mouse" else r
                 for r in _plan()["objects"]]},
])
def test_ids_cannot_be_omitted_duplicated_or_invented(tmp_path, monkeypatch, invalid):
    evidence, calls = _fixture(tmp_path, monkeypatch, [invalid, invalid])
    result = _run(tmp_path, evidence)
    assert not result["all_resolved"]
    assert {r["asset_id"] for r in result["objects"]} == {"bottle", "laptop", "mouse"}
    assert all(r["action"] == "unresolved" for r in result["objects"])
    assert len(calls) == 2
    assert all(c["stage"] == "object-scope-plan" for c in calls)


def test_unresolved_cannot_pass_even_with_reviewer_pass(tmp_path, monkeypatch):
    plan = _plan(("build", "unresolved", "background"))
    evidence, calls = _fixture(tmp_path, monkeypatch, [plan, PASS, plan, PASS])
    assert not _run(tmp_path, evidence)["all_resolved"]
    assert len(calls) == 4


def test_exceptions_bounded_and_fail_closed(tmp_path, monkeypatch):
    evidence, calls = _fixture(tmp_path, monkeypatch, [RuntimeError("offline"), RuntimeError("offline")])
    result = _run(tmp_path, evidence)
    assert not result["all_resolved"] and len(result["attempts"]) == 2
    assert len(calls) == 2
    assert "offline" in result["attempts"][1]["issues"][0]


def test_review_pass_with_issues_is_not_accepted(tmp_path, monkeypatch):
    evidence, _ = _fixture(tmp_path, monkeypatch, [
        _plan(), {"decision": "PASS", "issues": ["wrong target"]},
        _plan(), {"decision": "REDO", "issues": ["still wrong"]}])
    assert not _run(tmp_path, evidence)["all_resolved"]
