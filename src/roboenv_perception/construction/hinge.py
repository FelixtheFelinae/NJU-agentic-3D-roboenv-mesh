"""Hinge interaction protocol and evidence evaluation, independent of simulator code."""

import math

import numpy as np


def hinge_cases(suite='primary'):
    if suite == 'followup':
        return [dict(name=f'hold_{a}', kind='hold', initial_deg=a, torque_Nm=0,
                     force_start=.6, force_end=1.6, duration=2.3) for a in [30, 35]]+[
            dict(name='close_short_release', kind='move', initial_deg=110, torque_Nm=-.9,
                 force_start=.6, force_end=.68, duration=2.3, require_interior_release=True)]
    cases = [dict(name=f'hold_{angle:g}', kind='hold', initial_deg=angle,
                  torque_Nm=0, force_start=.6, force_end=1.6, duration=2.3)
             for angle in [15, 45, 90, 128.56557]]
    for name, kind, angle, torque, end, duration in [
        ('small_open', 'small', 90, .15, 1.6, 2.3),
        ('small_close', 'small', 90, -.15, 1.6, 2.3),
        ('open_release', 'move', 80, .9, 1.0, 2.3),
        ('close_release', 'move', 110, -.9, 1.0, 2.3),
        ('upper_stop', 'upper', 110, 1.2, 2.2, 2.8),
        ('lower_stop', 'lower', 30, -1.2, 2.2, 2.8),
    ]:
        cases.append(dict(name=name, kind=kind, initial_deg=angle, torque_Nm=torque,
                          force_start=.6, force_end=end, duration=duration))
    return cases


def evaluate_hinge_case(case, rows, lower=0, upper=2.4):
    time = np.array([r['t'] for r in rows])
    q = np.array([r['q_rad'] for r in rows])
    velocity = np.array([r['qd_rad_s'] for r in rows])
    roots = np.array([r['root_state'] for r in rows])

    def at(t):
        return q[np.argmin(abs(time-t))]

    before = at(case['force_start'])
    after = at(case['force_end'])
    active = (time >= case['force_start']) & (time <= case['force_end'])
    released = time >= case['force_end']+.3
    drift = float(np.ptp(q[time >= .5]))
    motion = float(after-before)
    # Solver position corrections may change q while the DOF velocity reports ~0.
    # Audit actual angle differences as well, so velocity-buffer artifacts cannot pass QA.
    angle_velocity = np.diff(q)/np.diff(time)
    release_speed = max(float(np.max(abs(velocity[released]))),
                        float(np.max(abs(angle_velocity[released[1:]]))))
    root_drift = float(np.max(np.linalg.norm(roots[time >= .5, :2]-roots[time >= .5, :2][0], axis=1)))
    checks = {
        'finite_state': bool(np.isfinite(q).all() and np.isfinite(velocity).all() and np.isfinite(roots).all()),
        'initialized_angle_within_2deg': abs(at(.5)-math.radians(case['initial_deg'])) < math.radians(2),
        'limits_within_2deg': bool(q.min() >= lower-math.radians(2) and q.max() <= upper+math.radians(2)),
        'base_stays_supported': bool(roots[:, 2].min() > .80 and root_drift < .02),
    }
    if case['kind'] == 'hold':
        checks['gravity_hold_drift_below_2deg'] = drift < math.radians(2)
    elif case['kind'] == 'small':
        checks['small_torque_motion_below_2deg'] = float(np.max(abs(q[active]-before))) < math.radians(2)
    else:
        checks['moves_in_applied_direction_over_10deg'] = math.copysign(1, case['torque_Nm'])*motion > math.radians(10)
        checks['stops_after_release_below_1deg_s'] = release_speed < math.radians(1)
    if case['kind'] in {'upper', 'lower'}:
        stop = upper if case['kind'] == 'upper' else lower
        checks['reaches_requested_stop_within_2deg'] = abs(after-stop) < math.radians(2)
    if case.get('require_interior_release'):
        checks['release_and_rest_away_from_both_stops'] = (
            lower+math.radians(5) < after < upper-math.radians(5)
            and lower+math.radians(5) < q[-1] < upper-math.radians(5))
    checks = {name: bool(value) for name, value in checks.items()}
    return {'name': case['name'], 'status': 'passed' if all(checks.values()) else 'failed',
            'checks': checks, 'initial_target_deg': case['initial_deg'],
            'before_force_deg': math.degrees(before), 'after_force_deg': math.degrees(after),
            'final_deg': math.degrees(float(q[-1])), 'motion_deg': math.degrees(motion),
            'hold_drift_deg': math.degrees(drift), 'release_max_speed_deg_s': math.degrees(release_speed),
            'observed_range_deg': [math.degrees(float(q.min())), math.degrees(float(q.max()))],
            'final_at_hard_stop': bool(min(abs(q[-1]-lower), abs(q[-1]-upper)) < math.radians(2)),
            'base_horizontal_drift_mm': root_drift*1000, 'applied_torque_Nm': case['torque_Nm']}
