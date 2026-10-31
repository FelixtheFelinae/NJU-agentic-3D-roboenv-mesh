"""Physical load validation, separate from the robot agent and ideal tool bench.

Run inside the pinned Arena image. External fixture/loads are explicit test
conditions. Does not command a robot, teleport parts, or supply success states.
"""
import argparse,json,math,sys,time,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--asset',required=True);p.add_argument('--output',required=True);p.add_argument('--seconds',type=float,default=30);p.add_argument('--dt',type=float,default=.001);p.add_argument('--case',choices=['idle','perturb','pull','torque','release','breakaway'],default='idle');a=p.parse_args()
from isaaclab.app import AppLauncher
app=AppLauncher(headless=True,device='cpu').app
import numpy as np,torch,omni.usd,omni.physics.tensors as tensors
import isaaclab.sim as su
from pxr import Usd,UsdGeom,UsdPhysics,PhysxSchema,Sdf,Gf
sys.path.insert(0,'/repo/src/roboenv_perception/tools')
from solver_thread import SolverThread
from fastener import Parameters,ScrewModel
from screwdriving import rotate
from native_screw_runtime import multiply_q,support_reaction
torch.set_num_threads(2);stage=omni.usd.get_context().get_stage();UsdGeom.Xform.Define(stage,'/World');UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1)
source=Usd.Stage.Open(a.asset)
for prim in source.GetDefaultPrim().GetChildren():Sdf.CopySpec(source.GetRootLayer(),prim.GetPath(),stage.GetRootLayer(),prim.GetPath())
floor=UsdGeom.Cube.Define(stage,'/World/floor');floor.CreateSizeAttr(1);floor.AddTranslateOp().Set(Gf.Vec3d(0,0,-.025));floor.AddScaleOp().Set(Gf.Vec3f(2,2,.05));UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
PhysxSchema.PhysxContactReportAPI.Apply(stage.GetPrimAtPath('/World/lid')).CreateThresholdAttr(0.)
if a.case in ('torque','pull','release','breakaway'):
 j=UsdPhysics.FixedJoint.Define(stage,'/World/bench_fixture');j.CreateBody1Rel().SetTargets(['/World/body'])
 if stage.GetPrimAtPath('/World/body').HasAPI(UsdPhysics.ArticulationRootAPI):
  UsdPhysics.ArticulationRootAPI.Apply(j.GetPrim());stage.GetPrimAtPath('/World/body').RemoveAPI(UsdPhysics.ArticulationRootAPI)
sim=su.SimulationContext(su.SimulationCfg(device='cpu',dt=a.dt,render_interval=100000,physx=su.PhysxCfg(solver_type=1,enable_external_forces_every_iteration=True)))
import carb;carb.settings.get_settings().set_bool('/physics/disableContactProcessing',False);sim.reset()
v=tensors.create_simulation_view('torch');v.set_subspace_roots('/');body=v.create_rigid_body_view('/World/body');lid=v.create_rigid_body_view('/World/lid');screws=[v.create_rigid_body_view(f'/World/screw_{i}') for i in range(3)];contacts=v.create_rigid_contact_view('/World/lid',filter_patterns=['/World/body','/World/screw_0','/World/screw_1','/World/screw_2'])
models=[];mechanics=[];origins=[]
for i in range(3):
 spec=json.loads(stage.GetPrimAtPath(f'/World/screw_{i}').GetAttribute('envcreator:fastenerSpec').Get());models.append(ScrewModel(Parameters(**spec['thread_parameters'])));mechanics.append(SolverThread(stage,spec,'/World'));origins.append(np.array(spec['origin_world_m']))
idx=torch.tensor([0],dtype=torch.int32);trace=[];peak_res=0.;baseline=None;breakaway=None

def pose(view):
 t=view.get_transforms()[0].numpy();return t[:3],np.array([t[6],*t[3:6]])
for step in range(round(a.seconds/a.dt)):
 t=step*a.dt;bp,bq=pose(body);iq=bq*np.array([1,-1,-1,-1]);bc=bp+rotate(bq,body.get_coms()[0,:3].numpy());normal=rotate(bq,[0,0,1]);rf=np.zeros(3);rt=np.zeros(3);lf=np.zeros(3);lt=np.zeros(3);rows=[];lp,lq=pose(lid);lc=lp+rotate(lq,lid.get_coms()[0,:3].numpy())
 for i,screw in enumerate(screws):
  sp,sq=pose(screw);sc=sp+rotate(sq,screw.get_coms()[0,:3].numpy());z=float((rotate(iq,sp-bp)-origins[i])[2]);models[i].observe(multiply_q(iq,sq),z);
  if a.case=='release' and t>=2 and i==0:models[i].thread_active=False
  m=mechanics[i].step(models[i],z,a.dt);load=m['preload_N'];f=np.zeros(3);tau=normal*load*models[i].p.lead_m/math.tau;pull=0.;pf=-normal*load;lf+=pf;lt+=np.cross(sp-lc,pf);rf-=pf;rt+=np.cross(sp-bc,-pf)-tau
  if a.case=='perturb' and t>2 and i==2:tau-=normal*.0006
  if a.case=='pull' and t>2 and i==0:pull=20.;f+=normal*pull
  if a.case=='torque' and t>2 and i==0:tau+=normal*min(.3,(t-2)*.1)
  if a.case=='breakaway' and t>2 and i==0:
   if models[i].theta>.01 and breakaway is None:breakaway={'time_s':t,'previous_applied_torque_Nm':min(2.,max(0.,t-a.dt-2)), 'theta_rad':models[i].theta}
   if breakaway is None:tau+=normal*min(2.,t-2)
  if a.case=='release' and t>2 and i==0:
   f+=normal*.06
   if t>2.6:f+=np.array([.01,0,0]);tau=np.array([1e-6,0,0])
  screw.apply_forces_and_torques_at_position(torch.tensor([f],dtype=torch.float32),torch.tensor([tau],dtype=torch.float32),None,idx,True)

  rows.append({'pose':screw.get_transforms()[0].tolist(),'theta_rad':models[i].theta,'exit_m':z,'thread_active':models[i].thread_active,**m})
 body.apply_forces_and_torques_at_position(torch.tensor([rf],dtype=torch.float32),torch.tensor([rt],dtype=torch.float32),None,idx,True)
 if a.case=='perturb' and t>2:
  down=30. if (int(t)//3)%2 else 10.;lf+=np.array([2.*math.sin(t),0.,-down]);lt+=np.array([.1*math.sin(t),0.,0.])
 lid.apply_forces_and_torques_at_position(torch.tensor([lf],dtype=torch.float32),torch.tensor([lt],dtype=torch.float32),None,idx,True)
 sim.step(render=False)
 if step%100==0:
  trace.append({'t':t,'screws':rows,'lid_contact_normal_N':contacts.get_contact_force_matrix(a.dt).tolist(),'body_pose':body.get_transforms()[0].tolist(),'lid_pose':lid.get_transforms()[0].tolist(),'body_velocity':body.get_velocities()[0].tolist()})
 if step%5000==0:print(a.case,t,flush=True)
report={'breakaway':breakaway,'case':a.case,'dt':a.dt,'seconds':a.seconds,'robot':False,'fixed_body':a.case in ('torque','pull','release','breakaway'),'trace':trace,'scope':'declared external wrench bench; no robot demonstration'}
out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2));print('saved',a.output,flush=True);os._exit(0)
