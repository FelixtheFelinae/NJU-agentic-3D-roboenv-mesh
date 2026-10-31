"""Compose a validated box candidate into the existing wrist-tool robot scene."""
import argparse,json,shutil,hashlib,math
from pathlib import Path
from pxr import Usd,Sdf,Gf,UsdGeom,UsdPhysics
p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--asset',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
shutil.copytree(a.base,a.output);path=a.output/'scene/scene.usda';stage=Usd.Stage.Open(str(path));source=Usd.Stage.Open(str(a.asset/'triangular_box.usda'));offset=Gf.Vec3d(.5,0,.75)
for name in ['body','lid',*[f'screw_{i}' for i in range(3)],*[f'thread_guide_{i}' for i in range(3)]]:stage.RemovePrim('/World/'+name)
for prim in source.GetDefaultPrim().GetChildren():
 name=prim.GetName();Sdf.CopySpec(source.GetRootLayer(),prim.GetPath(),stage.GetRootLayer(),prim.GetPath());dst=stage.GetPrimAtPath(prim.GetPath())
 if dst.HasAPI(UsdPhysics.RigidBodyAPI):
  UsdGeom.Xformable(dst).AddTranslateOp(opSuffix='placement').Set(offset)
 if name.startswith('screw_'):
  spec=json.loads(dst.GetAttribute('envcreator:fastenerSpec').Get());spec['origin_world_m']=[x+y for x,y in zip(spec['origin_world_m'],offset)];dst.GetAttribute('envcreator:fastenerSpec').Set(json.dumps(spec))
world=stage.GetPrimAtPath('/World')
# Replacing the rigid body must retain the scene's declared sensor capability.
for row in json.loads(world.GetAttribute('envcreator:contactViews').Get() or '[]'):
 sensor=stage.GetPrimAtPath(row['body'].replace('{SCENE}','/World'))
 if not sensor or not sensor.HasAPI(UsdPhysics.RigidBodyAPI):
  raise ValueError('Declared contact sensor has no rigid body: '+row['body'])
 sensor.AddAppliedSchema('PhysxContactReportAPI')
 sensor.CreateAttribute('physxContactReport:threshold',Sdf.ValueTypeNames.Float).Set(0.)
world.CreateAttribute('envcreator:screwPaths',Sdf.ValueTypeNames.StringArray).Set([f'screw_{i}' for i in range(3)]);stage.GetRootLayer().Save()
sceneid='g1-triangle-m4x07-native-helical-v9'
delivery=json.loads((a.output/'metadata/delivery.json').read_text());delivery.update(scene_id=sceneid,status='unverified',scope='native helical assembly, equivalent preload, wrist-fixed electric tool',physics_status='see v9 validation artifacts; robot combined validation pending');(a.output/'metadata/delivery.json').write_text(json.dumps(delivery,indent=2))
manifest=json.loads((a.output/'manifest.json').read_text());manifest['scene']['id']=sceneid;manifest['delivery_status']='unverified';manifest['files_sha256']={str(p.relative_to(a.output)):hashlib.sha256(p.read_bytes()).hexdigest() for p in a.output.rglob('*') if p.is_file() and p.name!='manifest.json'};manifest['source_delivery_sha256']=manifest['files_sha256']['metadata/delivery.json'];(a.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
