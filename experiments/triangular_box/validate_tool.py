"""Actual exported triangular-box/tool contact bench; ideal fixed tool housing.

50 N prescribed lid press is a bench load, NOT a robot left hand demonstration.
"""
import sys,json,math,time,threading,argparse
p=argparse.ArgumentParser()
p.add_argument('--asset',required=True);p.add_argument('--tool',required=True)
p.add_argument('--output',required=True);p.add_argument('--seconds',type=float,default=40.)
p.add_argument('--rpm',type=float,default=30.);p.add_argument('--press',type=float,default=50.)
a=p.parse_args()
from pathlib import Path
sys.path.insert(0,'/repo/src/roboenv_perception/tools')
from isaaclab.app import AppLauncher
app=AppLauncher(headless=True,device='cpu').app
import numpy as np
import torch
torch.set_num_threads(2)
import omni.usd
import omni.physics.tensors as tensors
import isaaclab.sim as sim_utils
from pxr import Gf,Sdf,Usd,UsdGeom,UsdPhysics,PhysxSchema
from native_screw_runtime import AssemblyRuntime
stage=omni.usd.get_context().get_stage()
if stage is None:omni.usd.get_context().new_stage();stage=omni.usd.get_context().get_stage()
UsdGeom.Xform.Define(stage,'/World');UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
case='pressed' if a.press else 'unpressed'
box=Usd.Stage.Open(a.asset)
for prim in box.GetDefaultPrim().GetChildren():
    Sdf.CopySpec(box.GetRootLayer(),prim.GetPath(),stage.GetRootLayer(),prim.GetPath())
# Filter virtual thread contact in this dynamic stage; lid/head contact remains.
for i in range(3):
    UsdPhysics.FilteredPairsAPI.Apply(stage.GetPrimAtPath(f'/World/screw_{i}')).CreateFilteredPairsRel().AddTarget('/World/body')
floor=UsdGeom.Cube.Define(stage,'/World/floor');floor.CreateSizeAttr(1);floor.AddTranslateOp().Set(Gf.Vec3d(0,0,-.025));floor.AddScaleOp().Set(Gf.Vec3f(2,2,.05));UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
source=Usd.Stage.Open(a.tool)
Sdf.CopySpec(source.GetRootLayer(),source.GetDefaultPrim().GetPath(),stage.GetRootLayer(),Sdf.Path('/World/WristTool'))
tool=stage.GetPrimAtPath('/World/WristTool')
spec=json.loads(stage.GetPrimAtPath('/World/screw_0').GetAttribute('envcreator:fastenerSpec').Get())
face=spec['origin_world_m'];rootpos=Gf.Vec3d(face[0],face[1],face[2]-.0015+.130)
xf=UsdGeom.Xformable(tool);xf.ClearXformOpOrder();xf.AddTranslateOp().Set(rootpos);xf.AddOrientOp().Set(Gf.Quatf(math.sqrt(.5),Gf.Vec3f(-math.sqrt(.5),0,0)))
# Ideal XYZ carriage, authored joints provide reaction loads through the tool.
# Housing remains dynamic; no runtime pose/velocity writes.
tool.RemoveAPI(UsdPhysics.ArticulationRootAPI)
ts=json.loads(tool.GetAttribute('envcreator:toolSpec').Get());ts['articulation_view_suffix']='/carriage_anchor';ts['motor']['max_command_rpm']=max(a.rpm,ts['motor']['max_command_rpm']);tool.GetAttribute('envcreator:toolSpec').Set(json.dumps(ts))
for label in ('anchor','slide_x','slide_y'):
    bodyprim=UsdGeom.Xform.Define(stage,'/World/WristTool/'+label)
    bodyprim.AddTranslateOp().Set(Gf.Vec3d(0))
    bodyprim.AddOrientOp().Set(Gf.Quatf(math.sqrt(.5),Gf.Vec3f(math.sqrt(.5),0,0)))
    UsdPhysics.RigidBodyAPI.Apply(bodyprim.GetPrim())
    massapi=UsdPhysics.MassAPI.Apply(bodyprim.GetPrim());massapi.CreateMassAttr(.02)
    massapi.CreateDiagonalInertiaAttr(Gf.Vec3f(1e-4))
    massapi.CreateCenterOfMassAttr(Gf.Vec3f(0))
