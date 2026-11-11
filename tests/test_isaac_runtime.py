import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from roboenv_perception.execution import isaac_runtime


def test_lab3_physx_probe_preserves_solver_and_actuation(monkeypatch):
    monkeypatch.setattr(isaac_runtime.metadata, "version", lambda _: "3.0.0")
    modules = {
        "isaaclab.sim": SimpleNamespace(SimulationCfg=lambda **kw: kw),
        "isaaclab_physx.physics": SimpleNamespace(PhysxCfg=lambda **kw: kw),
    }
    monkeypatch.setattr(isaac_runtime, "import_module", modules.__getitem__)
    solver = isaac_runtime.physx_config(solver_type=1, enable_external_forces_every_iteration=True)
    config = isaac_runtime.simulation_config(device="cpu", dt=1 / 240, physx=solver)
    assert config["physics"] == solver
    assert config["dt"] == 1 / 240
    assert config["use_newton_actuators"] is False
    assert "physx" not in config
    with pytest.raises(ValueError, match="another physics backend"):
        isaac_runtime.simulation_config(physics="newton")


def test_lab2_keeps_existing_physx_configuration(monkeypatch):
    # Lab 2 reports its internal extension version, rather than release v2.x.
    monkeypatch.setattr(isaac_runtime.metadata, "version", lambda _: "0.54.4")
    monkeypatch.setattr(
        isaac_runtime, "import_module",
        lambda _: SimpleNamespace(SimulationCfg=lambda **kw: kw, PhysxCfg=lambda **kw: kw),
    )
    solver = isaac_runtime.physx_config(solver_type=1)
    assert isaac_runtime.simulation_config(device="cpu", physx=solver) == {
        "device": "cpu", "physx": {"solver_type": 1},
    }


def test_bundled_probe_binds_runtime_configuration_source(tmp_path):
    from roboenv_perception.harness.physics import (
        PhysicsProtocol,
        PhysicsTask,
        _source_hashes,
        sha256,
    )

    scene = tmp_path / "scene.usda"
    scene.write_text("#usda 1.0\n")
    task = PhysicsTask(
        scene, tmp_path / "result", Path("/unused/python"),
        PhysicsProtocol("rigid", "/World/Probe", 0.01, 0.002),
    )
    helper = Path(isaac_runtime.__file__).resolve()
    assert _source_hashes(task)[str(helper)] == sha256(helper)


@pytest.mark.parametrize("version", ["0.54.4", "3.0.0"])
def test_quaternion_roundtrip_and_state_report_contract(monkeypatch, version):
    monkeypatch.setattr(isaac_runtime.metadata, "version", lambda _: version)
    # Asymmetric quaternion catches a scalar/vector permutation, unlike identity.
    quat = [0.5, -0.5, 0.5, -0.5]
    native = isaac_runtime.runtime_quaternion(quat)
    assert isaac_runtime.quaternion_wxyz(native) == quat
    assert json.loads(json.dumps(isaac_runtime.quaternion_wxyz(np.asarray(native, dtype=np.float32)))) == quat
    state = np.arange(26, dtype=float).reshape(2, 13)
    state[:, 3:7] = native
    original = state.copy()
    normalized = isaac_runtime.state_wxyz(state)
    np.testing.assert_array_equal(normalized[:, 3:7], [quat, quat])
    np.testing.assert_array_equal(normalized[:, :3], state[:, :3])
    np.testing.assert_array_equal(normalized[:, 7:], state[:, 7:])
    np.testing.assert_array_equal(state, original)


@pytest.mark.parametrize("legacy_callbacks", [False, True])
def test_simulation_teardown_uses_available_api(legacy_callbacks):
    calls = []
    sim = SimpleNamespace(stop=lambda: calls.append("stop"), clear_instance=lambda: calls.append("clear"))
    if legacy_callbacks:
        sim.clear_all_callbacks = lambda: calls.append("callbacks")
    isaac_runtime.close_simulation(sim)
    assert calls == (["stop", "callbacks", "clear"] if legacy_callbacks else ["stop", "clear"])
