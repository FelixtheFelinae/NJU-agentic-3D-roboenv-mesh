import math

import numpy as np

from roboenv_perception.construction.hinge import evaluate_hinge_case, hinge_cases


def samples(q, speed=0):
    return [{'t': float(t), 'q_rad': float(q(t) if callable(q) else q),
             'qd_rad_s': speed, 'root_state': [.56, -.04, .85, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]}
            for t in np.arange(1/240, 2.3+1e-8, 1/240)]


def test_stationary_hinge_cannot_pass_an_interaction_test():
    case = next(c for c in hinge_cases() if c['name'] == 'open_release')
    result = evaluate_hinge_case(case, samples(math.radians(80)))
    assert result['status'] == 'failed'
    assert not result['checks']['moves_in_applied_direction_over_10deg']


def test_hinge_that_keeps_moving_does_not_pass_release_test():
    case = next(c for c in hinge_cases() if c['name'] == 'open_release')
    q = lambda t: math.radians(80+min(30, max(0, (t-.6)*75)))  # noqa: E731
    result = evaluate_hinge_case(case, samples(q, speed=math.radians(3)))
    assert result['checks']['moves_in_applied_direction_over_10deg']
    assert not result['checks']['stops_after_release_below_1deg_s']


def test_hold_and_limit_errors_are_detected():
    case = hinge_cases()[0]
    assert evaluate_hinge_case(case, samples(math.radians(15)))['status'] == 'passed'
    drifting = evaluate_hinge_case(case, samples(lambda t: math.radians(15+3*t)))
    assert not drifting['checks']['gravity_hold_drift_below_2deg']
    outside = evaluate_hinge_case(case, samples(2.5))
    assert not outside['checks']['limits_within_2deg']


def test_support_loss_is_not_hidden_by_good_joint_angle():
    rows = samples(math.radians(15))
    rows[-1]['root_state'][2] = .70
    result = evaluate_hinge_case(hinge_cases()[0], rows)
    assert not result['checks']['base_stays_supported']


def test_angle_drift_cannot_hide_behind_zero_reported_velocity():
    case = next(c for c in hinge_cases() if c['name'] == 'open_release')
    result = evaluate_hinge_case(case, samples(lambda t: math.radians(80+12*t), speed=0))
    assert not result['checks']['stops_after_release_below_1deg_s']


def test_short_release_must_stop_away_from_hard_stops():
    case = hinge_cases('followup')[-1]
    result = evaluate_hinge_case(case, samples(lambda t: math.radians(110 if t < .6 else 0)))
    assert not result['checks']['release_and_rest_away_from_both_stops']
