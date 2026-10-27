"""Contract tests use a small trusted fake worker; Isaac is not required here."""

import sys
from pathlib import Path

import pytest

from roboenv_perception.harness.isaac_generic_worker import (
    _rigid_baseline_metrics,
    _rigid_driven_metrics,
    _subtree_prims,
    _unique_subtree_prim,
)
from roboenv_perception.harness.physics import (
    InteractionFamily,
    PhysicsProtocol,
    PhysicsTask,
    run_physics_task,
    sha256,
    verify_physics_report,
)


def rigid_protocol():
    return PhysicsProtocol(
        family=InteractionFamily.RIGID,
        target_prim="/World/Object",
        min_response=0.01,
        max_baseline=0.002,
        force_world_N=(2.0, 0.0, 0.0),
    )


def test_prim_selection_handles_nested_bodies_and_sibling_names():
    class Prim:
        def __init__(self, path, role):
            self.path = path
            self.role = role

        def GetPath(self):
            return self.path

    class Stage:
        def __init__(self, prims):
            self.prims = prims

        def Traverse(self):
            return self.prims

    scene = Stage([
        Prim("/World/Assets/Asset_tablet_fixture", "root"),
        Prim("/World/Assets/Asset_tablet_fixture/body", "rigid"),
        Prim("/World/Assets/Asset_tablet_fixture_2/body", "rigid"),
    ])
    selected = _unique_subtree_prim(
        scene, "/World/Assets/Asset_tablet_fixture",
        lambda prim: prim.role == "rigid", "rigid body")
    assert selected.GetPath() == "/World/Assets/Asset_tablet_fixture/body"
    assert len(_subtree_prims(scene, "/World/Assets", lambda prim: prim.role == "rigid")) == 2
    with pytest.raises(RuntimeError, match="expected exactly one, found 2"):
        _unique_subtree_prim(scene, "/World/Assets", lambda prim: prim.role == "rigid", "body")


def report_for(protocol, scene, hashes):
    checks = {
        "loaded_exported_usd": True,
        "finite_states": True,
        "baseline_stable": True,
        "driven_response": True,
        "source_unchanged": True,
    }
    if protocol.family == InteractionFamily.COUPLED:
        checks.update(disconnected_control=True, ratio_consistency=True,
                      control_driver_responds=True)
    if protocol.family == InteractionFamily.RIGID:
        checks.update(vertical_stability=True, vertical_velocity_stability=True,
                      orientation_stability=True, driven_support_height=True)
        if protocol.max_driven_tilt_rad is not None:
            checks["driven_upright"] = True
    return {
        "status": "passed",
        "checks": checks,
        "artifact": {
            "scene_path": str(scene.resolve()),
            "scene_sha256": hashes[str(scene.resolve())],
            "source_hashes": hashes,
        },
        "protocol": {
            "family": protocol.family.value,
            "loaded_exported_usd": True,
            "state_writes_during_measurement": False,
            "measurement_source": "simulated_state",
            "actuation_mode": "external_force" if protocol.family == InteractionFamily.RIGID else "external_torque",
            "target_prim": protocol.target_prim,
            "fixture_description": protocol.fixture_description,
            "configured_protocol": protocol.as_dict(),
            "upright_required": protocol.max_driven_tilt_rad is not None,
            "native_mimic_verified": protocol.family == InteractionFamily.COUPLED,
        },
        "measurements": {
            "unit": "m" if protocol.family == InteractionFamily.RIGID else "rad",
            "baseline": 0.001,
            "response": 0.02,
            **({"support_evidence": "root_trajectory_proxy", "baseline_planar_excursion_m": 0.001,
                "baseline_z_span_m": 0.001,
                "baseline_max_abs_vertical_speed_m_s": 0.001,
                "baseline_orientation_change_rad": 0.001,
                "driven_max_drop_m": 0.001, "driven_max_tilt_rad": 0.001}
               if protocol.family == InteractionFamily.RIGID else {}),
        },
    }


def test_physics_report_requires_causal_motion_from_the_exported_scene(tmp_path):
    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    hashes = {str(scene.resolve()): sha256(scene)}
    protocol = rigid_protocol()
    report = report_for(protocol, scene, hashes)
    assert verify_physics_report(protocol, scene, hashes, report) == ()

    report["protocol"]["state_writes_during_measurement"] = True
    assert "joint/root state writes" in " ".join(verify_physics_report(protocol, scene, hashes, report))
    report["protocol"]["state_writes_during_measurement"] = False
    report["protocol"]["actuation_mode"] = "set_joint_angle"
    assert "actuation mode" in " ".join(verify_physics_report(protocol, scene, hashes, report))
    report["protocol"]["actuation_mode"] = "external_force"
    report["measurements"]["response"] = 0.0
    assert "below its threshold" in " ".join(verify_physics_report(protocol, scene, hashes, report))


