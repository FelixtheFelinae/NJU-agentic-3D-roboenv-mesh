import json,math,hashlib
from pathlib import Path
root=Path('outputs/triangular-box-20261030-v9')
checks=[]
def check(name,passed,**evidence):checks.append(dict(name=name,passed=bool(passed),**evidence))
for name in ['elastic-long','elastic-halfstep','elastic-pull','elastic-torque']:
 r=json.loads((root/'validation'/f'{name}.json').read_text());rows=[x for x in r['trace'] if x['t']>=2]
 angles=[[x['screws'][i]['theta_rad'] for x in rows] for i in range(3)]
 residual=max(abs(s['coupling_residual_m']) for x in rows for s in x['screws'])
 check(name,all(s['thread_active'] for x in rows for s in x['screws']) and residual<.00005 and max(max(a)-min(a) for a in angles)<.002,
       dt_s=r['dt'],duration_s=r['seconds'],max_helix_residual_m=residual,max_angle_range_rad=max(max(a)-min(a) for a in angles),last_lid_body_contact_N=rows[-1]['lid_contact_normal_N'][0][0][2])
for name in ['elastic-breakaway','elastic-release']:
 path=root/'validation'/f'{name}.json'
 if not path.exists():continue
 r=json.loads(path.read_text())
 if name=='elastic-breakaway':
  b=r['breakaway'];check(name,b is not None and 1.2<b['previous_applied_torque_Nm']<1.8,measured_onset=b)
 else:
  x=r['trace'][-1];s=x['screws'][0];start=r['trace'][0]['screws'][0]['pose'];p=s['pose'];check(name,not s['thread_active'] and p[2]-start[2]>.1 and abs(p[0]-start[0])>.01 and abs(p[3])>.001,scope='explicitly forced connector release tests free-body topology, not unscrewing success',final_pose=p)
path=root/'validation/final-native-tool.json'
if path.exists():
 r=json.loads(path.read_text());rows=r['trace'];last=rows[-1]['tool'];s=last['fasteners']['/World/screw_0']
 log=path.with_suffix('.log').read_text();check('final-native-log',not any(t in log for t in ['[Error]','Traceback','limit invalid']),scope='no engine errors in final full-tool run')
 residual=max(abs(s['thread_metrics']['coupling_residual_m']) for x in rows for s in x['tool']['fasteners'].values() if s.get('thread_active',False))
 untouched=[last['fasteners'][f'/World/screw_{i}'] for i in (1,2)]
 check('full-tool-removal',s['thread_state']=='removed' and s['screw_exit_m']>=.024 and all(t['thread_active'] and abs(t['screw_rotation_rad'])<.002 for t in untouched) and abs(last['motor']['measured_rpm'])<1. and residual<.00005,scope='ideal driven carriage + 50N lid load; not robot execution',final_exit_m=s['screw_exit_m'],final_motor_rpm=last['motor']['measured_rpm'],max_helix_residual_m=residual,events=r['events'],untouched_angles_rad=[t['screw_rotation_rad'] for t in untouched])
# Acceptance uses actual native motor/shaft and housing reactions, not the
# engagement model's constitutive torque estimate.
if path.exists():
 names=rows[-1]['native_link_names'];sp=names.index('spindle');ho=names.index('housing')
 peak_sp=max(abs(x['native_joint_wrenches_child_frames'][sp][5]) for x in rows)
 peak_ho=max(abs(x['native_joint_wrenches_child_frames'][ho][5]) for x in rows)
 axial=max(abs(x['native_joint_wrenches_child_frames'][sp][2]) for x in rows if 3<x['t']<6)
 check('native-reaction-path',peak_sp>.5 and abs(peak_sp-peak_ho)<.05 and axial>.1,
       peak_spindle_torque_Nm=peak_sp,peak_housing_torque_Nm=peak_ho,
       axial_shaft_load_during_threading_N=axial,
       scope='joint child-frame measured reactions; wrist/robot not instantiated')
report={'status':'passed' if len(checks)==9 and all(x['passed'] for x in checks) else 'incomplete-or-failed','checks':checks,'asset_sha256':hashlib.sha256((root/'candidate-elastic-friction/triangular_box.usda').read_bytes()).hexdigest(),'limits':['Rigid bodies; no resolved elastic stress or thread surfaces','Friction and relaxation not calibrated on hardware','Two numerical coordinate carriers of 1g each per screw; connected inertia regularized','Native tool torque estimate is not a measured coupling reaction','G1 combined robot test pending']}
(root/'validation/acceptance.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
