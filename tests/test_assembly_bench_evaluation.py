"""Prevent missing/contradictory assembly evidence from becoming acceptance.

Synthetic reports exercise the evaluator only, never count as physics evidence.
"""

import copy
import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'experiments/triangular_box/evaluate_assembly.py'
spec = importlib.util.spec_from_file_location('assembly_evaluation', MODULE)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def baseline_report():
    screws = [dict(thread_active=True, connector_enabled=True, helix_residual_m=0.,
                   theta_rad=0., exit_m=0., pose=[0.] * 7, velocity=[0.] * 6) for _ in range(3)]
    return dict(completed=True, failure=None, final_phase='done', case='sequence',
                runtime_pose_writes=False, forced_thread_release=False, order=[], removals=[],
                boundary='fixed', fault='none', rpm=120,
                trace=[dict(t=3., phase='probe', screws=screws, pull_N=20., press_N=0.,
                            geometry=dict(anchor_displacement_body_m=[[0.] * 3 for _ in range(3)]),
                            tool={'motor': {'enabled': False, 'measured_rpm': 0.}},
                            lid_pose=[0.] * 7, body_pose=[0.] * 7,
                            lid_velocity=[0.] * 6, body_velocity=[0.] * 6)],
                probes=[dict(t=3.5, remaining=[0, 1, 2], pull_cap_N=20.,
                             geometry=dict(anchor_displacement_body_m=[[0.] * 3 for _ in range(3)]))])


def failed_checks(report, log=''):
    return {item['name'] for item in evaluation.evaluate(report, log)['checks'] if not item['passed']}


def test_no_removal_pull_baseline_is_not_complete_disassembly():
    report = baseline_report()
    assert evaluation.evaluate(report, '')['status'] == 'passed'
    report['order'] = [0, 1, 2]
    assert {'tool_removal_order', 'all_requested_lid_probes_executed'} <= failed_checks(report)


def test_lid_cannot_pass_while_the_test_press_holds_it_down():
    report = baseline_report()
    report['trace'][0]['press_N'] = 50.
    assert 'probe_0_finite_pull_and_no_press' in failed_checks(report)


def test_state_label_cannot_replace_thread_travel_or_connector_release():
    report = baseline_report()
    report['trace'][0]['screws'][0]['thread_active'] = False
    assert {'connector_matches_thread_state', 'release_requires_thread_travel'} <= failed_checks(report)


def test_partial_connection_is_checked_at_retained_anchor_not_lid_center():
    report = baseline_report()
    probe = copy.deepcopy(report['probes'][0])
    probe['remaining'] = [2]
    probe['geometry']['anchor_displacement_body_m'] = [[0., 0., .04], [0., 0., .04], [0., 0., .01]]
    report['probes'].append(probe)
    assert 'probe_1_remaining_connections_hold' in failed_checks(report)


def test_engine_rejected_constraint_blocks_a_success_label():
    assert 'no_engine_errors' in failed_checks(
        baseline_report(), '[Error] PhysX rejected joint limit invalid')


def test_probe_endpoint_recovery_cannot_hide_transient_separation():
    report = baseline_report()
    report['trace'][0]['geometry']['anchor_displacement_body_m'][0][2] = .01
    assert 'probe_0_remaining_connections_hold' in failed_checks(report)


def test_missing_probe_trajectory_geometry_is_not_acceptance():
    report = baseline_report()
    del report['trace'][0]['geometry']
    assert 'probe_0_remaining_connections_hold' in failed_checks(report)


def test_interrupted_simulation_and_nonfinite_state_fail():
    report = baseline_report()
    report['completed'] = False
    report['trace'][0]['lid_velocity'][0] = float('nan')
    assert {'executed_to_terminal_phase', 'finite_states'} <= failed_checks(report)


def test_completion_does_not_hide_a_running_motor():
    report = baseline_report()
    report['trace'][0]['tool']['motor'].update(enabled=True, measured_rpm=120.)
    assert 'motor_off_and_below_1rpm_at_finish' in failed_checks(report)


def test_removal_label_cannot_replace_box_exterior_deposition():
    report = baseline_report()
    report.update(order=[0], removals=[{'screw': 0, 't': 3.}])
    row = report['trace'][0]
    row['body_pose'][6] = 1.
    row['screws'][0].update(thread_active=False, connector_enabled=False, exit_m=.024,
                            theta_rad=72., pose=[.30, .23, .005, 0., 0., 0., 1.])
    row['tool']['fasteners'] = {'/World/screw_0': {'magnetic_retention': False}}
    assert 'screw_0_deposited_outside_box' not in failed_checks(report)
    # An extracted screw still above the tool or left inside the enclosure is
    # not the requested carry-and-drop outcome, even with a removal event.
    for position, retained in [([.30, .23, .14], True), ([0., 0., .005], False)]:
        row['screws'][0]['pose'][:3] = position
        row['tool']['fasteners']['/World/screw_0']['magnetic_retention'] = retained
        assert 'screw_0_deposited_outside_box' in failed_checks(report)


def test_pull_actuator_stops_lifting_a_whole_enclosure_at_world_target():
    path = MODULE.with_name('assembly_protocol.py')
    module_spec = importlib.util.spec_from_file_location('assembly_protocol_test', path)
    controls = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(controls)
    # Lid and body both rising must not leave a permanent upward command just
    # because their relative separation is zero. No physical engine used here.
    assert controls.lid_pull_force(.04, 0., 0., 1.) == 0.
    assert controls.lid_pull_force(1., 0., 0., 1.) == 0.
    assert 0 < controls.lid_pull_force(0., 0., 0., .15) <= 10.
