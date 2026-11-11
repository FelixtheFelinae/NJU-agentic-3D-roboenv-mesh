"""Controller tests use synthetic components, not model or physical evidence."""

import sys
from pathlib import Path

import pytest
from PIL import Image

from roboenv_perception.artifacts.registry import tree_index
from roboenv_perception.components.sources import source_path
from roboenv_perception.harness.workflow import Workflow
from roboenv_perception.harness.workflow_evidence import Sampling
from roboenv_perception.io import read_json, sha256, write_json
from roboenv_perception.workbench.config import Settings


class Components:
    def __init__(self):
        self.calls = []
        self.geometry_fault = True
        self.visual_fault = False
        self.crash = None
        self.unknown = False
        self.repair_target = "geometry"

    def __call__(self, operation, sources, output, settings, task, **kwargs):
        for binding in sources.values():
            source_path(binding)
        self.calls.append(operation)
        if self.crash == operation:
            self.crash = None
            raise RuntimeError("synthetic interruption")
        output.mkdir(parents=True)
        if operation == "perceive":
            write_json(output / "perception/marker.json", {"sampling": kwargs["params"]})
            return {"perception": "perception"}, "unverified"
        if operation == "evidence":
            root = output / "evidence"
            write_json(root / "objects.json", {"objects": [
                {"asset_id": "a", "object_id": "laptop", "evidence_ref": "object.json", "status": "ready_for_design"},
            ]})
            Image.new("RGB", (32, 24), "green").save(root / "frame.png")
            Image.new("L", (32, 24), 255).save(root / "mask.png")
            write_json(root / "object.json", {
                "status": "ready_for_design", "category": "laptop", "views": [{
                    "frame_id": "frame-0", "ref_id": "view-0", "frame_ref": "frame.png",
                    "context_ref": "frame.png", "mask_ref": "mask.png",
                }],
            })
            return {"evidence": "evidence"}, "unverified"
        if operation == "scope_apply":
            write_json(output / "scope/scope_plan.json", {"all_resolved": True, **kwargs["params"]})
            return {"scope": "scope"}, "explicit_selection"
        if operation == "blueprint":
            value = {
                "object_id": "laptop", "kind": "rigid",
                "frame_convention": "metres; Z-up; front=+Y; local object frame",
                "parts": [{"name": "body", "parent": None, "role": "fixture",
                           "geometry": "box", "material_appearance": "gray",
                           "collision_intent": "solid", "evidence_refs": ["view-0"]}],
                "acceptance": ["synthetic fixture only"],
            }
            if hasattr(self, "canonical"):
                value = self.canonical
            write_json(output / "blueprint/blueprint.json", value)
            write_json(output / "blueprint/selfcheck.json", {"issues": ["synthetic description fault"] if self.unknown else []})
            return {"blueprint": "blueprint/blueprint.json"}, "needs_revision" if self.unknown else "ready"
        if operation == "blueprint_review":
            write_json(output / "review.json", {"decision": "PASS", "findings": []})
            return {"blueprint_review": "review.json"}, "review_passed"
        if operation == "geometry":
            if hasattr(self, "geometry_factory"):
                write_json(output / "recipe.json", self.geometry_factory(sources))
                return {"recipe": "recipe.json"}, "unverified"
            write_json(output / "recipe.json", {"repair": "previous" in sources})
            return {"recipe": "recipe.json"}, "unverified"
        if operation == "geometry_audit":
            if getattr(self, "real_geometry_audit", False):
                from roboenv_perception.construction.assembly_blueprint import parse_blueprint
                from roboenv_perception.harness.geometric_verifier import persist_verification
                from roboenv_perception.harness.geometry import GeometryRecipe

                report = persist_verification(output / "check",
                    parse_blueprint(read_json(source_path(sources["blueprint"]))),
                    GeometryRecipe.model_validate(read_json(source_path(sources["recipe"]))))
                return {"geometric_verification": "check"}, report["status"]
            recipe = read_json(source_path(sources["recipe"]))
            status = "failed" if self.geometry_fault and not recipe["repair"] else "passed"
            write_json(output / "check/geometric_verification.json", {
                "status": status, "feedback": [{"message": "wrong shaft axis", "repair_target": self.repair_target}] if status == "failed" else [],
            })
            return {"geometric_verification": "check"}, status
        if operation == "assembly_audit":
            write_json(output / "assembly/assembly_validation.json", {"status": "geometrically_plausible"})
            return {"assembly": "assembly"}, "geometrically_plausible"
        if operation == "build":
            write_json(output / "built/candidate.json", {"visual_previews": []})
            return {"built": "built"}, "unverified"
        if operation == "geometry_review":
            write_json(output / "assets/assets.json", {"objects": [{"review": {
                "decision": "REDO" if self.visual_fault else "PASS",
                "issues": ["screen distorted"] if self.visual_fault else [],
            }}]})
            return {"assets": "assets"}, "unverified" if self.visual_fault else "review_passed"
        if operation == "scene":
            write_json(output / "scene/scene_result.json", {"status": "assembled"})
            return {"scene": "scene"}, "unverified"
        if operation == "physics":
            write_json(output / "physics/physics.json", {"status": "failed", "issues": ["drift"]})
            return {"physics": "physics"}, "failed"
        raise AssertionError(operation)


