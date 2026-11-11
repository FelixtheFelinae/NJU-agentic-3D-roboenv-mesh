"""Real USD integration and observer controls; synthetic tests are not physical evidence."""

import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from roboenv_perception.io import read_json, write_json
from roboenv_perception.standard_interactions.compiler import compile_connections
from roboenv_perception.standard_interactions.observation import ReleaseObserver, evaluate_release_records

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / 'outputs/triangular-box-20261030-v9/delivery/triangular_box.usda'
COMPILED = REPO / 'outputs/standard-interactions-20261104/candidate-v1/compiled_assembly.json'


def local_components():
    pytest.importorskip('pxr.Usd')
    if not SOURCE.exists() or not COMPILED.exists():
        pytest.skip('local standard assets intentionally excluded from GitHub')
    return read_json(COMPILED)


def export_fixture(path, *, bad_joint=False, translation=0.):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    original = Usd.Stage.Open(str(SOURCE))
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, 'Z')
    root = UsdGeom.Xform.Define(stage, '/Export')
    stage.SetDefaultPrim(root.GetPrim())
    for name in ('body', 'lid', 'screw_0', 'screw_1', 'screw_2', 'Material', 'SteelContact'):
        Sdf.CopySpec(original.GetRootLayer(), '/World/'+name, stage.GetRootLayer(), '/Export/'+name)
    from roboenv_perception.standard_interactions.bundle import remap_relationships
    remap_relationships(stage, {'/World': '/Export'})
    if translation:
        body = UsdGeom.Xformable(stage.GetPrimAtPath('/Export/body'))
        body.ClearXformOpOrder()
        body.AddTranslateOp().Set(Gf.Vec3d(translation, 0, 0))
    for name in ('lid', 'screw_0', 'screw_1', 'screw_2'):
        joint = UsdPhysics.FixedJoint.Define(stage, '/Export/fixed_'+name)
        joint.CreateBody0Rel().SetTargets(['/Export/body'])
        joint.CreateBody1Rel().SetTargets(['/Export/'+name])
    if bad_joint:
        joint = UsdPhysics.FixedJoint.Define(stage, '/Export/world_weld')
        joint.CreateBody1Rel().SetTargets(['/Export/lid'])
    stage.GetRootLayer().Save()
    return path


def test_installs_real_slot_geometry_releases_exporter_welds_and_is_portable(tmp_path):
    compiled = local_components()
    from pxr import Usd, UsdPhysics
    from roboenv_perception.standard_interactions.bundle import install_bundle
    from roboenv_perception.standard_interactions.validation import verify_bundle

    raw = export_fixture(tmp_path/'raw.usda', translation=.02)
    out = tmp_path/'installed'
    report = install_bundle(raw, compiled, out)
    assert len(report['removed_closure_joints']) == 4
    installed = verify_bundle(out)
    assert installed['connections'][0]['binding']['origin_m'][0] == pytest.approx(
        compiled['connections'][0]['binding']['origin_m'][0]-.02)
    stage = Usd.Stage.Open(str(out/'assembly.usda'))
    assert not stage.GetPrimAtPath('/World/fixed_lid')
    assert stage.GetPrimAtPath('/World/WristTool/spindle').HasAPI(UsdPhysics.RigidBodyAPI)
    template = Usd.Stage.Open(str(SOURCE))
    # Exact registered points and collider pieces, not just a cylinder envelope.
    source_meshes = [p for p in template.Traverse() if str(p.GetPath()).startswith('/World/screw_0/') and p.GetTypeName() == 'Mesh']
    assert len(source_meshes) > 2
    for prim in source_meshes:
        target = stage.GetPrimAtPath(str(prim.GetPath()).replace('/screw_0/', '/screw_2/'))
        assert target.GetAttribute('points').Get() == prim.GetAttribute('points').Get()
    for p in stage.Traverse():
        if p.IsA(UsdPhysics.Joint):
            for rel in ('physics:body0', 'physics:body1'):
                assert all(stage.GetPrimAtPath(t) for t in p.GetRelationship(rel).GetTargets())
    with pytest.raises(FileExistsError):
        install_bundle(raw, compiled, out)
    (out/'runtime/fastener.py').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        verify_bundle(out)


def test_unknown_world_weld_cannot_be_silently_removed(tmp_path):
    compiled = local_components()
    from roboenv_perception.standard_interactions.bundle import install_bundle
    raw = export_fixture(tmp_path/'bad.usda', bad_joint=True)
    with pytest.raises(ValueError, match='unresolved constraint'):
        install_bundle(raw, compiled, tmp_path/'out')


