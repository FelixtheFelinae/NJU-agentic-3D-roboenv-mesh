"""EnvCreator triangular disassembly asset, generated from explicit user dimensions.

Uses the existing trusted Blender backend. No image inference is claimed for
this text-specified design. Physics execution is a separate recorded stage.
"""
import argparse,hashlib,json,math,sys
from pathlib import Path
from ..construction.blender_backend import build_blender_meshes
from ..io import write_json


def build(output, screw_scene, blender):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    r=.3/math.sqrt(3)
    # Holes 35 mm inward from each vertex along the angle bisector: side clearance
    # 17.5 mm, sufficient for the narrow nose; full housing clearance is evaluated separately.
    holes=[[(r-.035)*math.cos(math.pi/2+i*math.tau/3),
            (r-.035)*math.sin(math.pi/2+i*math.tau/3)] for i in range(3)]
    parameters={'side_m':.3,'height_m':.08,'wall_m':.004,'lid_m':.004,'holes_xy_m':holes}
    write_json(output/'design.json',{'schema':'envcreator.triangular-box.v1',**parameters,
        'evidence':{'side_m':'user: approximately 30 cm equilateral triangle',
                    'height_wall_lid_holes':'explicit design assumptions'},
        'method':'trusted Blender geometry + convex-prism collision partition + USD assembly',
        'left_hand_press_region_local_m':[0,0,.084],
        'unverified':['grasp/contact stability','lid clamp preload','robot bimanual execution'],
        'runtime':'AssemblyRuntime provides moving-body reciprocal coupling; requires native binding and validation'})
    result=build_blender_meshes(Path(__file__).with_name('triangular_box_blender.py'),output/'geometry',
                               parameters=parameters,blender=blender)
    export(output,parameters,result,Path(screw_scene).resolve())
    return str(output/'triangular_box.usda')


