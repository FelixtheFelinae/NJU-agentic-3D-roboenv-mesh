"""Offline boundary tests: real worker/subprocess, a deliberately small fake SDK."""

import copy
import json
import subprocess
import sys

import pytest

from roboenv_perception.construction import artisan_asset_worker as worker
from roboenv_perception.construction import artisan_backend as backend


@pytest.fixture
def runtime(tmp_path):
    sdk_root = tmp_path / 'fake-sdk'
    sdk = sdk_root / 'sdk/runtime'
    sdk.mkdir(parents=True)
    (sdk.parent / '__init__.py').write_text('')
    (sdk / '__init__.py').write_text('')
    (sdk / 'runtime.py').write_text('''
import json
import time
from dataclasses import dataclass
@dataclass
class Outcome:
    status: str

def compile_for_agent(model, report):
    text = model.read_text()
    if 'sleep_compile' in text:
        time.sleep(30)
    passed = 'fail_compile' not in text
    report.write_text(json.dumps({'compile': passed}))
    return Outcome('ok' if passed else 'failed'), 'fake compiler findings'

def run_qa_tests(model, source):
    if 'qa_error' in source:
        raise RuntimeError('QA infrastructure failure')
    return {'ok': True, 'passed': 'qa_fail' not in source,
            'failures': ['wrong hinge'] if 'qa_fail' in source else []}
''')
    (sdk / 'render.py').write_text('''
def export(model, formats, destination):
    text = model.read_text()
    paths = {}
    meshes = destination / 'meshes'
    meshes.mkdir()
    (meshes / 'body.obj').write_text('mesh fixture')
    for fmt in formats:
        path = destination / ('object.' + {'mjcf': 'xml'}.get(fmt, fmt))
        if 'omit_' + fmt not in text:
            path.write_text(fmt + ' fixture')
        paths[fmt] = str(path)
    if 'mutate_model' in text:
        model.write_text(text + '\\n# changed during export')
    return {'exports': paths, 'skipped': []}
''')
    interpreter = tmp_path / 'fake-artisan-python'
    interpreter.write_text(f'''#!{sys.executable}
import runpy
import sys
from pathlib import Path
sys.argv = sys.argv[1:]
sys.path[:0] = [{str(sdk_root)!r}, str(Path(sys.argv[0]).parent)]
runpy.run_path(sys.argv[0], run_name='__main__')
''')
    interpreter.chmod(0o755)
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'model.py').write_text('# fixed candidate')
    (bundle / 'inputs').mkdir()
    (bundle / 'inputs/body.obj').write_text('input mesh fixture')
    return interpreter, bundle


def request_for(bundle, report):
    key = report['backend_result']['request_id']
    return json.loads((bundle / '.artisan-requests' / (key + '.json')).read_text())


def test_explicit_compile_then_selected_export(runtime):
    python, bundle = runtime
    compiled = backend.run_artisan(python, bundle, 'compile')
    assert compiled['backend_result']['phases'] == {
        'compile': 'passed', 'export': 'not_run', 'qa': 'not_run', 'union': 'not_run'}
    assert not (bundle / 'export').exists()
    exported = backend.run_artisan(python, bundle, 'export', formats=['mjcf'])
    assert set(exported['exports']) == {'mjcf'}
    receipt = exported['backend_result']
    assert receipt['phases']['compile'] == 'not_run'
    assert receipt['physics_status'] == 'not_run'
    assert 'export/meshes/body.obj' in receipt['artifact_sha256']
    assert json.loads((bundle / 'compile.json').read_text()) == compiled


def test_build_compatibility_and_legacy_reexports(runtime):
    from roboenv_perception.construction.asset_agents import run_artisan

    python, bundle = runtime
    assert run_artisan is backend.run_artisan
    report = run_artisan(python, bundle)
    assert set(report['exports']) == {'urdf', 'mjcf', 'usd'}
    assert json.loads((bundle / 'build.json').read_text()) == report
    log = bundle / '.artisan-requests' / (report['backend_result']['request_id'] + '.log')
    assert log.read_text() == (bundle / 'build.log').read_text()
    assert backend.verify_result(bundle, request_for(bundle, report), report) == report
    # Direct worker CLI remains available to old scripts, without a request argument.
    process = subprocess.run([str(python), str(backend.WORKER), 'build', str(bundle)],
                             capture_output=True, text=True)
    assert process.returncode == 0, process.stdout + process.stderr
    assert list((bundle / '.artisan-requests').glob('*.previous-export'))


def test_compile_failure_never_exports(runtime):
    python, bundle = runtime
    (bundle / 'model.py').write_text('# fail_compile')
    with pytest.raises(RuntimeError, match='failed'):
        backend.run_artisan(python, bundle)
    result = json.loads((bundle / 'build.json').read_text())
    assert result['backend_result']['phases']['compile'] == 'failed'
    assert result['backend_result']['phases']['export'] == 'not_run'
    assert not (bundle / 'export').exists()