def test_report_rejects_stale_scene_and_changed_test_conditions(tmp_path):
    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    hashes = {str(scene.resolve()): sha256(scene)}
    protocol = rigid_protocol()
    report = report_for(protocol, scene, hashes)
    report["artifact"]["scene_sha256"] = "0" * 64
    assert "exported USD" in " ".join(verify_physics_report(protocol, scene, hashes, report))
    report = report_for(protocol, scene, hashes)
    report["protocol"]["configured_protocol"]["force_world_N"] = [20.0, 0.0, 0.0]
    assert "frozen test conditions" in " ".join(verify_physics_report(protocol, scene, hashes, report))


def test_rigid_baseline_rejects_fall_and_tip_even_when_horizontal_push_passes(tmp_path):
    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    hashes = {str(scene.resolve()): sha256(scene)}
    protocol = rigid_protocol()
    traces = [
        {"phase": "baseline", "root_position_m": [0, 0, 1],
         "root_velocity_m_s": [0, 0, 0], "root_quaternion_wxyz": [1, 0, 0, 0]},
        {"phase": "baseline", "root_position_m": [0, 0, 0.8],
         "root_velocity_m_s": [0, 0, -1], "root_quaternion_wxyz": [0.98, 0.2, 0, 0]},
        {"phase": "baseline", "root_position_m": [0, 0, 0.5],
         "root_velocity_m_s": [0, 0, -2], "root_quaternion_wxyz": [0.9, 0.4359, 0, 0]},
    ]
    metrics = _rigid_baseline_metrics(traces)
    assert metrics["baseline_planar_excursion_m"] == 0
    assert metrics["baseline_z_span_m"] == pytest.approx(0.5)
    assert metrics["baseline_max_abs_vertical_speed_m_s"] == 2
    assert metrics["baseline_orientation_change_rad"] > 0.15
    report = report_for(protocol, scene, hashes)
    report["measurements"].update(metrics)
    issues = " ".join(verify_physics_report(protocol, scene, hashes, report))
    assert "vertical drift" in issues
    assert "vertical speed" in issues
    assert "orientation change" in issues


def test_rigid_baseline_uses_full_planar_trajectory():
    traces = [
        {"phase": "baseline", "root_position_m": [0, 0, 1],
         "root_velocity_m_s": [0, 0, 0], "root_quaternion_wxyz": [1, 0, 0, 0]},
        {"phase": "baseline", "root_position_m": [0, 0.03, 1],
         "root_velocity_m_s": [0, 0, 0], "root_quaternion_wxyz": [1, 0, 0, 0]},
        {"phase": "baseline", "root_position_m": [0, 0, 1],
         "root_velocity_m_s": [0, 0, 0], "root_quaternion_wxyz": [1, 0, 0, 0]},
    ]
    assert _rigid_baseline_metrics(traces)["baseline_planar_excursion_m"] == pytest.approx(0.03)


def test_coupled_requires_control_and_ratio_measurements(tmp_path):
    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    hashes = {str(scene.resolve()): sha256(scene)}
    protocol = PhysicsProtocol(
        family=InteractionFamily.COUPLED,
        target_prim="/World/Bicycle",
        joint_name="pedal",
        follower_joint_name="rear_spin",
        moving_body_name="crank",
        base_body_name="frame",
        min_response=0.02,
        max_baseline=0.01,
        target_ratio=2.5,
        max_ratio_residual=0.05,
        max_disconnected_response=0.01,
    )
    report = report_for(protocol, scene, hashes)
    assert "disconnected control" in " ".join(verify_physics_report(protocol, scene, hashes, report))
    report["measurements"].update(disconnected_response=0.005, ratio_residual=0.02,
                                  target_ratio=2.5, control_driver_response=0.03,
                                  control_prim=protocol.disconnected_control_prim)
    assert verify_physics_report(protocol, scene, hashes, report) == ()
    report["measurements"]["disconnected_response"] = 0.2
    assert "disconnected control" in " ".join(verify_physics_report(protocol, scene, hashes, report))


