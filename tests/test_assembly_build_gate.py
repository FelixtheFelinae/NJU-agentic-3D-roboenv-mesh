"""Exercise the DSH component audit/build path with real geometry checks, no renderer."""

import json
from types import SimpleNamespace

import pytest

from roboenv_perception.components.operations import geometry_operation
from roboenv_perception.construction.contracts import Blueprint
from roboenv_perception.harness.assembly import persist_audit, require_audit
from roboenv_perception.harness.assembly_adapter import candidate_hashes, recipe_parts
from roboenv_perception.harness.geometry import GeometryRecipe
from roboenv_perception.io import read_json, write_json


def assembly_fixture():
    ref = 'mechanical-source'
    blueprint = Blueprint.model_validate({
        'object_id': 'bolt-fixture', 'kind': 'rigid',
        'frame_convention': 'metres; Z-up; front=+Y; local object frame',
        'parts': [{'name': name, 'parent': None if name == 'plate' else 'plate',
                   'role': name, 'geometry': 'schematic analytic fixture',
                   'material_appearance': 'gray', 'collision_intent': 'solid',
                   'evidence_refs': [ref]} for name in ('plate', 'bolt', 'nut')],
        'joints': [{'name': 'fix_' + name, 'parent': 'plate', 'child': name, 'type': 'fixed',
                    'origin_hint': 'analytic fixture origin', 'evidence_kind': 'assumed',
                    'evidence_refs': [ref]} for name in ('bolt', 'nut')],
        'acceptance': ['geometric plausibility only; not fastening proof'],
    })
    solids = [
        {'name': 'plate_surface', 'part': 'plate', 'shape': 'annulus', 'radius_m': .02,
         'inner_radius_m': .0025, 'length_m': .01, 'center_m': [0, 0, .005]},
        {'name': 'head', 'part': 'bolt', 'shape': 'cylinder', 'radius_m': .004,
         'length_m': .004, 'center_m': [0, 0, .012], 'assembly_role': 'fastener_head'},
        {'name': 'shaft', 'part': 'bolt', 'shape': 'cylinder', 'radius_m': .002,
         'length_m': .02, 'center_m': [0, 0, 0], 'assembly_role': 'fastener_shaft'},
        {'name': 'nut_surface', 'part': 'nut', 'shape': 'annulus', 'radius_m': .004,
         'inner_radius_m': .002, 'length_m': .004, 'center_m': [0, 0, -.002]},
    ]
    recipe = GeometryRecipe.model_validate({
        'object_id': blueprint.object_id,
        'solids': [dict(s, source='assumed', evidence_refs=[ref]) for s in solids],
        'joint_placements': [{'joint': j.name, 'origin_parent_m': [0, 0, 0],
                              'source': 'assumed', 'evidence_refs': [ref]} for j in blueprint.joints],
        'part_masses_kg': {p.name: .1 for p in blueprint.parts},
        'assembly': {'connections': [{
            'id': 'connection', 'fastener': 'bolt', 'head_solid': 'head', 'shaft_solid': 'shaft',
            'stack': ['plate'], 'mate': 'nut', 'mate_kind': 'through_nut',
            'retention': 'counterhold_tool', 'retention_basis': 'ideal second wrench assumed',
            'evidence_refs': [ref], 'observed': ['schematic fixture, not a video observation'],
            'assumptions': ['all dimensions are test assumptions'],
            'alternatives': ['tapped support'], 'selection_reason': 'test explicit stack',
            'pitch_m': .0008, 'engagement_m': .004,
        }]},
    })
    return blueprint, recipe, {ref}


@pytest.fixture
def component(tmp_path, monkeypatch):
    blueprint, recipe, refs = assembly_fixture()
    paths = {name: tmp_path / 'candidate' / (name + '.json') for name in ('blueprint', 'recipe')}
    write_json(paths['blueprint'], blueprint.model_dump(mode='json'))
    write_json(paths['recipe'], recipe.model_dump(mode='json'))
    write_json(paths['recipe'].parent / 'evidence_refs.json', {'refs': sorted(refs)})
    settings = SimpleNamespace(artisan_python=tmp_path / 'unused', blender='unused', max_wall_seconds=20)
    calls = []
    monkeypatch.setattr('roboenv_perception.harness.geometry.build_recipe',
                        lambda *args, **kwargs: calls.append((args, kwargs)))

    def invoke(operation, task='验证紧固'):
        output = tmp_path / operation
        return geometry_operation(operation, paths, output, settings, task, None, None)

    return paths, calls, invoke