fixed=UsdPhysics.FixedJoint.Define(stage,'/World/WristTool/carriage_anchor')
fixed.CreateBody1Rel().SetTargets(['/World/WristTool/anchor']);fixed.CreateLocalPos0Attr(Gf.Vec3f(*rootpos))
UsdPhysics.ArticulationRootAPI.Apply(fixed.GetPrim())
for label,axis,parent,child in [('rail_x','X','anchor','slide_x'),('rail_y','Y','slide_x','slide_y'),('rail_z','Z','slide_y','housing')]:
    j=UsdPhysics.PrismaticJoint.Define(stage,'/World/WristTool/'+label)
    j.CreateBody0Rel().SetTargets(['/World/WristTool/'+parent]);j.CreateBody1Rel().SetTargets(['/World/WristTool/'+child]);j.CreateAxisAttr(axis)
    if label=='rail_z':j.CreateLocalRot1Attr(Gf.Quatf(math.sqrt(.5),Gf.Vec3f(math.sqrt(.5),0,0)))
    d=UsdPhysics.DriveAPI.Apply(j.GetPrim(),'linear');d.CreateTypeAttr('force');d.CreateStiffnessAttr(1000.);d.CreateDampingAttr(40.);d.CreateMaxForceAttr(100.)
for i in range(3):
    for label in ('housing','spindle'):
        UsdPhysics.FilteredPairsAPI.Apply(stage.GetPrimAtPath('/World/WristTool/'+label)).CreateFilteredPairsRel().AddTarget(f'/World/screw_{i}')
# Smaller contact offsets are important at M4 scale. Keep resolved lid contacts.
for prim in stage.Traverse():
    if prim.HasAPI(UsdPhysics.CollisionAPI):
        api=PhysxSchema.PhysxCollisionAPI.Apply(prim);api.CreateContactOffsetAttr(.00005);api.CreateRestOffsetAttr(0.)
PhysxSchema.PhysxContactReportAPI.Apply(stage.GetPrimAtPath('/World/lid')).CreateThresholdAttr(0.)
for i in range(3):
 j=UsdPhysics.Joint.Define(stage,f'/World/screw_{i}_tool_coupling');j.CreateBody0Rel().SetTargets([f'/World/screw_{i}']);j.CreateBody1Rel().SetTargets(['/World/WristTool/spindle']);j.CreateExcludeFromArticulationAttr(True);j.CreateJointEnabledAttr(False)
 d=UsdPhysics.DriveAPI.Apply(j.GetPrim(),'rotX');d.CreateTypeAttr('force');d.CreateStiffnessAttr(50*math.pi/180);d.CreateDampingAttr(.02*math.pi/180);d.CreateMaxForceAttr(3.)
sim=sim_utils.SimulationContext(sim_utils.SimulationCfg(device='cpu',dt=.001,gravity=(0.,0.,-9.81),render_interval=100000,
    physx=sim_utils.PhysxCfg(solver_type=1,enable_external_forces_every_iteration=True)))
import carb;carb.settings.get_settings().set_bool('/physics/disableContactProcessing',False)
sim.reset();view=tensors.create_simulation_view('torch');view.set_subspace_roots('/')
class State:
    lock=threading.RLock();tool_command={'enabled':False,'rpm':0.,'release':False};tool_deadline=float('inf')
    tool_status={};events=[]
    @staticmethod
    def clock():return time.monotonic()*1000
    def event(self,name,**kw):
        if name!='tool-mechanics-sample':self.events.append({'event':name,**kw})
