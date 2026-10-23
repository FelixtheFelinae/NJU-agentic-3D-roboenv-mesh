from pathlib import Path

import pytest
from pydantic import ValidationError

from roboenv_perception.construction.asset_agents import (
    AssetReview,
    child_environment,
    compile_review_qa,
    review_asset,
    validate_program,
)
from roboenv_perception.io import write_json


def test_generated_program_cannot_read_credentials_or_escape_bundle(monkeypatch):
    for source in [
        'import os\ndef qa_tests(ctx): pass',
        'def qa_tests(ctx): open(".env.local")',
        'def qa_tests(ctx): ctx.__class__',
        'from sdk import Mesh\ndef qa_tests(ctx): Mesh(filename="../secret.obj")',
        'def qa_tests(ctx): ctx.check("fake physics", True)',
    ]:
        with pytest.raises(ValueError):
            validate_program(source, 'qa_tests')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-only')
    monkeypatch.setenv('HF_TOKEN', 'test-only')
    monkeypatch.setenv('PYTHONPATH', '/unrelated/runtime')
    env = child_environment()
    assert not any(k in env for k in ['DASHSCOPE_API_KEY', 'HF_TOKEN', 'PYTHONPATH'])


def test_review_observations_are_separate_from_blockers():
    AssetReview(candidate_sha256='a'*64, decision='PASS', issues=[],
                observations=['The root is free.'], qa_plan=['topology', 'clearance'])
    with pytest.raises(ValidationError):
        AssetReview(candidate_sha256='a'*64, decision='PASS', issues=['Missing hinge'],
                    qa_plan=['topology', 'clearance'])


def test_review_plan_cannot_omit_contract_checks_or_fake_a_pass():
    rigid = {'kind': 'rigid', 'parts': [{'name': 'body'}]}
    with pytest.raises(ValueError):
        compile_review_qa(['clearance'], rigid)
    source = compile_review_qa(['topology', 'clearance'], rigid)
    validate_program(source, 'qa_tests')
    assert 'ctx.model.parts' in source and 'ctx.model.articulations' in source
    assert 'True' not in source
    with pytest.raises(ValueError):
        compile_review_qa(['topology', 'clearance'], {'kind': 'articulated'})


def test_review_rejects_failed_or_unbound_physics_before_api(tmp_path):
    workspace = tmp_path/'objects/asset-0003'
    workspace.mkdir(parents=True)
    (workspace/'model.py').write_text('candidate')
    write_json(workspace/'blueprint.json', {'category': 'mouse'})
    for report in [{'status': 'failed', 'checks': {'contact': False}},
                   {'status': 'passed', 'checks': {'contact': True}, 'candidate_sources': {}}]:
        with pytest.raises(ValueError):
            review_asset(None, workspace, [], report, Path('/unused'), '')