def test_real_audit_can_reach_component_builder(component, tmp_path):
    paths, calls, invoke = component
    exports, status = invoke('assembly_audit')
    assert status == 'geometrically_plausible'
    paths['assembly'] = tmp_path / 'assembly_audit' / exports['assembly']
    report = read_json(paths['assembly'] / 'assembly_validation.json')
    assert report['verification']['domain'] == 'geometric_constraint'
    assert report['verification']['physics_status'] == 'not_run'
    assert invoke('build')[1] == 'unverified'
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', ['mass', 'blueprint', 'report', 'plan', 'geometry', 'refs'])
def test_component_blocks_changed_candidate_or_receipt(component, tmp_path, mutation):
    paths, calls, invoke = component
    invoke('assembly_audit')
    paths['assembly'] = tmp_path / 'assembly_audit/assembly'
    if mutation == 'report':
        path = paths['assembly'] / 'assembly_validation.json'
        data = read_json(path)
        data['connections'][0]['facts']['invented_pass'] = True
    elif mutation == 'blueprint':
        path = paths['blueprint']
        data = read_json(path)
        data['assumptions'].append('changed understanding')
    elif mutation == 'refs':
        path = paths['recipe'].parent / 'evidence_refs.json'
        data = read_json(path)
        data['refs'].append('another-source')
    else:
        path = paths['recipe']
        data = read_json(path)
        if mutation == 'mass':
            data['part_masses_kg']['plate'] = .2
        elif mutation == 'plan':
            data['assembly']['connections'][0]['assumptions'].append('new assumption')
        else:
            data['solids'][2]['center_m'][0] += .001
    write_json(path, data)
    with pytest.raises(ValueError, match='stale|validation failed'):
        invoke('build')
    assert not calls


def test_fastener_cannot_skip_audit_by_dropping_plan(component):
    paths, calls, invoke = component
    data = read_json(paths['recipe'])
    data['assembly'] = None
    write_json(paths['recipe'], data)
    with pytest.raises(ValueError, match='assembly_audit'):
        invoke('build', task='')
    assert not calls


def test_research_refs_survive_audit_and_build(component, tmp_path, monkeypatch):
    paths, calls, invoke = component
    # The recipe's source is mechanical research; visible views use another ID.
    paths['evidence'] = tmp_path / 'evidence'
    paths['research'] = tmp_path / 'research'
    write_json(paths['recipe'].parent / 'evidence_refs.json', {'refs': []})
    write_json(paths['research'] / 'research.json', {'objects': {'bolt-fixture': {
        'claims': [{'source_id': 'mechanical-source', 'status': 'candidate'}]}}})
    monkeypatch.setattr('roboenv_perception.components.operations.observation',
                        lambda *args: ({}, {'object_id': 'bolt-fixture',
                                            'views': [{'ref_id': 'video-view'}]}, []))
    invoke('assembly_audit')
    paths['assembly'] = tmp_path / 'assembly_audit/assembly'
    assert invoke('build')[1] == 'unverified'
    assert len(calls) == 1


@pytest.mark.parametrize('fault', ['wrong_bore', 'unsupported_axis', 'missing_plan'])
def test_faults_are_persisted_and_never_build(component, tmp_path, fault):
    paths, calls, invoke = component
    data = read_json(paths['recipe'])
    if fault == 'wrong_bore':
        data['solids'][3]['inner_radius_m'] = .003
    elif fault == 'unsupported_axis':
        data['assembly']['connections'][0]['axis'] = [1, 0, 0]
    else:
        data['assembly'] = None
    write_json(paths['recipe'], data)
    with pytest.raises(ValueError, match='assembly validation failed'):
        invoke('assembly_audit')
    report = read_json(tmp_path / 'assembly_audit/assembly/assembly_validation.json')
    assert report['status'] == 'failed'
    assert report['verification']['physics_status'] == 'not_run'
    paths['assembly'] = tmp_path / 'assembly_audit/assembly'
    with pytest.raises(ValueError, match='assembly validation failed'):
        invoke('build')
    assert not calls


def test_legacy_audit_is_readable_but_not_accepted_for_new_build(tmp_path):
    blueprint, recipe, refs = assembly_fixture()
    parts = recipe_parts(blueprint, recipe)
    report = persist_audit(tmp_path, parts, recipe.assembly, allowed_refs=refs)
    assert json.loads((tmp_path / 'assembly_validation.json').read_text()) == report
    with pytest.raises(ValueError, match='stale'):
        require_audit(report, parts, recipe.assembly, allowed_refs=refs,
                      candidate_hashes=candidate_hashes(blueprint, recipe))


def test_workflow_receives_failed_assembly_report_without_permitting_build(component, tmp_path):
    paths, calls, _ = component
    data = read_json(paths['recipe'])
    data['solids'][2]['center_m'][0] += .02
    write_json(paths['recipe'], data)
    settings = SimpleNamespace(artisan_python=tmp_path / 'unused', blender='unused', max_wall_seconds=20)
    exports, status = geometry_operation(
        'assembly_audit', paths, tmp_path / 'workflow-audit', settings, '验证紧固', None, None,
        workflow=True,
    )
    assert status == 'failed'
    paths['assembly'] = tmp_path / 'workflow-audit' / exports['assembly']
    assert read_json(paths['assembly'] / 'assembly_validation.json')['issues']
    with pytest.raises(ValueError, match='validation failed'):
        geometry_operation('build', paths, tmp_path / 'blocked-build', settings, '验证紧固', None, None)
    assert calls == []
