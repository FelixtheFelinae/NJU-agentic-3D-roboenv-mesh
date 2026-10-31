"""Regression checks for the experimental screw interaction contract."""

import math

import pytest

from experiments.cart_assembly.mechanics import Parameters, ScrewModel, tool_access


def observe_turns(model, angle, dz):
    for i in range(1, 101):
        a = angle * i / 100
        model.observe([math.cos(a / 2), 0, 0, math.sin(a / 2)], dz * i / 100)


def test_wrapped_angles_count_relative_turns_and_reverse_strokes():
    model = ScrewModel(Parameters())
    observe_turns(model, 6 * math.pi, 0.0024)
    assert model.theta == pytest.approx(6 * math.pi)
    for a in [6 * math.pi - j * 0.1 for j in range(1, 21)]:
        model.observe([math.cos(a / 2), 0, 0, math.sin(a / 2)], 0.0024)
    assert model.theta == pytest.approx(6 * math.pi - 2)
    assert model.thread_active


def test_axial_displacement_alone_does_not_release_thread():
    model = ScrewModel(Parameters())
    model.observe([1, 0, 0, 0], 0.01)
    assert model.thread_active


def test_turning_tool_does_not_directly_accumulate_screw_progress():
    model = ScrewModel(Parameters())
    for _ in range(100):
        f, t, m = model.forces([0, 0, 0], [0, 0, 0], [0, 0, 0], 0.004, 0, engaged=False)
        assert t == [0, 0, 0]
    assert model.theta == 0


def test_coupling_reaction_is_power_conjugate():
    p = Parameters()
    model = ScrewModel(p)
    _, _, m = model.forces([0, 0, 0.0001], [0, 0, 0], [0, 0, 0], 0, 0)
    h = p.lead_m / (2 * math.pi)
    assert m["thread_reaction_torque_Nm"] == pytest.approx(-h * m["coupling_force_N"])


def test_release_removes_all_artificial_translation_guides():
    model = ScrewModel(Parameters())
    observe_turns(model, 12 * math.pi, 0.0048)
    assert not model.thread_active
    force, torque, _ = model.forces(
        [0.02, 0.01, 0.02], [1, 1, 1], [0, 0, 1], 0, 0.08, transverse_pull=0.01
    )
    assert force == [0.01, 0, 0.08]
    assert torque == [0, 0, 0]


def test_full_tool_envelope_hits_obstacle_even_if_centerline_clear():
    result = tool_access(
        0.003,
        0.02,
        0.06,
        [{"id": "edge", "min": [0.002, -0.001, 0.02], "max": [0.004, 0.001, 0.03]}],
    )
    assert not result["accessible"]
    assert result["obstacle_ids"] == ["edge"]


def test_invalid_parameters_rejected():
    with pytest.raises(ValueError):
        Parameters(lead_m=float("nan"))
    with pytest.raises(ValueError):
        Parameters(engagement_m=0.1)