def setup(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("")
    source = tmp_path / "original.png"
    Image.new("RGB", (32, 24), "green").save(source)
    settings = Settings(dsh_root=tmp_path, perception_config=config, construction_config=config,
                        artisan_python=Path(sys.executable), blender=Path(sys.executable),
                        isaac_python=Path(sys.executable), physics=True)
    components = Components()
    controller = Workflow(tmp_path / "workflow", settings, "reconstruct laptop",
                          [{"name": source.name, "path": str(source), "sha256": sha256(source)}],
                          {}, execute=components)
    return controller, components


def advance(w, tmp_path, action=None, **fields):
    w.used = {}  # Each call models a fresh worker and rechecks receipts.
    call = tmp_path / ("call-" + str(len(list(tmp_path.glob("call-*")))))
    if action:
        decision = {"action": action, **fields}
        if action != "resume":
            decision["revision"] = w.state["revision"]
    else:
        decision = None
    return w.run(call, decision)


def target(w, tmp_path):
    assert advance(w, tmp_path)["phase"] == "target"
    assert advance(w, tmp_path, "select_target", object_id="laptop", reason="observed body and screen")["phase"] == "structure"


def allow_backend(monkeypatch):
    monkeypatch.setattr(Workflow, "preflight_build", lambda self: None)


def test_geometry_repair_reuses_upstream_and_checks_new_candidate(tmp_path, monkeypatch):
    allow_backend(monkeypatch)
    w, c = setup(tmp_path)
    target(w, tmp_path)
    result = advance(w, tmp_path, "accept_structure", reason="evidence supports body and screen")
    assert result["phase"] == "completed" and result["physics_status"] == "not_run"
    assert c.calls.count("perceive") == c.calls.count("blueprint") == c.calls.count("build") == 1
    assert c.calls.count("geometry") == c.calls.count("geometry_audit") == 2
    assert all(x["candidate_role"] == "diagnostic_only" for x in result["critic"])
    before = list(c.calls)
    advance(w, tmp_path, "resume")
    assert c.calls == before


def test_interrupted_build_resumes_and_stale_decisions_cannot_repeat(tmp_path, monkeypatch):
    allow_backend(monkeypatch)
    w, c = setup(tmp_path)
    target(w, tmp_path)
    revision = w.state["revision"]
    c.crash = "build"
    with pytest.raises(RuntimeError, match="interruption"):
        advance(w, tmp_path, "accept_structure", reason="supported")
    with pytest.raises(ValueError, match="过期"):
        w.run(tmp_path / "stale", {"action": "accept_structure", "revision": revision, "reason": "repeat"})
    assert advance(w, tmp_path, "resume")["phase"] == "completed"
    assert c.calls.count("geometry") == 2 and c.calls.count("build") == 2
    assert c.calls.count("blueprint") == 1


@pytest.mark.parametrize("role", ["perception", "blueprint"])
def test_tampered_upstream_is_rejected_on_resume(tmp_path, monkeypatch, role):
    allow_backend(monkeypatch)
    w, _ = setup(tmp_path)
    target(w, tmp_path)
    receipt = w.state[role]
    path = next(p for p in w.path(receipt["ref"]).rglob("*.json"))
    path.write_text("{}")
    with pytest.raises(ValueError, match="发生变化"):
        advance(w, tmp_path, "accept_structure", reason="supported")


def test_visual_fault_returns_to_structure_and_no_fake_observation(tmp_path, monkeypatch):
    allow_backend(monkeypatch)
    w, c = setup(tmp_path)
    target(w, tmp_path)
    c.visual_fault = True
    result = advance(w, tmp_path, "accept_structure", reason="supported")
    assert result["phase"] == "recovery"
    observations = set(w.state["observed_frames"])
    c.visual_fault = False
    assert advance(w, tmp_path, "revise_structure", reason="check screen proportions")["phase"] == "structure"
    assert c.calls.count("perceive") == 1
    assert set(w.state["observed_frames"]) == observations
    assert advance(w, tmp_path, "accept_structure", reason="new structure supported")["phase"] == "completed"


def test_semantic_hard_failure_never_reaches_build(tmp_path, monkeypatch):
    allow_backend(monkeypatch)
    w, c = setup(tmp_path)
    c.repair_target = "blueprint"
    target(w, tmp_path)
    result = advance(w, tmp_path, "accept_structure", reason="supported")
    assert result["phase"] == "recovery"
    assert c.calls.count("geometry") == 1 and "build" not in c.calls


def test_selfcheck_redo_persisted_and_round_budget_bounded(tmp_path):
    w, c = setup(tmp_path)
    c.unknown = True
    advance(w, tmp_path)
    result = advance(w, tmp_path, "select_target", object_id="laptop", reason="actual object")
    assert result["phase"] == "recovery" and result["critic"][-1]["issues"]
    assert "blueprint_review" not in c.calls
    for _ in range(w.settings.max_revisions):
        result = advance(w, tmp_path, "revise_structure", reason="recheck structure")
    assert result["allowed_actions"] == ["stop"]
    assert advance(w, tmp_path, "stop", reason="insufficient evidence")["phase"] == "stopped"


def test_numeric_physics_failure_does_not_become_success(tmp_path, monkeypatch):
    allow_backend(monkeypatch)
    w, _ = setup(tmp_path)
    w.options["physics"] = True
    target(w, tmp_path)
    result = advance(w, tmp_path, "accept_structure", reason="supported")
    assert result["phase"] == "physical_recovery" and result["physics_status"] == "failed"
    assert result["allowed_actions"] == ["stop"]


def test_config_and_input_changes_need_new_workflow(tmp_path):
    w, _ = setup(tmp_path)
    advance(w, tmp_path)
    w.settings.construction_config.write_text("# edited")
    with pytest.raises(ValueError, match="配置"):
        advance(w, tmp_path, "resume")


@pytest.mark.parametrize("sampling", [
    {"max_frames": 65, "sample_fps": 1},
    {"max_frames": 8, "sample_fps": float("nan")},
    {"max_frames": 8, "sample_fps": 1, "replay_manifest": "/private"},
    {"max_frames": 8, "sample_fps": 1, "start_seconds": 9},
    {"max_frames": 8, "sample_fps": 1, "reference_frame": 9},
])
def test_sampling_is_bounded(sampling):
    with pytest.raises(ValueError):
        Sampling.decision(sampling, 8)


def test_no_new_selected_source_frames_blocks_evidence_revisit(tmp_path, monkeypatch):
    from roboenv_perception.harness import workflow_evidence as ev
    w, c = setup(tmp_path)
    w.inputs[0]["name"] = "video.mp4"  # Synthetic decoder metadata below.
    w.video = True

    def overview(source, output):
        write_json(output / "overview.json", {"duration_seconds": 10, "source_sha256": sha256(source)})
        Image.new("RGB", (80, 40)).save(output / "source-overview.jpg")

    original = ev.observations
    monkeypatch.setattr(ev, "overview", overview)
    monkeypatch.setattr(ev, "observations", lambda e, p, v: original(e, p, False))
    assert advance(w, tmp_path)["phase"] == "sampling"
    advance(w, tmp_path, "sample_video", sampling={"max_frames": 8, "sample_fps": 1})
    advance(w, tmp_path, "select_target", object_id="laptop", reason="actual mask")
    advance(w, tmp_path, "revisit_video", sampling={"max_frames": 8, "sample_fps": 2},
            reason="need hinge evidence")
    result = advance(w, tmp_path, "select_target", object_id="laptop", reason="same object in current mask")
    assert result["phase"] == "target" and "没有增加" in result["reason"]
    assert c.calls.count("blueprint") == 1
    assert len(w.state["observed_frames"]) == 1


def test_output_snapshot_has_no_mutable_dependency(tmp_path):
    w, _ = setup(tmp_path)
    first = advance(w, tmp_path)
    output = next(tmp_path.glob("call-*"))
    digest = tree_index(output)
    advance(w, tmp_path, "select_target", object_id="laptop", reason="actual mask")
    assert tree_index(output) == digest
    assert first["phase"] == "target"


def test_explicit_build_hypothesis_uses_derivative_and_keeps_real_geometry_gate(tmp_path):
    from test_workflow_p02 import boxes, laptop, plan

    from roboenv_perception.construction.assembly_blueprint import parse_blueprint

    w, c = setup(tmp_path)
    bp = laptop().model_dump(mode="json")
    bp["object_id"] = bp["asset_brief"]["object_id"] = "laptop"
    c.canonical = bp
    c.real_geometry_audit = True
    c.geometry_factory = lambda sources: boxes(parse_blueprint(read_json(
        source_path(sources["blueprint"])))).model_dump(mode="json")
    target(w, tmp_path)
    canonical = w.ref(w.state["blueprint"], "blueprint")
    source_hash = sha256(canonical)
    raw = plan(parse_blueprint(read_json(canonical)), source_hash=source_hash)
    # This synthetic evidence has just view-0, so bind the actual reference.
    for parameter in raw["parameters"]:
        parameter["evidence_refs"] = ["view-0"]
    result = advance(w, tmp_path, "propose_build", reason="independent trial, not a measurement", hypothesis=raw)
    assert result["phase"] == "structure"
    assert result["construction_hypothesis"]["measurement_status"] == "not_measured"
    assert sha256(canonical) == source_hash
    assert read_json(canonical)["joints"][0]["travel"]["value"] is None
    result = advance(w, tmp_path, "accept_structure", reason="test real geometric gate")
    assert result["phase"] == "recovery"
    assert c.calls.count("geometry") == c.calls.count("geometry_audit") == 0
    assert "build" not in c.calls  # Undefined hinge surfaces remain unresolved.
    assert result["critic"][-1]["stage"] == "structure_audit"


def test_stale_build_hypothesis_does_not_consume_revision(tmp_path):
    from test_workflow_p02 import laptop, plan

    w, c = setup(tmp_path)
    bp = laptop().model_dump(mode="json")
    bp["object_id"] = bp["asset_brief"]["object_id"] = "laptop"
    c.canonical = bp
    target(w, tmp_path)
    revision = w.state["revision"]
    with pytest.raises(ValueError, match="当前语义"):
        advance(w, tmp_path, "propose_build", reason="stale", hypothesis=plan(laptop()))
    assert read_json(w.state_path)["revision"] == revision


def test_fusion_runs_inside_controller_and_resumes_without_repeating_perception(tmp_path, monkeypatch):
    import shutil

    from test_workflow_p02 import origin

    from roboenv_perception.harness import workflow_evidence as ev

    w, c = setup(tmp_path)
    w.inputs[0]["name"] = "video.mp4"  # Controlled fixture outputs, not video/model evidence.
    w.video = True
    fixtures = [
        origin(tmp_path, 0, [0., 1., 2.], video_hash=w.inputs[0]["sha256"]),
        origin(tmp_path, 1, [2., 3., 4.], video_hash=w.inputs[0]["sha256"]),
    ]
    counts = {"perceive": 0}
    def execute(operation, sources, output, *args, **kwargs):
        if operation == "perceive":
            item = fixtures[counts["perceive"]]
            counts["perceive"] += 1
            shutil.copytree(item["perception"], output / "perception")
            return {"perception": "perception"}, "unverified"
        if operation == "evidence":
            item = fixtures[counts["perceive"] - 1]
            shutil.copytree(item["evidence"], output / "evidence")
            return {"evidence": "evidence"}, "unverified"
        return c(operation, sources, output, *args, **kwargs)
    w.execute = execute
    def overview(source, output):
        write_json(output / "overview.json", {"duration_seconds": 10, "source_sha256": sha256(source)})
        Image.new("RGB", (80, 40)).save(output / "source-overview.jpg")
    monkeypatch.setattr(ev, "overview", overview)
    advance(w, tmp_path)
    advance(w, tmp_path, "sample_video", sampling={"sample_fps": 1, "max_frames": 3})
    advance(w, tmp_path, "select_target", object_id="track-0", reason="front mask")
    frozen = tree_index(tmp_path / "call-2")
    result = advance(w, tmp_path, "revisit_video",
                     sampling={"start_seconds": 2, "sample_fps": 1, "max_frames": 3},
                     reason="need rear view")
    assert result["retained_evidence"][0]["ticket"] == "e0:view-001"
    fuse = ev.fuse
    crashed = []
    def crash(*args, **kwargs):
        fuse(*args, **kwargs)
        if not crashed:
            crashed.append(True)
            raise RuntimeError("interrupt before fusion receipt")
    monkeypatch.setattr(ev, "fuse", crash)
    with pytest.raises(RuntimeError, match="fusion receipt"):
        advance(w, tmp_path, "select_target", object_id="track-1", reason="same laptop, continuous views")
    result = advance(w, tmp_path, "resume")
    assert result["phase"] == "structure"
    assert counts["perceive"] == 2 and c.calls.count("blueprint") == 2
    assert w.state["target"]["object_id"] == "track-0" and result["latest_track_id"] == "track-1"
    assert len(w.state["target"]["views"]) == 5 and len(w.state["observed_frames"]) == 5
    assert tree_index(tmp_path / "call-2") == frozen
    assert len(w.state["evidence_origins"]) == 2