@pytest.mark.parametrize('changed', ['model.py', 'inputs/body.obj', 'blueprint.json'])
def test_export_rejects_changed_compile_inputs(runtime, changed):
    python, bundle = runtime
    backend.run_artisan(python, bundle, 'compile')
    (bundle / changed).write_text('changed source')
    with pytest.raises(RuntimeError, match='failed'):
        backend.run_artisan(python, bundle, 'export')
    assert not (bundle / 'export').exists()


def test_missing_export_file_cannot_reuse_old_output(runtime):
    python, bundle = runtime
    backend.run_artisan(python, bundle)
    (bundle / 'model.py').write_text('# omit_usd')
    with pytest.raises(RuntimeError, match='missing current Artisan export: usd'):
        backend.run_artisan(python, bundle)
    result = json.loads((bundle / 'build.json').read_text())
    assert result['backend_result']['phases']['export'] == 'failed'
    assert not (bundle / 'export/object.usd').exists()


@pytest.mark.parametrize('path', ['export/object.usd', 'export/meshes/body.obj'])
def test_changed_artifacts_invalidate_result(runtime, path):
    python, bundle = runtime
    report = backend.run_artisan(python, bundle)
    (bundle / path).write_text('modified output')
    with pytest.raises(ValueError, match='output changed'):
        backend.verify_result(bundle, request_for(bundle, report), report)


def test_qa_findings_are_feedback_but_execution_errors_raise(runtime):
    python, bundle = runtime
    (bundle / 'qa_tests.py').write_text('# qa_fail')
    report = backend.run_artisan(python, bundle, 'qa')
    assert report['passed'] is False
    assert report['failures'] == ['wrong hinge']
    assert report['backend_result']['failure_domain'] == 'qa_findings'
    assert report['backend_result']['physics_status'] == 'not_run'
    (bundle / 'qa_tests.py').write_text('# qa_error')
    with pytest.raises(RuntimeError, match='QA infrastructure failure'):
        backend.run_artisan(python, bundle, 'qa')


def test_stale_legacy_json_does_not_count_as_current_result(runtime, tmp_path):
    _, bundle = runtime
    backend.write_record(bundle / 'build.json', {'passed': True})
    noop = tmp_path / 'noop'
    noop.write_text(f'#!{sys.executable}\n')
    noop.chmod(0o755)
    with pytest.raises(RuntimeError, match='no current result'):
        backend.run_artisan(noop, bundle)


def test_input_mutation_during_worker_execution_is_rejected(runtime):
    python, bundle = runtime
    (bundle / 'model.py').write_text('# mutate_model')
    with pytest.raises(ValueError, match='inputs changed'):
        backend.run_artisan(python, bundle)


@pytest.mark.parametrize('mutation', ['request', 'physics', 'phase', 'findings', 'unbound_export'])
def test_receipt_consistency(runtime, mutation):
    python, bundle = runtime
    report = copy.deepcopy(backend.run_artisan(python, bundle))
    request = request_for(bundle, report)
    receipt = report['backend_result']
    if mutation == 'request':
        receipt['request_id'] = 'older-call'
    elif mutation == 'physics':
        receipt['physics_status'] = 'passed'
    elif mutation == 'phase':
        receipt['phases']['export'] = 'not_run'
    elif mutation == 'findings':
        report['failures'] = ['contradictory finding']
    else:
        receipt['artifact_sha256'].pop('export/object.usd')
    with pytest.raises(ValueError):
        backend.verify_result(bundle, request, report)


def test_modes_formats_and_symlinks_are_checked(runtime, tmp_path):
    _, bundle = runtime
    for mode, formats in [('unknown', None), ('build', []), ('build', ['usd', 'usd']),
                          ('export', ['cad']), ('compile', ['usd'])]:
        with pytest.raises(ValueError):
            backend.make_request(bundle, mode, formats)
    (bundle / 'inputs/escape').symlink_to(tmp_path / 'fake-sdk', target_is_directory=True)
    with pytest.raises(ValueError, match='bundle-local'):
        backend.make_request(bundle, 'build')


def test_timeout_cannot_use_old_success(runtime):
    python, bundle = runtime
    (bundle / 'model.py').write_text('# sleep_compile')
    backend.write_record(bundle / 'build.json', {'passed': True})
    with pytest.raises(subprocess.TimeoutExpired):
        backend.run_artisan(python, bundle, timeout=0.15)


def test_union_has_no_sdk_requirement(runtime, monkeypatch):
    _, bundle = runtime
    monkeypatch.setattr(worker, 'union_meshes', lambda workspace: ({'passed': True}, {}))
    result = worker.execute(bundle, backend.make_request(bundle, 'union'))
    assert result['passed'] is True
    assert result['backend_result']['phases']['compile'] == 'not_run'
    assert result['backend_result']['phases']['union'] == 'passed'
