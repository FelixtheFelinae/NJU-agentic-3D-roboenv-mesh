"""Independent, versioned checks for the deterministic assembly bench.

This evaluator neither commands the simulator nor repairs assets. Thresholds
are bench tolerances, not hardware accuracy claims. Incomplete runs fail.
"""

import json
import math
from pathlib import Path


def evaluate(report, log):
    checks = []

    def check(name, passed, **details):
        checks.append({'name': name, 'passed': bool(passed), **details})

    rows = report.get('trace', [])
    probes = report.get('probes', [])
    check('executed_to_terminal_phase', report.get('completed') and not report.get('failure'),
          final_phase=report.get('final_phase'), failure=report.get('failure'),
          blocked=report.get('blocked'))
    errors = [line for line in log.splitlines()
              if any(word in line for word in ('[Error]', 'limit invalid',
                                               'Recursion not allowed'))]
    check('no_engine_errors', not errors, errors=errors[:20])
    check('physical_execution_only', report.get('runtime_pose_writes') is False
          and report.get('forced_thread_release') is False)
    numeric = [value for row in rows for key in ('lid_pose', 'body_pose', 'lid_velocity', 'body_velocity')
               for value in row.get(key, [])]
    numeric += [value for row in rows for screw in row['screws']
                for key in ('pose', 'velocity') for value in screw[key]]
    check('finite_states', bool(numeric) and all(math.isfinite(v) for v in numeric))
    motor = rows[-1].get('tool', {}).get('motor', {}) if rows else {}
    check('motor_off_and_below_1rpm_at_finish', motor.get('enabled') is False
          and abs(motor.get('measured_rpm', float('inf'))) < 1.,
          measured_rpm=motor.get('measured_rpm'), tolerance_rpm=1.)
    check('connector_matches_thread_state', bool(rows) and all(
        screw['thread_active'] == screw['connector_enabled']
        for row in rows for screw in row['screws']))
    releases = []
    for i in range(3):
        first = next((row['screws'][i] for row in rows
                      if not row['screws'][i]['thread_active']), None)
        if first:
            releases.append({'screw': i, 'exit_m': first['exit_m'],
                             'rotation_rad': first['theta_rad']})
    check('release_requires_thread_travel', all(
        r['exit_m'] >= .0079 and r['rotation_rad'] * .0007 / math.tau >= .0079
        for r in releases), releases=releases,
          scope='v9 M4x0.7, 8mm effective thread; tolerance 0.1mm')
    # This conservative check uses the first 20ms trajectory sample AFTER
    # release. It can fail on post-release rebound; inspect the preserved
    # per-physics-step thread_clear events before diagnosing premature release.
    operated = set()
    premature = []
    for row in rows:
        if row.get('selected') is not None:
            operated.add(row['selected'])
        for i, screw in enumerate(row['screws']):
            if i not in operated and (not screw['thread_active'] or abs(screw['theta_rad']) >= .02):
                premature.append({'t': row['t'], 'screw': i, 'theta_rad': screw['theta_rad']})
    check('not_yet_operated_connections_hold', not premature, violations=premature[:10])
    active = [abs(screw['helix_residual_m']) for row in rows if row['t'] >= 2
              for screw in row['screws'] if screw['thread_active']]
    check('active_helix_residual', bool(active) and max(active) < .00005,
          max_residual_m=max(active, default=None), tolerance_m=.00005)
    for number, probe in enumerate(probes):
        sampled = [row for row in rows if row['phase'] == 'probe' and
                   probe['t'] - 1.5 <= row['t'] <= probe['t']]
        check(f'probe_{number}_finite_pull_and_no_press', bool(sampled)
              and max(r['pull_N'] for r in sampled) > 1.
              and all(0 <= r['pull_N'] <= 20. and r['press_N'] == 0. for r in sampled))
        retained = probe['remaining']
        if retained:
            measured = [probe['geometry'], *(r.get('geometry') for r in sampled)]
            complete_geometry = all(g and 'anchor_displacement_body_m' in g for g in measured)
            displacement = max((abs(g['anchor_displacement_body_m'][i][2])
                                for g in measured if g for i in retained), default=float('inf'))
            check(f'probe_{number}_remaining_connections_hold', complete_geometry and displacement < .002,
                  # End-point recovery alone cannot hide transient separation.
                  continuous_samples_present=complete_geometry,
                  remaining=retained, max_anchor_vertical_displacement_m=displacement,
                  tolerance_m=.002, pull_cap_N=probe['pull_cap_N'])
        else:
            clearance = min(p[2] for p in probe['geometry']['anchor_displacement_body_m'])
            check(f'probe_{number}_released_lid_separates', clearance > .02,
                  minimum_anchor_lift_m=clearance, required_m=.02)
    check('baseline_pull_executed', bool(probes) and probes[0]['remaining'] == [0, 1, 2])
    if report['case'] == 'sequence':
        order = report['order']
        check('tool_removal_order', [row['screw'] for row in report['removals']] == order,
              requested=order, actual=[row['screw'] for row in report['removals']])
        check('all_requested_lid_probes_executed', len(probes) == len(order) + 1,
              requested=len(order) + 1, actual=len(probes))
        for count, probe in enumerate(probes):
            expected = sorted(set(range(3)) - set(order[:count]))
            check(f'probe_{count}_correct_topology', probe['remaining'] == expected,
                  expected=expected, actual=probe['remaining'])
        for removal in report['removals']:
            i = removal['screw']
            at_removal = min(rows, key=lambda row: abs(row['t'] - removal['t']))['screws'][i]
            check(f'screw_{i}_physically_extracted', not at_removal['thread_active']
                  and at_removal['exit_m'] >= .0235,
                  measured_exit_m=at_removal['exit_m'], required_m=.024,
                  sampling_tolerance_m=.0005)
            final = rows[-1]
            screw = final['screws'][i]
            # The triangular enclosure fits inside a 0.174m horizontal radius.
            # A 0.20m body-relative distance puts the deposited screw outside it.
            dx, dy, dz = (a - b for a, b in zip(screw['pose'][:3], final['body_pose'][:3]))
            qx, qy, qz, qw = final['body_pose'][3:]
            local_x = ((1 - 2 * (qy*qy + qz*qz)) * dx
                       + 2 * (qx*qy + qw*qz) * dy + 2 * (qx*qz - qw*qy) * dz)
            local_y = (2 * (qx*qy - qw*qz) * dx
                       + (1 - 2 * (qx*qx + qz*qz)) * dy + 2 * (qy*qz + qw*qx) * dz)
            fastener = final.get('tool', {}).get('fasteners', {}).get(f'/World/screw_{i}', {})
            distance = math.hypot(local_x, local_y)
            check(f'screw_{i}_deposited_outside_box', distance > .20
                  and screw['pose'][2] < .04 and fastener.get('magnetic_retention') is False,
                  body_relative_horizontal_distance_m=distance,
                  world_height_m=screw['pose'][2], magnetic_retention=fastener.get('magnetic_retention'),
                  scope='v9 enclosure radius <0.174m; world tabletop z=0; deposition zone below 40mm')
        untouched = set(range(3)) - set(order)
    else:
        untouched = set(range(3))
        after = [row for row in rows if row['phase'] in {'engage', 'spin', 'negative_finish'}]
        check('negative_preserves_connections', bool(after) and all(
            screw['thread_active'] for row in after for screw in row['screws']))
        fault = report['fault']
        reason = {'offset': 'radial_misalignment', 'tilted': 'axis_misalignment',
                  'gap': 'not_inserted_or_bottomed_out'}.get(fault)
        if reason:
            challenged = [row for row in after if reason in row['tool'].get('fasteners', {}).get(
                f"/World/screw_{report['order'][0]}", {}).get('engagement', {}).get('reasons', [])]
            check('negative_condition_exercised', len(challenged) >= 20,
                  reason=reason, sampled_frames=len(challenged))
            check('no_virtual_engagement', bool(after) and not any(
                row['tool'].get('fasteners', {}).get(f"/World/screw_{report['order'][0]}", {}).get(
                    'engagement', {}).get('engaged', False) for row in after))
        else:
            check('negative_engaged_control_exercised', any(
                row['tool'].get('engagement', {}).get('engaged') for row in after))
            if fault == 'motor_off':
                check('motor_disabled', bool(after) and all(
                    not row['tool'].get('motor', {}).get('enabled', True) for row in after))
            elif fault == 'low_torque':
                check('bounded_low_torque_command', any(
                    row['tool'].get('motor', {}).get('enabled') and
                    row['tool']['motor']['torque_limit_Nm'] == .3 for row in after))
    check('untouched_screws_do_not_loosen', all(
        screw['thread_active'] and abs(screw['theta_rad']) < .02
        for row in rows for i, screw in enumerate(row['screws']) if i in untouched),
          screws=sorted(untouched))
    return {'schema': 'envcreator.assembly-evaluation.v1',
            'status': 'passed' if checks and all(c['passed'] for c in checks) else 'failed',
            'checks': checks, 'scope': report.get('scope'), 'boundary': report['boundary'],
            'fault': report['fault'], 'order': report['order'], 'rpm': report['rpm']}


def evaluate_file(path):
    path = Path(path)
    result = evaluate(json.loads(path.read_text()), path.with_suffix('.log').read_text())
    path.with_suffix('.evaluation.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    result = evaluate_file(args.report)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 2)
