"""Reference reuse must not transfer old verdicts or accept inconsistent new geometry."""

import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from roboenv_perception.components.reference_workflow import (
    _clear_downstream,
    _fit,
    _invalidate_scene_review,
    _pin,
    _review_summary,
    verify_pins,
)
from roboenv_perception.construction.asset_agents import compile_review_qa


def test_reference_rejects_different_video_before_using_old_annotations(tmp_path):
    archive = tmp_path / "user-scene-assets-20261023"
    archive.mkdir()
    perception = tmp_path / "perception"
    (perception / "inputs").mkdir(parents=True)
    (perception / "inputs/source.mp4").write_bytes(b"different source")
    with pytest.raises(ValueError, match="相同原视频"):
        _fit({"perception": perception}, tmp_path / "new", SimpleNamespace(reference_case_root=archive))
    assert not (tmp_path / "new").exists()


def test_pinned_reference_observations_detect_tampering(tmp_path):
    source = tmp_path / "camera-observations"
    source.mkdir()
    (source / "images.txt").write_text("actual camera model")
    pins = {"cameras": _pin(source)}
    verify_pins(pins)
    (source / "images.txt").write_text("changed")
    with pytest.raises(ValueError, match="参考观测发生变化"):
        verify_pins(pins)


def test_review_change_invalidates_only_new_scene_verdict_and_exposes_negative_details(tmp_path):
    import shutil

    old = tmp_path/'old'
    old.mkdir()
    (old/'scene-review.json').write_text('{"decision":"PASS"}')
    new = tmp_path/'new'
    shutil.copytree(old, new)
    _invalidate_scene_review(new)
    assert (old/'scene-review.json').exists()
    assert not (new/'scene-review.json').exists()
    summary = _review_summary({'decision': 'REDO', 'issues': ['hold_30 drift 6.03 degrees'],
                              'observations': ['Geometry matches the blueprint'],
                              'supplementary_hinge': {'followup': {'status': 'failed', 'checks': {'hold_30': False}, 'cases': []}}})
    assert summary['issues'] == ['hold_30 drift 6.03 degrees']
    assert summary['observations']
    assert summary['supplementary_hinge']['followup']['checks']['hold_30'] is False


def test_candidate_revision_invalidates_old_physics_and_reviews_in_new_snapshot(tmp_path):
    import shutil

    old = tmp_path / "old"
    for path in ["simulation/validation.json", "scene-reload/validation.json", "hinge-primary/validation.json",
                 "objects/asset-0002/review.json", "objects/asset-0002/qa.json", "scene-review.json"]:
        p = old / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"status":"passed"}')
    new = tmp_path / "new"
    shutil.copytree(old, new)
    _clear_downstream(new)
    assert (old / "simulation/validation.json").exists()
    assert (old / "objects/asset-0002/review.json").exists()
    assert not (new / "simulation").exists()
    assert not (new / "objects/asset-0002/review.json").exists()
    assert not (new / "objects/asset-0002/qa.json").exists()


def test_blueprint_qa_detects_wrong_dimensions_zero_pose_and_joint_axis(monkeypatch):
    feel = SimpleNamespace(effort=.65, motion=.7, hold="holds_anywhere")
    monkeypatch.setitem(sys.modules, "sdk", SimpleNamespace(
        ArticulationType=SimpleNamespace(REVOLUTE="revolute"),
        MotionFeel=SimpleNamespace(from_intent=lambda **_: feel),
    ))
    spec = {
        "kind": "articulated", "category": "laptop", "parts": [{"name": "base"}, {"name": "lid"}],
        "joint": {"name": "hinge", "axis": [1, 0, 0], "origin": [0, 0, .001], "lower": 0, "upper": 2.4},
        "width_m": .3126, "depth_m": .22, "lid_length_m": .21,
        "base_thickness_m": .0112, "lid_thickness_m": .0033,
    }
    namespace = {}
    exec(compile_review_qa(["topology", "hinge", "clearance"], spec), namespace)

    def part(name, size, xyz):
        return SimpleNamespace(name=name, visuals=[SimpleNamespace(
            geometry=SimpleNamespace(size=size), origin=SimpleNamespace(xyz=xyz, rpy=(0, 0, 0))
        )])

    joint = SimpleNamespace(articulation_type="revolute", parent="base", child="lid", axis=[1, 0, 0],
                            origin=SimpleNamespace(xyz=[0, 0, .001]),
                            motion_limits=SimpleNamespace(lower=0, upper=2.4),
                            motion_feel=feel, behavior=None, mimic=None)
    good = SimpleNamespace(parts=[part("base", (.3126, .22, .0112), (0, .11, -.0056)),
                                 part("lid", (.3126, .21, .0033), (0, .105, .00165))],
                           articulations=[joint])
    good.get_articulation = lambda _: good.articulations[0]

    class Context:
        def __init__(self, model):
            self.model, self.failures = model, []

        def check(self, name, condition):
            if not condition:
                self.failures.append(name)

        def fail_if_parts_overlap_in_current_pose(self):
            pass

        def fail_if_parts_overlap_in_sampled_poses(self, **kwargs):
            pass

        def report(self):
            return self.failures

    assert namespace["qa_tests"](Context(good)) == []
    wrong = deepcopy(good)
    wrong.parts[0].visuals[0].geometry.size = (.1, .22, .0112)
    wrong.parts[1].visuals[0].origin.xyz = (0, 0, .1)
    wrong.articulations[0].axis = [0, 1, 0]
    wrong.get_articulation = lambda _: wrong.articulations[0]
    failures = namespace["qa_tests"](Context(wrong))
    assert {"base_blueprint_dimensions", "lid_canonical_origin", "hinge_axis"} <= set(failures)