def convex_top_patches(verts,faces):
    """Merge coplanar top triangles into convex collision patches, preserving holes."""
    def area(poly):
        return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1])))/2
    def hull(points):
        points=sorted(set(points))
        def cross(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
        lo=[];hi=[]
        for q in points:
            while len(lo)>=2 and cross(lo[-2],lo[-1],q)<=1e-15:lo.pop()
            lo.append(q)
        for q in reversed(points):
            while len(hi)>=2 and cross(hi[-2],hi[-1],q)<=1e-15:hi.pop()
            hi.append(q)
        return lo[:-1]+hi[:-1]
    groups={}
    for face in faces:
        a,b,c=[verts[i] for i in face]
        cross=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        if cross<1e-12 or max(a[2],b[2],c[2])-min(a[2],b[2],c[2])>1e-7:continue
        z=round(a[2],7)
        groups.setdefault(z,[]).append([tuple(round(x,9) for x in v[:2]) for v in (a,b,c)])
    for z,polys in groups.items():
        changed=True
        while changed:
            changed=False
            for i in range(len(polys)):
                for j in range(i+1,len(polys)):
                    if len(set(polys[i])&set(polys[j]))<2:continue
                    merged=hull(polys[i]+polys[j])
                    if len(merged)<=24 and abs(area(merged)-area(polys[i])-area(polys[j]))<1e-12:
                        polys[i]=merged;polys.pop(j);changed=True;break
                if changed:break
        for poly in polys:
            if area(poly)>1e-12:yield z,poly


def export(output,p,result,screw_scene):
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdPhysics,UsdShade
    stage=Usd.Stage.CreateNew(str(output/'triangular_box.usda'))
    world=UsdGeom.Xform.Define(stage,'/World');stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
    mat=UsdShade.Material.Define(stage,'/World/Material')
    props=UsdPhysics.MaterialAPI.Apply(mat.GetPrim());props.CreateStaticFrictionAttr(.6);props.CreateDynamicFrictionAttr(.45)
    for row in result['meshes']:
        name=row['part']
        if name.startswith('screw_'):continue
        body=UsdGeom.Xform.Define(stage,'/World/'+name)
        UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
        mass=UsdPhysics.MassAPI.Apply(body.GetPrim());mass.CreateMassAttr(.65 if name=='body' else .20)
        # Let PhysX derive COM and inertia from the convex collision pieces.
        UsdShade.MaterialBindingAPI.Apply(body.GetPrim()).Bind(mat,materialPurpose='physics')
        verts=[];faces=[]
        for line in (output/'geometry'/row['file']).read_text().splitlines():
            fields=line.split()
            if fields and fields[0]=='v':verts.append(tuple(map(float,fields[1:])))
            if fields and fields[0]=='f':faces.append(tuple(int(s.split('/')[0])-1 for s in fields[1:]))
        mesh=UsdGeom.Mesh.Define(stage,str(body.GetPath())+'/visual')
        mesh.CreatePointsAttr([Gf.Vec3f(*v) for v in verts]);mesh.CreateFaceVertexCountsAttr([len(f) for f in faces])
        mesh.CreateFaceVertexIndicesAttr([i for f in faces for i in f]);mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateDisplayColorAttr([Gf.Vec3f(*row['rgba'][:3])])
        count=0
        # Blender has triangulated the closed surfaces. Upward horizontal faces
        # partition the shell and pierced lid into convex triangular prisms,
        # keeping the cavity, blind pilot holes and lid clearance holes open.
        for top,poly in convex_top_patches(verts,faces):
            if name=='lid':bottom=p['height_m']
            elif abs(top-p['wall_m'])<1e-6:bottom=0.
            else:bottom=p['wall_m']
            n=len(poly)
            points=[Gf.Vec3f(v[0],v[1],z) for z in (bottom,top) for v in poly]
            col=UsdGeom.Mesh.Define(stage,str(body.GetPath())+f'/collision_{count}')
            index_faces=[list(reversed(range(n))),list(range(n,2*n))]
            index_faces += [[i,(i+1)%n,(i+1)%n+n,i+n] for i in range(n)]
            # Cook millimetre-scale details near a local origin in millimetres,
            # then restore metres through the mesh transform. PhysX convex
            # cooking of long thin metre-coordinate patches can distort holes.
            center=[sum(v[a] for v in points)/len(points) for a in range(3)]
            col.CreatePointsAttr([Gf.Vec3f(*[(v[a]-center[a])*1000. for a in range(3)]) for v in points])
            col.AddTranslateOp().Set(Gf.Vec3d(*center));col.AddScaleOp().Set(Gf.Vec3f(.001))
            col.CreateFaceVertexCountsAttr([len(f) for f in index_faces])
            col.CreateFaceVertexIndicesAttr([i for f in index_faces for i in f]);col.CreateSubdivisionSchemeAttr('none')
            col.CreateVisibilityAttr('invisible');UsdPhysics.CollisionAPI.Apply(col.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(col.GetPrim()).CreateApproximationAttr('convexHull');count+=1
        if not count:raise ValueError('No collision partition generated')
    source=Usd.Stage.Open(str(screw_scene))
    original=source.GetPrimAtPath('/World/TargetScrew')
    assembly=[]
    for i,xy in enumerate(p['holes_xy_m']):
        path=f'/World/screw_{i}'
        Sdf.CopySpec(source.GetRootLayer(),original.GetPath(),stage.GetRootLayer(),Sdf.Path(path))
        prim=stage.GetPrimAtPath(path)
        origin=[*xy,p['height_m']+p['lid_m']+.004]
        prim.GetAttribute('xformOp:translate').Set(Gf.Vec3d(*origin))
        spec=json.loads(prim.GetAttribute('envcreator:fastenerSpec').Get())
        spec.update(screw=path,fixed_support=None,moving_support='/World/body',clamped_member='/World/lid',origin_world_m=origin,
                    origin_support_local_m=origin,runtime_binding='AssemblyRuntime moving-body coupling; robot validation required')
        guide_path=f'/World/thread_guide_{i}'
        guide=UsdPhysics.Joint.Define(stage,guide_path)
        guide.CreateBody0Rel().SetTargets(['/World/body']);guide.CreateBody1Rel().SetTargets([path])
        guide.CreateLocalPos0Attr(Gf.Vec3f(*origin));guide.CreateLocalPos1Attr(Gf.Vec3f(0))
        for axis in ('transX','transY','rotX','rotY'):
            limits=UsdPhysics.LimitAPI.Apply(guide.GetPrim(),axis)
            limits.CreateLowAttr(1.);limits.CreateHighAttr(-1.)
        guide.CreateCollisionEnabledAttr(False)
        spec.update(guide_joint=guide_path,axial_contact_enabled=True)
        spec['assumptions']=[v for v in spec['assumptions'] if 'fixed anti-rotated' not in v]
        spec['assumptions'] += ['moving support with reciprocal wrench and solver-native lateral/tilt guide',
            'guide released after thread clears; unilateral axial and compliant socket-wall contact',
            'no calibrated clamp preload or resolved helical/chamfer contact']
        prim.GetAttribute('envcreator:fastenerSpec').Set(json.dumps(spec));assembly.append(spec)
        UsdPhysics.FilteredPairsAPI.Apply(prim).CreateFilteredPairsRel().AddTarget('/World/body')
        # Matching existing M4-like head underside bears on the top lid surface.
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim.AddAppliedSchema('PhysxRigidBodyAPI')
            for name,value,kind in [('maxDepenetrationVelocity',.05,Sdf.ValueTypeNames.Float),
                                    ('solverPositionIterationCount',32,Sdf.ValueTypeNames.Int),
                                    ('solverVelocityIterationCount',8,Sdf.ValueTypeNames.Int)]:
                prim.CreateAttribute('physxRigidBody:'+name,kind).Set(value)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            prim.AddAppliedSchema('PhysxCollisionAPI')
            prim.CreateAttribute('physxCollision:contactOffset',Sdf.ValueTypeNames.Float).Set(.00005)
            prim.CreateAttribute('physxCollision:restOffset',Sdf.ValueTypeNames.Float).Set(0.)
        if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
            # PhysX minimum thickness uses physical scene units after scaling.
            prim.AddAppliedSchema('PhysxConvexHullCollisionAPI')
            prim.CreateAttribute('physxConvexHullCollision:minThickness',Sdf.ValueTypeNames.Float).Set(.00001)
    stage.GetRootLayer().Save()
    write_json(output/'assembly.json',{'screws':assembly,'body':'/World/body','lid':'/World/lid',
        'runtime_status':'not_bound','physics_status':'not_run','fixed_to_world':False,
        'assumptions':['mass body .65 kg and lid .20 kg; unmeasured',
          'friction static .6 dynamic .45; unmeasured','no physical helical thread contact'],
        'source_sha256':hashlib.sha256(screw_scene.read_bytes()).hexdigest()})
    write_json(output/'hashes.json',{str(f.relative_to(output)):hashlib.sha256(f.read_bytes()).hexdigest()
        for f in output.rglob('*') if f.is_file()})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--screw-scene',type=Path,required=True);parser.add_argument('--blender',type=Path,required=True)
    a=parser.parse_args();print(build(a.output,a.screw_scene,a.blender))
