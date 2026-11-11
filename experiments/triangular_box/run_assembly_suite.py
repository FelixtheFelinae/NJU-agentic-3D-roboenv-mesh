"""Freeze inputs and run bounded, isolated Isaac assembly tests in Docker."""

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

IMAGE = 'sha256:d08db932be1f34d1b69162aef98845ed53197ed535fbc42b59d2e43a59bf24c6'
CASES = {
    'zero-fixed': dict(count=0),
    'zero-free': dict(count=0, boundary='free'),
    'one-fast': dict(count=1),
    'one-nominal': dict(count=1, rpm=30),
    'full-012': dict(count=3),
    'full-210': dict(count=3, order='2,1,0'),
    'pressed-full': dict(count=3, boundary='pressed'),
    'free-one': dict(count=1, boundary='free'),
    'nominal-halfstep-diagnostic': dict(count=1, rpm=30, dt=.0005, seconds=10),
    **{name: dict(count=1, fault=name) for name in
       ('offset', 'tilted', 'gap', 'motor_off', 'low_torque')},
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(root, repo):
    inputs = root / 'inputs'
    if inputs.exists():
        records = json.loads((inputs / 'provenance.json').read_text())['files']
        for name, record in records.items():
            if digest(inputs / name) != record['sha256']:
                raise ValueError('Frozen input changed: ' + name)
        return inputs
    inputs.mkdir(parents=True)
    files = {
        'box.usda': repo / 'outputs/triangular-box-20261030-v9/delivery/triangular_box.usda',
        'tool.usda': repo / 'outputs/triangular-box-20261030-v9/robot-bundle-final/scene/deps/0001-electric_screwdriver.usda',
    }
    for name in ('sources.json', 'design.json', 'mass-properties.json', 'manifest.json'):
        files['references/box-' + name] = repo / 'outputs/triangular-box-20261030-v9/delivery' / name
    for name in ('native_screw_runtime.py', 'solver_thread.py', 'fastener.py', 'screwdriving.py'):
        files['tools/' + name] = repo / 'src/roboenv_perception/tools' / name
    for name in ('validate_tool.py', 'assembly_protocol.py', 'evaluate_assembly.py', 'run_assembly_suite.py'):
        files['bench/' + name] = repo / 'experiments/triangular_box' / name
    records = {}
    for name, source in files.items():
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        records[name] = {'source': str(source), 'sha256': digest(target)}
    (inputs / 'provenance.json').write_text(json.dumps({
        'schema': 'envcreator.assembly-inputs.v1', 'image': IMAGE, 'files': records,
        'observation_permissions': 'privileged geometry and mechanism state; no model/robot',
        'protocol': {'dt_s': .001, 'standard_rpm': 30, 'accelerated_rpm': 120,
                     'lid_pull_cap_N': 20, 'lid_target_lift_m': .04,
                     'partial_anchor_tolerance_m': .002, 'full_lift_required_m': .02,
                     'active_helix_residual_tolerance_m': .00005,
                     'negative_duration_per_phase_s': 6, 'cases': CASES},
        'assumptions': ['unchanged v9 reduced preload/mimic thread',
                        'ideal XYZ carriage with finite joint drive force',
                        'geometry-gated tool contact and capped 3N magnetic retention',
                        'fixed base is an explicit fixture; pressed uses external 50N lid load',
                        '20N compliant pull is a test actuator, not robot grasp',
                        'internal clamp preload is not an external table normal load'],
    }, indent=2))
    return inputs


def run_case(root, inputs, name):
    cfg = CASES[name]
    folder = root / name
    folder.mkdir(exist_ok=False)
    container = 'envcreator-assembly-' + name.replace('_', '-') + '-' + str(time.time_ns())[-9:]
    output = folder / 'result.json'
    argv = ['docker', 'run', '--rm', '--name', container, '--gpus', 'all', '--network', 'none',
            '-e', 'ACCEPT_EULA=Y', '-e', 'PRIVACY_CONSENT=Y', '-e', 'OMNI_KIT_ALLOW_ROOT=1',
            '-e', 'OMP_NUM_THREADS=1', '-e', 'OPENBLAS_NUM_THREADS=1', '-e', 'MKL_NUM_THREADS=1',
            '-v', f'{inputs}:/inputs:ro',
            '-v', f'{inputs / "tools"}:/repo/src/roboenv_perception/tools:ro',
            '-v', f'{folder}:/results:rw', IMAGE, '/inputs/bench/validate_tool.py',
            '--asset', '/inputs/box.usda', '--tool', '/inputs/tool.usda',
            '--output', '/results/result.json', '--assembly-case', 'negative' if cfg.get('fault') else 'sequence',
            '--boundary', cfg.get('boundary', 'fixed'), '--remove-count', str(cfg['count']),
            '--order', cfg.get('order', '0,1,2'), '--rpm', str(cfg.get('rpm', 120)),
            '--fault', cfg.get('fault', 'none'), '--seconds', str(cfg.get('seconds', 220)),
            '--dt', str(cfg.get('dt', .001)), '--stage-timeout', '38']
    receipt = {'argv': argv, 'container': container, 'started_unix': time.time(),
               'input_manifest_sha256': digest(inputs / 'provenance.json')}
    (folder / 'receipt.json').write_text(json.dumps(receipt, indent=2))
    try:
        with output.with_suffix('.log').open('w') as log:
            proc = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, timeout=7200)
        receipt['returncode'] = proc.returncode
        if proc.returncode == 0 and output.is_file():
            # Load the frozen evaluator, not a possibly edited working copy.
            evaluation = subprocess.run(
                ['python', str(inputs / 'bench/evaluate_assembly.py'), str(output)],
                capture_output=True, text=True)
            receipt['evaluation_returncode'] = evaluation.returncode
            receipt['status'] = json.loads(output.with_suffix('.evaluation.json').read_text())['status']
        else:
            receipt['status'] = 'execution_failed'
    except BaseException as exc:
        receipt.update(status='execution_failed', error=f'{type(exc).__name__}: {exc}')
        subprocess.run(['docker', 'stop', '--time', '10', container], capture_output=True, timeout=30)
    finally:
        receipt['finished_unix'] = time.time()
        receipt['files_sha256'] = {p.name: digest(p) for p in folder.iterdir()
                                   if p.is_file() and p.name != 'receipt.json'}
        (folder / 'receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps({'case': name, 'status': receipt['status'],
                      'wall_seconds': receipt['finished_unix'] - receipt['started_unix']}), flush=True)
    return receipt['status']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=CASES, required=True)
    parser.add_argument('--jobs', type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    inputs = freeze(root, repo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_case, root, inputs, name): name for name in args.cases}
        statuses = {futures[future]: future.result() for future in concurrent.futures.as_completed(futures)}
    # Verify all frozen files after execution as well.
    freeze(root, repo)
    print(json.dumps(statuses, indent=2), flush=True)
    return 0 if all(s == 'passed' for s in statuses.values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