def test_scene_reference_translation_updates_runtime_strings_and_preserves_support_local_frame(tmp_path):
    compiled = local_components()
    from pxr import Usd, UsdPhysics
    from roboenv_perception.harness.scene import SceneAsset, SupportSpec, assemble_scene
    from roboenv_perception.standard_interactions.bundle import install_bundle
    raw = export_fixture(tmp_path/'raw.usda')
    install_bundle(raw, compiled, tmp_path/'bundle')
    asset = SceneAsset(asset_id='box', kind='rigid', usd_path=tmp_path/'bundle/assembly.usda',
                       frame_id='f', bbox_xyxy=(20,20,80,70), image_size_xy=(100,100),
                       local_bounds_min_m=(-.2,-.2,0), local_bounds_max_m=(.2,.2,.1), evidence_refs=['assumed'])
    support = SupportSpec(frame_id='f', image_size_xy=(100,100), image_bbox_xyxy=(0,0,100,100),
                          size_x_m=1, size_y_m=1, top_z_m=.75, evidence_refs=['assumed'])
    assemble_scene([asset], tmp_path/'scene', support, usd_python=Path(sys.executable))
    stage = Usd.Stage.Open(str(tmp_path/'scene/scene.usda'))
    prefix = '/World/Assets/Asset_box'
    spec = json.loads(stage.GetPrimAtPath(prefix+'/screw_0').GetAttribute('envcreator:fastenerSpec').Get())
    assert spec['moving_support'] == prefix+'/body'
    assert spec['tool'] == prefix+'/WristTool'
    assert spec['origin_support_local_m'] == compiled['connections'][0]['binding']['origin_m']
    assert spec['origin_world_m'][2] == pytest.approx(.088+.755)
    assert stage.GetPrimAtPath(prefix+'/body').HasAPI(UsdPhysics.ArticulationRootAPI)
    assert len(read_json(tmp_path/'scene/standard_bindings.json')['bindings']) == 3


class FakeArray:
    def __init__(self, values):
        self.values = np.array(values, dtype=float)
    def __getitem__(self, key):
        return FakeArray(self.values[key])
    def cpu(self):
        return self
    def numpy(self):
        return self.values.copy()


class FakeView:
    def __init__(self):
        self.position = np.zeros(3)
        self.theta = 0.
    def get_transforms(self):
        return FakeArray([[*self.position, 0, 0, math.sin(self.theta/2), math.cos(self.theta/2)]])


def test_observer_uses_same_step_pose_and_accepts_post_release_rebound():
    enabled = SimpleNamespace(value=True)
    screw = SimpleNamespace(screw_path='/World/screw_0', screw=FakeView(), support=FakeView(),
                            support_origin=np.zeros(3), model=SimpleNamespace(
                                p=SimpleNamespace(lead_m=.0007, engagement_m=.008), thread_active=True),
                            guide_joint=SimpleNamespace(GetJointEnabledAttr=lambda: SimpleNamespace(Get=lambda: enabled.value)))
    observer = ReleaseObserver(SimpleNamespace(screws=[screw]))
    # Many turns are measured incrementally, independent of runtime cumulative theta.
    target = .00801/.0007*math.tau
    for theta in np.linspace(0,target,200):
        screw.screw.theta = theta
        screw.screw.position[2] = theta*.0007/math.tau
        observer.before_step(.001)
        observer.after_mechanics()
        observer.after_step()
    observer.before_step(.001)
    enabled.value = False
    screw.model.thread_active = False
    observer.after_mechanics()
    screw.screw.position[2] = .0078
    observer.after_step()
    records = observer.records
    assert len(records) == 1
    assert records[0]['before']['rotation_travel_m'] == pytest.approx(.00801)
    assert records[0]['after_integration']['exit_m'] == .0078
    assert evaluate_release_records(records, expected_ids=['/World/screw_0'])['status'] == 'passed'
    altered = copy.deepcopy(records)
    altered[0]['before']['exit_m'] = .007
    assert evaluate_release_records(altered, expected_ids=['/World/screw_0'])['status'] == 'failed'
    assert evaluate_release_records([], expected_ids=['/World/screw_0'])['status'] == 'failed'
    assert evaluate_release_records([], expected_ids=[])['status'] == 'not_exercised'


def test_build_recipe_calls_real_usd_installer_after_artisan_normalization(tmp_path, monkeypatch):
    compiled = local_components()
    from roboenv_perception.construction.contracts import Blueprint
    import roboenv_perception.harness.geometry as geometry
    from roboenv_perception.harness.geometry import GeometryRecipe

    blueprint = Blueprint.model_validate({'object_id':'box','kind':'rigid',
        'frame_convention':'metres; Z-up; front=+Y; local object frame',
        'parts':[{'name':'body','parent':None,'role':'body','geometry':'box','material_appearance':'gray',
                  'collision_intent':'solid','evidence_refs':['assumed']}], 'joints':[], 'acceptance':['push']})
    recipe = GeometryRecipe(object_id='box',solids=[{'name':'body','part':'body','shape':'box',
        'size_m':[.1,.1,.1],'source':'assumed','evidence_refs':['assumed']}],part_masses_kg={'body':1.})
    def fake_blender(worker, folder, **kwargs):
        folder.mkdir()
        (folder/'body.obj').write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        (folder/'preview.png').write_bytes(b'placeholder for test, not a rendering')
        return {'meshes':[{'file':'body.obj'}],'previews':['preview.png']}
    def fake_artisan(python, out, **kwargs):
        export_fixture(out/'raw.usda')
        return {'exports':{'usd':'raw.usda'}}
    monkeypatch.setattr(geometry,'build_blender_meshes',fake_blender)
    monkeypatch.setattr(geometry,'run_artisan',fake_artisan)
    result = geometry.build_recipe(blueprint,recipe,tmp_path/'built',artisan_python=Path(sys.executable),compiled_assembly=compiled)
    assert result['standard_bundle'] == str(tmp_path/'built/standard')
    assert result['usd'].endswith('standard/assembly.usda')
    assert len(result['standard_installation']['removed_closure_joints']) == 4
    assert result['physics_status'] == 'not_run'