state=State();mechanics=AssemblyRuntime(stage,state,view,'/World/WristTool',[f'/World/screw_{i}' for i in range(3)])
lid=view.create_rigid_body_view('/World/lid');body=view.create_rigid_body_view('/World/body');contacts=view.create_rigid_contact_view('/World/lid',filter_patterns=['/World/body'])
art=mechanics.screws[0].tool
names=list(art.shared_metatype.dof_names)
railids=[names.index('rail_'+a) for a in 'xyz']
k=np.zeros((1,art.max_dofs),np.float32);d=k.copy();maxf=np.full_like(k,3.)
for i in railids:k[0,i]=5000.;d[0,i]=80.;maxf[0,i]=100.
art.set_dof_stiffnesses(torch.tensor(k),torch.tensor([0],dtype=torch.int32));art.set_dof_dampings(torch.tensor(d),torch.tensor([0],dtype=torch.int32));art.set_dof_max_forces(torch.tensor(maxf),torch.tensor([0],dtype=torch.int32))
original_physics_step=sim.step
def step_with_mechanics(*args,**kwargs):
    mechanics.before_step(sim.get_physics_dt())
    return original_physics_step(*args,**kwargs)
sim.step=step_with_mechanics
trace=[]
clear_step=None
clear_target=None
for step in range(round(a.seconds/.001)):
    if step==2000:state.tool_command={'enabled':True,'rpm':-a.rpm,'release':False}
    # native physics callback applies mechanics
    # Feedback carriage is an explicitly ideal bench actuator, not robot control.
    target=np.zeros((1,art.max_dofs),np.float32)
    screwpos=mechanics.screws[0].screw.get_transforms()[0,:3].cpu().numpy()
    delta=screwpos-np.array(face)
    target[0,railids]=delta
    # 1.1 mm extra approach relative to the initial 1.5 mm insertion reaches
    # the 2.5 mm socket bottom with 0.1 mm nominal compliance compression.
    target[0,railids[2]]+=.32*9.81/5000.
    target[0,railids[2]]-=.0012*min(1.,step/1000.)
    if not mechanics.screws[0].model.thread_active:
        if clear_step is None:
            clear_step=step
            clear_target=target[0,railids[2]]
        state.tool_command={'enabled':False,'rpm':0.,'release':False}
        target[0,railids[:2]]=0.
        target[0,railids[2]]=clear_target+min(.030,(step-clear_step)*.001*.003)
    art.set_dof_position_targets(torch.tensor(target),torch.tensor([0],dtype=torch.int32))
    press=a.press
    lid.apply_forces_and_torques_at_position(torch.tensor([[0.,0.,-press]]),torch.zeros((1,3)),None,torch.tensor([0],dtype=torch.int32),True)
    sim.step(render=False)
    if step%10==0:
        measured_joint_wrenches=art.get_link_incoming_joint_force()[0].cpu().numpy().tolist()
        pose=body.get_transforms()[0].cpu().numpy().tolist();vel=body.get_velocities()[0].cpu().numpy().tolist()
        trace.append({'native_joint_wrenches_child_frames':measured_joint_wrenches,'native_link_names':list(art.shared_metatype.link_names),'t':step*.001,'box_pose':pose,'box_velocity':vel,'tool':state.tool_status,'lid_box_contact_N':contacts.get_contact_force_matrix(.001).tolist()})
    if step%1000==0:
        print(case,step,flush=True)
        Path(a.output).with_suffix('.progress.json').write_text(json.dumps(trace[-1],indent=2))
peaks=max(abs(r['tool'].get('engagement',{}).get('transmitted_Nm',0.)) for r in trace)
report={'case':case,'press_N':a.press,'peak_constitutive_torque_estimate_Nm':peaks,
 'trace':trace,'events':state.events,'final_lid_pose':lid.get_transforms()[0].cpu().numpy().tolist(),'final_screw_poses':[m.screw.get_transforms()[0].cpu().numpy().tolist() for m in mechanics.screws],
 'scope':'actual exported tool + dynamic box/lid/screws; ideal fixed tool housing and prescribed bench press, no robot',
 'robot_execution':False,'model':'native mimic, detachable screw, reduced clamp preload; no resolved thread surfaces', 'rpm':a.rpm}
Path(a.output).write_text(json.dumps(report,indent=2));print('peak',peaks,flush=True)
sys.stdout.flush()
__import__('os')._exit(0)
