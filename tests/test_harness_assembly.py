import copy
import json

import pytest

from experiments.cart_full.repair_fasteners import REFS, repair
from experiments.cart_full.spec import build
from roboenv_perception.harness.assembly import AssemblyPlan, audit_assembly
from roboenv_perception.harness.assembly_adapter import cart_parts


def candidate():
    spec, plan = repair(build())
    return spec, plan


def test_repaired_connections_and_conditional_access():
    spec, plan = candidate()
    report = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert report['status'] == 'geometrically_plausible', report['issues']
    assert len(report['connections']) == 8
    assert report['physics_status'] == 'not_run'
    assert all(r['facts']['merged_into_one_body'] for r in report['connections'])
    assert sum(r['facts']['tool_access_status'] == 'requires_unverified_predecessor'
               for r in report['connections']) == 4


def test_original_geometry_cannot_pass_using_repaired_semantics():
    original = build()
    _, plan = candidate()
    report = audit_assembly(cart_parts(original), plan, allowed_refs=REFS)
    assert report['status'] == 'failed'
    assert any('missing connection member' in x for x in report['issues'])


def test_reintroduced_plate_collision_fails_even_with_one_body():
    spec, plan = candidate()
    rail = next(p for p in spec['parts'] if p['id'] == 'rail_left')
    rail['center'][0] += .00635
    report = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert any('shaft intersects solid material' in x for x in report['issues'])


def test_short_shaft_and_floating_bracket_rejected():
    spec, plan = candidate()
    screw = next(p for p in spec['parts'] if p['id'] == 'mount_screw_left')
    screw['solids'][1].update(length=.002, center=[0, 0, -.0024])
    report = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert any('reach' in x or 'short' in x for x in report['issues'])
    spec, plan = candidate()
    bracket = next(p for p in spec['parts'] if p['id'] == 'bumper_mount_left')
    bracket['center'][2] -= .005
    report = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert report['status'] == 'failed'


def test_tool_blocker_is_not_silently_deleted():
    spec, plan = candidate()
    c = next(c for c in plan.connections if c.fastener == 'mount_screw_left')
    c.access_predecessors = []
    report = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert any('tool approach blocked' in x for x in report['issues'])
    c.access_predecessors = ['cross_front']
    report = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert any('invalid access predecessor' in x for x in report['issues'])


def test_wrong_nut_bore_and_unknown_evidence_rejected():
    spec, plan = candidate()
    mate = next(p for p in spec['parts'] if p['id'] == plan.connections[0].mate)
    mate['solids'][0]['inner_radius'] = .003
    report = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert any('thread-envelope bore' in x for x in report['issues'])
    plan.connections[0].evidence_refs = ['invented-source']
    assert any('unknown evidence' in x for x in audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)['issues'])


def test_missing_semantics_cannot_evade_required_task():
    assert audit_assembly([], None, allowed_refs=set(), required=True)['status'] == 'failed'
    assert audit_assembly([], None, allowed_refs=set())['status'] == 'not_applicable'
    spec, plan = candidate()
    plan.connections.pop()
    assert any('without connection semantics' in x for x in audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)['issues'])


def test_unsupported_axis_and_duplicate_connection_fail():
    spec, plan = candidate()
    plan.connections[0].axis = (1, 0, 0)
    assert audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)['status'] == 'failed'
    data = plan.model_dump(mode='json')
    data['connections'].append(copy.deepcopy(data['connections'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        AssemblyPlan.model_validate(data)


def test_manifest_hash_changes_with_geometry_or_hypothesis():
    spec, plan = candidate()
    first = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    spec['parts'][0]['center'][0] += .001
    second = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert first['geometry_sha256'] != second['geometry_sha256']
    plan.connections[0].engagement_m = .002
    third = audit_assembly(cart_parts(spec), plan, allowed_refs=REFS)
    assert second['plan_sha256'] != third['plan_sha256']


def test_harness_repair_loop_consumes_assembly_errors_before_export(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    import roboenv_perception.harness.run as run
    from roboenv_perception.harness.geometry import GeometryRecipe
    from roboenv_perception.io import write_json

    designs, evidence, research = [tmp_path / n for n in ('design', 'evidence', 'research')]
    blueprint = {'object_id': 'box', 'kind': 'rigid',
                 'frame_convention': 'metres; Z-up; front=+Y; local object frame',
                 'parts': [{'name': 'body', 'parent': None, 'role': 'body', 'geometry': 'box',
                            'material_appearance': 'gray', 'collision_intent': 'solid',
                            'evidence_refs': ['view-001']}], 'joints': [], 'acceptance': ['push']}
    write_json(designs / 'designs.json', {'objects': [{'asset_id': 'asset-0001', 'object_id': 'box', 'status': 'design_ready'}]})
    write_json(designs / 'objects/asset-0001/blueprint.json', blueprint)
    write_json(evidence / 'objects/asset-0001/evidence.json', {'views': []})
    write_json(research / 'research.json', {'objects': {}})
    seen = []

    def infer(*args):
        seen.append(list(args[5]))
        return GeometryRecipe(object_id='box', solids=[{
            'name': 'body', 'part': 'body', 'shape': 'box', 'size_m': [.1, .1, .1],
            'source': 'assumed', 'evidence_refs': ['view-001']}])

    monkeypatch.setattr(run, 'infer_recipe', infer)
    monkeypatch.setattr(run, 'infer_connections', lambda *a: (_ for _ in ()).throw(ValueError('bad proposal')))
    monkeypatch.setattr(run, 'build_recipe', lambda *a, **kw: pytest.fail('must not export invalid assembly'))
    settings = run.HarnessSettings(perception_config=tmp_path/'p', construction_config=tmp_path/'c',
                                   artisan_python=tmp_path/'a', max_geometry_revisions=2)
    run._build_assets(tmp_path/'out', designs, evidence, research, settings, SimpleNamespace(vlm=None),
                      time.monotonic()+30, assembly_required=True)
    assert len(seen) == 2
    assert 'missing assembly plan' in seen[1][0]
    report = json.loads((tmp_path/'out/assets.json').read_text())
    assert not report['all_visual_reviews_passed']
    assert (tmp_path/'out/asset-0001/candidate-000/assembly/assembly_validation.json').is_file()