def _fake_worker(path: Path):
    path.write_text(
        """import argparse, json, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--request', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--mode', default='pass')
a = p.parse_args()
request = json.loads(a.request.read_text())
a.output.mkdir(parents=True, exist_ok=False)
if a.mode == 'sleep':
    time.sleep(5)
if a.mode == 'no-report':
    sys.exit(0)
protocol = request['protocol']
hashes = request['source_hashes']
scene = request['scene_usd']
report = {
    'status': 'passed',
    'checks': {name: True for name in ['loaded_exported_usd', 'finite_states', 'baseline_stable', 'driven_response', 'source_unchanged', 'vertical_stability', 'vertical_velocity_stability', 'orientation_stability', 'driven_support_height']},
    'artifact': {'scene_path': scene, 'scene_sha256': hashes[scene], 'source_hashes': hashes},
    'protocol': {
        'family': protocol['family'], 'loaded_exported_usd': True,
        'state_writes_during_measurement': a.mode in {'angle-write', 'hang-invalid'},
        'measurement_source': 'simulated_state', 'actuation_mode': 'external_force',
        'target_prim': protocol['target_prim'],
        'fixture_description': protocol['fixture_description'],
        'configured_protocol': protocol,
        'upright_required': protocol['max_driven_tilt_rad'] is not None,
    },
    'measurements': {'unit': 'm', 'baseline': 0.001, 'response': 0.02,
                     'support_evidence': 'root_trajectory_proxy',
                     'baseline_planar_excursion_m': 0.001, 'baseline_z_span_m': 0.001,
                     'baseline_max_abs_vertical_speed_m_s': 0.001,
                     'baseline_orientation_change_rad': 0.001,
                     'driven_max_drop_m': 0.001, 'driven_max_tilt_rad': 0.001},
}
(a.output / 'validation.json').write_text(json.dumps(report))
if a.mode in {'hang-report', 'hang-invalid'}:
    time.sleep(30)
sys.exit(3 if a.mode == 'nonzero' else 0)
"""
    )


def _task(tmp_path, mode="pass", timeout=5):
    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    worker = tmp_path / "worker.py"
    _fake_worker(worker)
    task = PhysicsTask(
        scene_usd=scene,
        output_dir=tmp_path / "run",
        python_executable=Path(sys.executable),
        protocol=rigid_protocol(),
        worker_script=worker,
        trusted_worker_sha256=sha256(worker),
        timeout_s=timeout,
        worker_args=("--mode", mode),
    )
    return task


def test_subprocess_runner_accepts_bound_report_and_requires_fresh_output(tmp_path):
    task = _task(tmp_path)
    result = run_physics_task(task)
    assert result.passed
    assert result.report_sha256 == sha256(task.output_dir / "validation.json")
    assert result.log_path.is_file()
    with pytest.raises(ValueError, match="fresh"):
        run_physics_task(task)


@pytest.mark.parametrize('mode,passed', [('hang-report', True), ('hang-invalid', False)])
def test_shutdown_after_report_still_audits_complete_evidence(tmp_path, monkeypatch, mode, passed):
    monkeypatch.setattr('roboenv_perception.harness.physics._REPORT_SHUTDOWN_GRACE_S', .01)
    result = run_physics_task(_task(tmp_path, mode))
    assert result.passed is passed
    assert result.shutdown_status == 'terminated_after_report'
    if not passed:
        assert 'state writes' in ' '.join(result.issues)


@pytest.mark.parametrize("mode,expected", [
    ("angle-write", "state writes"),
    ("no-report", "validation.json"),
    ("nonzero", "code 3"),
    ("sleep", "timeout"),
])
def test_runner_rejects_false_success_and_incomplete_runs(tmp_path, mode, expected):
    result = run_physics_task(_task(tmp_path, mode, timeout=0.15 if mode == "sleep" else 5))
    assert result.status == "failed"
    assert expected in " ".join(result.issues)


def test_rotary_protocol_requires_a_named_moving_body():
    with pytest.raises(ValueError, match="moving_body_name"):
        PhysicsProtocol(
            family=InteractionFamily.ROTARY,
            target_prim="/World/Wheel",
            joint_name="spin",
            min_response=0.1,
            max_baseline=0.01,
        )
    protocol = PhysicsProtocol(
        family="rotary",
        target_prim="/World/Wheel",
        joint_name="spin",
        moving_body_name="wheel",
        min_response=0.1,
        max_baseline=0.01,
    )
    assert protocol.as_dict()["family"] == "rotary"


