"""Physical meaning of engagement gates, reaction and feasibility admission."""

import math

import numpy as np
import pytest

from roboenv_perception.harness import tooling
from roboenv_perception.io import sha256
from roboenv_perception.tools.screwdriving import EngagementParameters, HexEngagement, rotate

DOWN = [math.sqrt(.5), -math.sqrt(.5), 0, 0]
IDENTITY = [1, 0, 0, 0]


def qmul(a, b):
    aw, *av = a
    bw, *bv = b
    av, bv = np.array(av), np.array(bv)
    return [aw*bw-av@bv, *(aw*bv+bw*av+np.cross(av, bv))]


def contact(model, *, tcp=(0, 0, -.0015), q=DOWN, omega=(0, 0, 0)):
    return model.step(tcp, q, omega, (0, 0, 0), IDENTITY, (0, 0, 0))


def test_engaged_rotation_transmits_reciprocal_torque_and_release_clears_it():
    model = HexEngagement()
    assert contact(model)["engaged"]
    rotated = qmul([math.cos(.01), 0, 0, math.sin(.01)], DOWN)
    result = contact(model, q=rotated)
    assert result["transmitted_Nm"] > 0
    assert np.allclose(np.array(result["torque_to_screw_world_Nm"]) + result["torque_to_tool_world_Nm"], 0)
    released = contact(model, tcp=(0, 0, .006), q=rotated, omega=(0, 0, 30))
    assert not released["engaged"] and released["transmitted_Nm"] == 0


@pytest.mark.parametrize("tcp,q,params,reason", [
    ((.004, 0, -.0015), DOWN, EngagementParameters(), "radial_misalignment"),
    ((0, 0, .006), DOWN, EngagementParameters(), "not_inserted_or_bottomed_out"),
    ((0, 0, -.004), DOWN, EngagementParameters(), "not_inserted_or_bottomed_out"),
    ((0, 0, -.0015), IDENTITY, EngagementParameters(), "axis_misalignment"),
    ((0, 0, -.0015), DOWN, EngagementParameters(socket_af_m=.004), "bit_socket_size_mismatch"),
])
def test_motor_speed_cannot_bypass_geometric_engagement(tcp, q, params, reason):
    result = contact(HexEngagement(params), tcp=tcp, q=q, omega=(0, 0, 100))
    assert reason in result["reasons"]
    assert not result["engaged"] and result["transmitted_Nm"] == 0


def test_damping_dissipates_relative_motion_and_rotation_does_not_mutate_observation():
    q = np.array(DOWN)
    copy = q.copy()
    rotate(q, (0, 1, 0))
    assert np.array_equal(q, copy)
    result = contact(HexEngagement(), omega=(0, 0, 2))
    assert result["transmitted_Nm"] * -2 < 0


def test_feasibility_rejects_missing_controls_false_checks_and_changed_asset(tmp_path):
    asset = tmp_path / "tool.usda"
    asset.write_text("fixture")
    checks = dict.fromkeys(tooling.REQUIRED_CHECKS, True)
    for case in tooling.REQUIRED_CASES:
        checks.update({case+"_finite": True, case+"_reciprocal_torque": True})
        if case != "aligned":
            checks[case+"_no_false_loosening"] = True
        if case not in {"aligned", "motor_off"}:
            checks[case+"_no_virtual_torque"] = True
    report = {"status": "passed", "checks": checks, "runtime_pose_writes": False,
              "cases": dict.fromkeys(tooling.REQUIRED_CASES, {}),
              "source_hashes": {str(asset.resolve()): sha256(asset)}}
    assert tooling.audit_interaction(report, asset)
    report["cases"].pop("offset")
    assert not tooling.audit_interaction(report, asset)
    report["cases"]["offset"] = {}
    checks["tilt_challenge_exercised"] = False
    assert not tooling.audit_interaction(report, asset)
    checks["tilt_challenge_exercised"] = True
    asset.write_text("changed fixture")
    assert not tooling.audit_interaction(report, asset)


