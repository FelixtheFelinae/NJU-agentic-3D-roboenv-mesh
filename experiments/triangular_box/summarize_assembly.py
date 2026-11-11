"""Re-evaluate preserved trajectories without replacing frozen run evidence."""

import argparse
import hashlib
import importlib.util
import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE_NOTES = {
    'round-01/motor_off': 'Rejected: Kit initialization errors; later clean controls are separate runs.',
    'round-01/offset': 'Earlier search-speed control; superseded by round-05 requested-speed protocol.',
    'round-02/tilted': 'Earlier search-speed control; superseded by round-05 requested-speed protocol.',
    'round-02/gap': 'Earlier search-speed control; superseded by round-05 requested-speed protocol.',
    'round-02/low_torque': 'Earlier search-speed control; superseded by round-05 requested-speed protocol.',
    'round-02/zero-free': 'Excluded as bounded bench evidence: old relative-target controller lifts entire assembly about 1.11m. Mechanical hold checks alone pass.',
    'round-03/pressed-full': 'Rejected and superseded: old lift/press controller confounds post-probe divergence.',
    'round-04/free-one': 'Superseded old relative-target controller; residual check also fails.',
    'round-04/motor_off': 'Clean serial startup rerun; older search-speed protocol.',
    'round-06/nominal-halfstep-diagnostic': 'Intentionally incomplete first-10s timestep diagnostic, not completed disassembly.',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same_step_residual(row, screw):
    """Use post-step body/screw quaternions; pre-step theta selects turn count.

    This local unwrap assumes less than pi rotation during the single step.
    It does not use commanded rpm or integrate reported angular velocity.
    """
    bx, by, bz, bw = row['body_pose'][3:]
    sx, sy, sz, sw = screw['pose'][3:]
    w = bw * sw + bx * sx + by * sy + bz * sz
    x = bw * sx - bx * sw - by * sz + bz * sy
    y = bw * sy + bx * sz - by * sw - bz * sx
    z = bw * sz - bx * sy + by * sx - bz * sw
    angle = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    before = screw['theta_rad']
    after = before + math.atan2(math.sin(angle - before), math.cos(angle - before))
    return screw['exit_m'] - after * .0007 / math.tau


def metrics(report):
    rows = report['trace']
    active = [(abs(s['helix_residual_m']), row['t'], i, row['phase'])
              for row in rows if row['t'] >= 2
              for i, s in enumerate(row['screws']) if s['thread_active']]
    early = [item for item in active if item[1] <= 10]
    synchronized = [(abs(same_step_residual(row, s)), row['t'], i, row['phase'])
                    for row in rows if row['t'] >= 2
                    for i, s in enumerate(row['screws']) if s['thread_active']]
    names = report['native_link_names']
    torque = {}
    for name in ('spindle', 'housing'):
        index = names.index(name)
        torque[name] = max(math.sqrt(sum(v * v for v in row[
            'native_joint_wrenches_child_frames'][index][3:])) for row in rows)
    return {
        'completed': report['completed'], 'failure': report['failure'],
        'final_phase': report['final_phase'], 'sim_seconds': report['sim_seconds'],
        'wall_seconds': report['wall_seconds'], 'dt_s': report.get('dt', .001),
        'boundary': report['boundary'], 'rpm': report['rpm'], 'fault': report['fault'],
        'requested_order': report['order'], 'removals': report['removals'],
        'probes': report['probes'], 'worst_active_residual_m_t_screw_phase': max(active, default=None),
        'worst_first_10s_residual_m_t_screw_phase': max(early, default=None),
        'worst_same_step_pose_residual_m_t_screw_phase': max(synchronized, default=None),
        'peak_native_joint_torque_norm_Nm': torque,
        'thread_clear_events': [event for event in report['events'] if event.get('to') == 'thread_clear'],
        'final_screw_positions_m': [s['pose'][:3] for s in rows[-1]['screws']],
        'final_motor': rows[-1]['tool']['motor'],
        'max_screw_rotation_rad': [max(abs(row['screws'][i]['theta_rad']) for row in rows)
                                   for i in range(3)],
        'max_body_world_height_m': max(row['body_pose'][2] for row in rows),
        'pull_target_frame': report.get('pull_target_frame', 'body-relative (early controller)'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    receipts = sorted(root.glob('round-*/*/receipt.json'))
    if not receipts:
        raise ValueError('No run receipts found')
    for receipt_path in receipts:
        receipt = json.loads(receipt_path.read_text())
        if 'finished_unix' not in receipt or not receipt_path.with_name('result.json').exists():
            raise ValueError(f'Run incomplete or missing trajectory: {receipt_path.parent}')
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / 'code'
    snapshot.mkdir()
    for name in ('evaluate_assembly.py', 'summarize_assembly.py', 'plot_assembly.py'):
        shutil.copy2(Path(__file__).with_name(name), snapshot / name)
    spec = importlib.util.spec_from_file_location('frozen_common_evaluator', snapshot / 'evaluate_assembly.py')
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    inventory, results = {}, {}
    for folder in sorted(root.glob('round-*/inputs')):
        manifest = folder / 'provenance.json'
        records = json.loads(manifest.read_text())['files']
        for name, record in records.items():
            if digest(folder / name) != record['sha256']:
                raise ValueError(f'Frozen file changed: {folder / name}')
        inventory[str(manifest.relative_to(root))] = digest(manifest)
    for path in sorted(root.glob('round-*/*/result.json')):
        label = str(path.parent.relative_to(root))
        receipt_path = path.with_name('receipt.json')
        receipt = json.loads(receipt_path.read_text())
        if 'finished_unix' not in receipt:
            raise ValueError(f'Run still active: {label}')
        for name, expected in receipt['files_sha256'].items():
            if digest(path.with_name(name)) != expected:
                raise ValueError(f'Run evidence changed: {path.with_name(name)}')
        report = json.loads(path.read_text())
        evaluation = evaluator.evaluate(report, path.with_suffix('.log').read_text())
        target = output / label
        target.mkdir(parents=True)
        (target / 'supplemental.evaluation.json').write_text(json.dumps(evaluation, indent=2))
        frozen = path.with_suffix('.evaluation.json')
        results[label] = {
            'source_result_sha256': digest(path), 'source_receipt_sha256': digest(receipt_path),
            'frozen_status': json.loads(frozen.read_text())['status'] if frozen.exists() else None,
            'common_status': evaluation['status'],
            'failed_checks': [c['name'] for c in evaluation['checks'] if not c['passed']],
            'metrics': metrics(report),
            'evidence_use': EVIDENCE_NOTES.get(label, 'Main assembly-bench evidence; inspect acceptance and stated scope.'),
        }
    preflight = root / 'preflight/zero.json'
    if preflight.exists():
        log = preflight.with_suffix('.log')
        report = json.loads(preflight.read_text())
        evaluation = evaluator.evaluate(report, log.read_text())
        target = output / 'preflight/zero'
        target.mkdir(parents=True)
        (target / 'supplemental.evaluation.json').write_text(json.dumps(evaluation, indent=2))
        results['preflight/zero'] = {
            'source_result_sha256': digest(preflight), 'source_log_sha256': digest(log),
            'frozen_status': None, 'common_status': evaluation['status'],
            'failed_checks': [c['name'] for c in evaluation['checks'] if not c['passed']],
            'metrics': metrics(report), 'note': 'Early fixed-base baseline; no suite receipt.',
        }
    (output / 'summary.json').write_text(json.dumps({
        'schema': 'envcreator.assembly-common-analysis.v1', 'root': str(root),
        'generated_utc': datetime.now(UTC).isoformat(),
        'code_sha256': {p.name: digest(p) for p in snapshot.iterdir() if p.is_file()},
        'input_manifest_sha256': inventory, 'cases': results,
        'notes': ['Supplemental evaluation; original frozen evaluations remain unchanged.',
                  'Repeated, superseded and incomplete diagnostic runs are not independent assets.',
                  'thread_clear events are pre-step; trajectory samples are post-step every 20ms.',
                  'Same-step diagnostic derives twist from relative body/screw poses; pre-step theta selects the nearest turn (assumes <pi per physics step). Acceptance thresholds remain unchanged.',
                  'Native joint torque norm is a constraint reaction, not measured screw breakaway torque.'],
    }, indent=2))
    print(json.dumps({name: result['common_status'] for name, result in results.items()}, indent=2))


if __name__ == '__main__':
    main()