def test_ascii_usd_references_are_bound_recursively(tmp_path):
    asset = tmp_path / "assets" / "object.usda"
    asset.parent.mkdir()
    texture = asset.parent / "texture.png"
    texture.write_bytes(b"placeholder")
    asset.write_text('#usda 1.0\nasset inputs:file = @./texture.png@\n')
    scene = tmp_path / "scene.usda"
    scene.write_text('#usda 1.0\nprepend references = @./assets/object.usda@\n')
    worker = tmp_path / "worker.py"
    _fake_worker(worker)
    task = PhysicsTask(scene, tmp_path / "run", Path(sys.executable), rigid_protocol(),
                       worker_script=worker, trusted_worker_sha256=sha256(worker))
    result = run_physics_task(task)
    assert result.passed
    assert set(result.source_hashes) == {str(path.resolve()) for path in [scene, asset, texture]}


def test_nonportable_usd_reference_is_rejected_before_launch(tmp_path):
    scene = tmp_path / "scene.usda"
    scene.write_text('#usda 1.0\nprepend references = @../outside.usd@\n')
    worker = tmp_path / "worker.py"
    _fake_worker(worker)
    task = PhysicsTask(scene, tmp_path / "run", Path(sys.executable), rigid_protocol(),
                       worker_script=worker, trusted_worker_sha256=sha256(worker))
    with pytest.raises(ValueError, match="escapes the scene package"):
        run_physics_task(task)


def test_binary_usd_asset_package_files_are_hash_bound(tmp_path):
    package = tmp_path / "assets" / "item"
    package.mkdir(parents=True)
    crate = package / "object.usd"
    crate.write_bytes(b"PXR-USDCfake")
    mesh = package / "meshes" / "part.obj"
    mesh.parent.mkdir()
    mesh.write_text("v 0 0 0\n")
    scene = tmp_path / "scene.usda"
    scene.write_text('#usda 1.0\nprepend references = @./assets/item/object.usd@\n')
    worker = tmp_path / "worker.py"
    _fake_worker(worker)
    task = PhysicsTask(scene, tmp_path / "run", Path(sys.executable), rigid_protocol(),
                       worker_script=worker, trusted_worker_sha256=sha256(worker))
    result = run_physics_task(task)
    assert result.passed
    assert str(mesh.resolve()) in result.source_hashes


def test_rigid_release_fall_and_tip_rejected_despite_stable_baseline(tmp_path):
    import math

    baseline = {"phase": "baseline", "root_position_m": [0, 0, 1],
                "root_quaternion_wxyz": [1, 0, 0, 0]}
    # Yaw during a horizontal slide is allowed; tipping on release is not.
    yaw = {"phase": "drive", "root_position_m": [.1, 0, 1],
           "root_quaternion_wxyz": [0, 0, 0, 1]}
    release = {"phase": "release", "root_position_m": [.2, 0, .9],
               "root_quaternion_wxyz": [math.cos(.4), math.sin(.4), 0, 0]}
    assert _rigid_driven_metrics([baseline, yaw])["driven_max_tilt_rad"] == 0
    metrics = _rigid_driven_metrics([baseline, yaw, release])
    assert metrics["driven_max_drop_m"] == pytest.approx(.1)
    assert metrics["driven_max_tilt_rad"] == pytest.approx(.8)
    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    hashes = {str(scene.resolve()): sha256(scene)}
    protocol = rigid_protocol()
    report = report_for(protocol, scene, hashes)
    protocol = PhysicsProtocol(**{**protocol.as_dict(), "max_driven_tilt_rad": .5})
    report = report_for(protocol, scene, hashes)
    report["measurements"].update(metrics)
    issues = " ".join(verify_physics_report(protocol, scene, hashes, report))
    assert "driven vertical drop" in issues
    assert "driven tilt" in issues


def test_rigid_upright_constraint_is_optional_and_explicit(tmp_path):
    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    hashes = {str(scene.resolve()): sha256(scene)}
    protocol = rigid_protocol()
    report = report_for(protocol, scene, hashes)
    report["measurements"]["driven_max_tilt_rad"] = .636
    assert "driven_upright" not in report["checks"]
    assert report["protocol"]["upright_required"] is False
    assert verify_physics_report(protocol, scene, hashes, report) == ()
    required = PhysicsProtocol(**{**protocol.as_dict(), "max_driven_tilt_rad": .5})
    report = report_for(required, scene, hashes)
    report["measurements"]["driven_max_tilt_rad"] = .636
    assert "driven tilt" in " ".join(verify_physics_report(required, scene, hashes, report))
    del report["checks"]["driven_upright"]
    assert "required driven upright" in " ".join(verify_physics_report(required, scene, hashes, report))
