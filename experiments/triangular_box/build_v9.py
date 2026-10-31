"""Versioned material and solver-thread candidate. Run with USD Python bindings."""
import argparse,json,math,hashlib,sys
from pathlib import Path
import numpy as np
from pxr import Usd,UsdGeom,UsdPhysics,UsdShade,Sdf,Gf


def mesh_properties(path,density):
    vertices=[];faces=[]
    for line in path.read_text().splitlines():
        a=line.split()
        if a and a[0]=='v':vertices.append(list(map(float,a[1:4])))
        if a and a[0]=='f':faces.append([int(i.split('/')[0])-1 for i in a[1:]])
    volume=0.;first=np.zeros(3);second=np.zeros((3,3));v=np.array(vertices)
    for face in faces:
        for i in range(1,len(face)-1):
            a,b,c=v[[face[0],face[i],face[i+1]]];dv=np.dot(a,np.cross(b,c))/6
            sm=a+b+c;volume+=dv;first+=dv*sm/4
            second+=dv*(np.outer(sm,sm)+np.outer(a,a)+np.outer(b,b)+np.outer(c,c))/20
    if volume<0:volume=-volume;first=-first;second=-second
    center=first/volume;mass=volume*density
    inertia=density*(np.eye(3)*np.trace(second)-second)-mass*(np.eye(3)*np.dot(center,center)-np.outer(center,center))
    eig,axes=np.linalg.eigh(inertia)
    if np.linalg.det(axes)<0:axes[:,0]*=-1
    rotation=Gf.Matrix3d(*axes.T.flatten().tolist()).ExtractRotation().GetQuat()
    return mass,center,eig,rotation,volume


