"""Contract regressions for continuous powered tools and their contact geometry."""

import math
import os
import subprocess
from pathlib import Path

import pytest

from roboenv_perception.harness.geometry import _model_source
from roboenv_perception.tools.electric_screwdriver import REFS, specification


def test_continuous_export_has_effort_speed_without_angle_stops():
    blueprint, recipe, spec = specification()
    recipe.check_against(blueprint, REFS)
    source = _model_source(blueprint, recipe)
    assert "MotionLimits(effort=0.2, velocity=" in source
    assert "lower=" not in source and "upper=" not in source
    assert math.isclose(blueprint.joints[0].velocity_limit.value,
                        spec["motor"]["max_command_rpm"] * math.pi / 30)


def test_continuous_limits_cannot_be_silently_invented():
    blueprint, recipe, _ = specification()
    blueprint.joints[0].effort_limit.value = None
    with pytest.raises(ValueError, match="explicit effort_limit"):
        _model_source(blueprint, recipe)


def test_nose_bore_does_not_obstruct_shaft_and_tcp_matches_bit():
    _, recipe, semantics = specification()
    solids = {s.name: s for s in recipe.solids}
    shaft = solids["rotor_shaft"]
    for name in ("clutch_collar", "nose_ring"):
        assert solids[name].shape == "annulus"
        assert solids[name].inner_radius_m - shaft.radius_m > .001
    assert shaft.center_m[1] - shaft.length_m / 2 + .080 > .065
    assert solids["hex_working_tip"].profile_m[-1][0] == semantics["frames"]["tcp"]["position_m"][1]
    assert math.isclose(2 * solids["hex_shank"].radius_m * math.cos(math.pi / 6), .00635)


def test_usd_motor_units_direction_coast_brake_and_invalid_command():
    python = os.environ.get("ENVCREATOR_USD_PYTHON")
    if not python:
        pytest.skip("Set ENVCREATOR_USD_PYTHON for real OpenUSD motor contract test")
    adapter = Path(__file__).parents[1] / "src/roboenv_perception/tools/power_tool_usd.py"
    script = '''
import importlib.util, json, sys
from pxr import Sdf, Usd, UsdGeom, UsdPhysics
module_spec = importlib.util.spec_from_file_location("motor", sys.argv[1])
motor = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(motor)
stage = Usd.Stage.CreateInMemory()
root = UsdGeom.Xform.Define(stage, "/Tool").GetPrim()
joint = UsdPhysics.RevoluteJoint.Define(stage, "/Tool/spin")
drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular")
for attr in (drive.CreateTargetVelocityAttr(), drive.CreateMaxForceAttr(), drive.CreateDampingAttr()):
    attr.Set(0)
spec = {"motor": {"max_command_rpm": 180, "torque_limit_Nm": .2, "usd_velocity_damping": .05},
        "usd_paths": {"joint": "/spin"}}
root.CreateAttribute("envcreator:toolSpec", Sdf.ValueTypeNames.String).Set(json.dumps(spec))
motor.set_motor(stage, "/Tool", 30)
assert drive.GetTargetVelocityAttr().Get() == 180
motor.set_motor(stage, "/Tool", -30)
assert drive.GetTargetVelocityAttr().Get() == -180
motor.set_motor(stage, "/Tool", 0)
assert drive.GetMaxForceAttr().Get() > 0  # Brake remains powered.
motor.set_motor(stage, "/Tool", enabled=False)
assert drive.GetMaxForceAttr().Get() == 0 and drive.GetDampingAttr().Get() == 0
before = stage.GetRootLayer().ExportToString()
for speed in (float("nan"), float("inf"), 181, -181, True):
    try: motor.set_motor(stage, "/Tool", speed)
    except ValueError: pass
    else: raise AssertionError("invalid command accepted")
assert stage.GetRootLayer().ExportToString() == before
'''
    result = subprocess.run([python, "-c", script, str(adapter)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