@pytest.mark.parametrize("status,exit_code", [("ideal_tool_verified", 0), ("unverified", 2)])
def test_tool_test_cli_reports_failed_admission_to_automation(monkeypatch, capsys, status, exit_code):
    from roboenv_perception.integration.cli import main

    monkeypatch.setattr(tooling, "test_tool", lambda *args: {"status": status})
    assert main(["tool-test", "--asset", "tool.usda", "--output", "results", "--isaac-python", "python"]) == exit_code
    assert status in capsys.readouterr().out


def test_axial_contact_is_repulsive_and_distinguishes_head_from_socket_bottom():
    from roboenv_perception.tools.screwdriving import axial_contact
    inside=contact(HexEngagement(),tcp=(0,0,-.0015))
    assert axial_contact(inside,0,.001,.012)['normal_force_N']==0
    bottom={**inside,'insertion_m':.0028}
    assert axial_contact(bottom,0,.001,.012)['normal_force_N']>0
    mismatch={**inside,'reasons':['hex_phase_misalignment'],'insertion_m':.0001}
    response=axial_contact(mismatch,0,.001,.012)
    assert response['surface']=='head_face' and response['normal_force_N']>0
    above={**mismatch,'insertion_m':-.001}
    assert axial_contact(above,-.01,.001,.012)['normal_force_N']==0
    away={**bottom,'radial_error_m':.02}
    assert axial_contact(away,0,.001,.012)['normal_force_N']==0


def test_inserted_socket_wall_reacts_to_lateral_load_but_does_not_capture_offset_bit():
    from roboenv_perception.tools.screwdriving import socket_side_contact
    model=HexEngagement()
    assert contact(model)['engaged']
    displaced=contact(model,tcp=(.0001,0,-.0015))
    assert displaced['engaged']
    f=socket_side_contact((.0001,0,-.0015),(0,0,0),np.array([0,0,1]),(0,0,0),displaced,.001,.012)
    assert f[0]>0 and np.allclose(f[1:],0)
    assert not contact(HexEngagement(),tcp=(.0001,0,-.0015))['engaged']
    withdrawn=contact(model,tcp=(.0001,0,.005))
    assert not withdrawn['engaged']
    assert np.allclose(socket_side_contact((.0001,0,.005),(0,0,0),np.array([0,0,1]),(0,0,0),withdrawn,.001,.012),0)


def test_socket_stations_resist_tilt_only_when_inserted_and_outside_clearance():
    from roboenv_perception.tools.screwdriving import socket_contact_wrench
    def response(angle, engaged=True):
        return socket_contact_wrench(np.array([0.,0.,-.0025]),np.zeros(3),
            np.array([math.sin(angle),0.,math.cos(angle)]),np.array([0.,0.,1.]),
            np.zeros(3),np.zeros(3),np.zeros(3),np.zeros(3),
            {'engaged':engaged,'insertion_m':.0025},.001,.012)
    assert np.allclose(response(.005),0)
    force, moment = response(.05)
    # Mouth load alone at a zero moment arm cannot restore tilt: the inserted
    # tip at the opposite offset supplies the restoring couple in general.
    assert force[0]>0
    assert np.allclose(response(.05,False),0)
    f,m=socket_contact_wrench(np.array([-.00007,0.,-.0025]),np.zeros(3),
        np.array([math.sin(.05),0.,math.cos(.05)]),np.array([0.,0.,1.]),
        np.zeros(3),np.zeros(3),np.zeros(3),np.zeros(3),
        {'engaged':True,'insertion_m':.0025},.001,.012)
    assert m[1]>0  # Tilts screw toward the bit, with an opposite tool moment.
    screw_com=np.zeros(3);tool_com=np.array([0.,0.,.1])
    tool_torque=-m+np.cross(screw_com-tool_com,-f)
    assert np.allclose(m+tool_torque+np.cross(tool_com,-f),0)


@pytest.mark.parametrize('initial_speed',[-120.,120.])
def test_passive_spindle_drag_dissipates_energy_without_reversing(initial_speed):
    from roboenv_perception.tools.screwdriving import passive_spindle_torque
    speed=initial_speed*math.tau/60
    inertia=3.6e-7
    for _ in range(2000):
        before=speed
        torque=passive_spindle_torque(speed,.001,inertia,5e-5,.01)
        speed+=torque*.001/inertia
        assert speed*before>=0 and abs(speed)<=abs(before)+1e-14
        assert torque*before<=0
    assert abs(speed)*60/math.tau<.001