def build(source,output,pretension=2500.):
    output.mkdir(parents=True,exist_ok=False)
    stage=Usd.Stage.Open(str(source/'triangular_box.usda'));stage.GetRootLayer().Export(str(output/'triangular_box.usda'))
    stage=Usd.Stage.Open(str(output/'triangular_box.usda'));properties={}
    # Explicit material selection: aluminium enclosure, steel socket screws.
    result=json.loads((source/'geometry/result.json').read_text())
    for row in result['meshes']:
        name=row['part'];density=7850. if name.startswith('screw_') else 2700.
        mass,com,inertia,axes,volume=mesh_properties(source/'geometry'/row['file'],density)
        prim=stage.GetPrimAtPath('/World/'+name)
        if name.startswith('screw_'):
            origin=json.loads(prim.GetAttribute('envcreator:fastenerSpec').Get())['origin_world_m'];com-=origin
        api=UsdPhysics.MassAPI.Apply(prim);api.CreateMassAttr(mass);api.CreateCenterOfMassAttr(Gf.Vec3f(*com));api.CreateDiagonalInertiaAttr(Gf.Vec3f(*inertia));api.CreatePrincipalAxesAttr(Gf.Quatf(axes))
        properties[name]={'mass_kg':mass,'center_of_mass_local_m':com.tolist(),'principal_inertia_kg_m2':inertia.tolist(),'density_kg_m3':density,'volume_m3':volume}
    for name,mu in [('Material',.45),('SteelContact',.14)]:
        mat=UsdShade.Material.Define(stage,'/World/'+name);api=UsdPhysics.MaterialAPI.Apply(mat.GetPrim());api.CreateStaticFrictionAttr(mu);api.CreateDynamicFrictionAttr(mu*.85);api.CreateRestitutionAttr(0.)
    UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath('/World/body'))
    root=stage.GetPrimAtPath('/World/body');root.AddAppliedSchema('PhysxArticulationAPI')
    for name,value,kind in [('solverPositionIterationCount',128,Sdf.ValueTypeNames.Int),('solverVelocityIterationCount',4,Sdf.ValueTypeNames.Int),('sleepThreshold',0.,Sdf.ValueTypeNames.Float),('stabilizationThreshold',0.,Sdf.ValueTypeNames.Float)]:
        root.CreateAttribute('physxArticulation:'+name,kind).Set(value)
    for i in range(3):
        prim=stage.GetPrimAtPath(f'/World/screw_{i}');spec=json.loads(prim.GetAttribute('envcreator:fastenerSpec').Get());origin=spec['origin_world_m']
        spec['solver']='native-mimic-helical-v1'
        spec['solver_thread']={'pretension_N':pretension,'relaxation_m':.00004,'settling_ramp_s':1.,'thread_mu':.14,'bearing_mu':.14,'friction_stiffness_Nm_rad':1000.,'bearing_radius_m':.003,'preload_application':'clamped_members','lid_body':'/World/lid','pitch_diameter_m':.00354533666304,'running_torque_Nm':.005,'friction_damping_Nm_s_rad':.01}
        spec['lead_joint']=f'/World/lead_joint_{i}';spec['helper_body']=f'/World/lead_{i}';spec['rotor_body']=f'/World/thread_rotor_{i}';spec['friction_joint']=f'/World/thread_hinge_{i}'
        spec['assumptions']=['M4x0.7 single-start native helical constraint, no resolved thread surfaces','aluminium enclosure and steel screw densities; friction unmeasured','preassembled bolt-stack preload relaxes over 40 micrometres; not resolved elastic stress','reduced bolt tension applies clamp load to lid/body; native joint supplies thread and preload bearing resistance; actual bolt internal stress unresolved','ideal wrist-fixed tool and capped magnetic retention remain']
        prim.GetAttribute('envcreator:fastenerSpec').Set(json.dumps(spec))
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(UsdShade.Material.Get(stage,'/World/SteelContact'),materialPurpose='physics')
        stage.RemovePrim(spec['guide_joint'])
        helper=UsdGeom.Xform.Define(stage,spec['helper_body']);helper.AddTranslateOp().Set(Gf.Vec3d(*origin));UsdPhysics.RigidBodyAPI.Apply(helper.GetPrim())
        ma=UsdPhysics.MassAPI.Apply(helper.GetPrim());ma.CreateMassAttr(.001);ma.CreateDiagonalInertiaAttr(Gf.Vec3f(1e-7));ma.CreateCenterOfMassAttr(Gf.Vec3f(0))
        properties[f'lead_{i}']={'mass_kg':.001,'reason':'numerical lead-coordinate carrier; no collision geometry'}
        properties[f'thread_rotor_{i}']={'mass_kg':.001,'reason':'numerical angular-coordinate carrier; no collision geometry'}
        slide=UsdPhysics.PrismaticJoint.Define(stage,spec['lead_joint']);slide.CreateBody0Rel().SetTargets(['/World/body']);slide.CreateBody1Rel().SetTargets([helper.GetPath()]);slide.CreateAxisAttr('Z');slide.CreateLocalPos0Attr(Gf.Vec3f(*origin))
        rotor=UsdGeom.Xform.Define(stage,spec['rotor_body']);rotor.AddTranslateOp().Set(Gf.Vec3d(*origin));UsdPhysics.RigidBodyAPI.Apply(rotor.GetPrim());ma=UsdPhysics.MassAPI.Apply(rotor.GetPrim());ma.CreateMassAttr(.001);ma.CreateDiagonalInertiaAttr(Gf.Vec3f(1e-7));ma.CreateCenterOfMassAttr(Gf.Vec3f(0))
        connector=UsdPhysics.FixedJoint.Define(stage,spec['guide_joint']);connector.CreateBody0Rel().SetTargets([rotor.GetPath()]);connector.CreateBody1Rel().SetTargets([prim.GetPath()]);connector.CreateExcludeFromArticulationAttr(True);connector.CreateCollisionEnabledAttr(False)
        hinge=UsdPhysics.RevoluteJoint.Define(stage,spec['friction_joint']);hinge.CreateBody0Rel().SetTargets([helper.GetPath()]);hinge.CreateBody1Rel().SetTargets([rotor.GetPath()]);hinge.CreateAxisAttr('Z');hinge.CreateLowerLimitAttr(-1e8);hinge.CreateUpperLimitAttr(1e8)
        d=UsdPhysics.DriveAPI.Apply(hinge.GetPrim(),'angular');d.CreateTypeAttr('force');d.CreateStiffnessAttr(0.);d.CreateDampingAttr(1e6*math.pi/180);d.CreateMaxForceAttr(.005);d.CreateTargetVelocityAttr(0.)
        # Friction acts directly on the physical screw, not through the light
        # coordinate carrier and its detachable connector.
        d.GetMaxForceAttr().Set(0.)
        friction=UsdPhysics.Joint.Define(stage,f'/World/friction_{i}')
        friction.CreateBody0Rel().SetTargets(['/World/body']);friction.CreateBody1Rel().SetTargets([prim.GetPath()])
        friction.CreateExcludeFromArticulationAttr(True)
        frame=Gf.Quatf(math.sqrt(.5),Gf.Vec3f(0,-math.sqrt(.5),0))
        friction.CreateLocalRot0Attr(frame);friction.CreateLocalRot1Attr(frame)
        fd=UsdPhysics.DriveAPI.Apply(friction.GetPrim(),'rotX');fd.CreateTypeAttr('force');fd.CreateStiffnessAttr(1000*math.pi/180);fd.CreateDampingAttr(.01*math.pi/180);fd.CreateMaxForceAttr(.005);fd.CreateTargetVelocityAttr(0.)
        spec['friction_joint']=str(friction.GetPath());spec['friction_axis']='rotX'
        prim.GetAttribute('envcreator:fastenerSpec').Set(json.dumps(spec))
        slide.GetPrim().AddAppliedSchema('PhysxMimicJointAPI:rotX');slide.GetPrim().CreateRelationship('physxMimicJoint:rotX:referenceJoint').SetTargets([hinge.GetPath()]);slide.GetPrim().CreateAttribute('physxMimicJoint:rotX:gearing',Sdf.ValueTypeNames.Float).Set(-spec['lead_m']/360);slide.GetPrim().CreateAttribute('physxMimicJoint:rotX:offset',Sdf.ValueTypeNames.Float).Set(0.)
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            prim.CreateAttribute('physxRigidBody:solverPositionIterationCount',Sdf.ValueTypeNames.Int).Set(64)
            prim.CreateAttribute('physxRigidBody:solverVelocityIterationCount',Sdf.ValueTypeNames.Int).Set(8)
    stage.GetRootLayer().Save();(output/'mass-properties.json').write_text(json.dumps(properties,indent=2))
    (output/'design.json').write_text(json.dumps({'status':'candidate-unverified','source':str(source),'materials':'aluminium enclosure 2700kg/m3, steel screw 7850kg/m3','pretension_design_N_per_screw':pretension,'physics':'native-mimic-helical-v1','mass_properties':properties},indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--pretension',type=float,default=2500.);a=p.parse_args();build(a.source,a.output,a.pretension)
