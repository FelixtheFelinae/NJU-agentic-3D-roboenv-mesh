"""G1 scripted contact attempt on the reviewed scene, using actual robot actuators."""

import argparse
import copy
import hashlib
import json
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(source, output):
    from isaaclab.app import AppLauncher

    launcher = AppLauncher(headless=True, enable_cameras=True, device='cpu',
                           kit_args=f'--portable-root {output.parent / "kit-runtime"}')
    app = launcher.app
    code = 2
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        import torch
        from isaaclab.actuators import ImplicitActuatorCfg
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
        from isaaclab.sensors import Camera, CameraCfg, ContactSensor, ContactSensorCfg
        from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg
        from PIL import Image, ImageDraw
        from pxr import PhysxSchema, Usd, UsdPhysics, UsdShade
        from scipy.spatial.transform import Rotation, Slerp

        from roboenv_perception.construction.robot_kinematics import RobotKinematics, standing_pose

        torch.set_num_threads(4)
        output.mkdir(parents=True, exist_ok=False)
        reference = source.parent.parent/'user-video-isaac-20261023/references'
        robot_input = output/'robot-input'
        shutil.copytree(reference, robot_input)
        urdf = robot_input/'g1_29dof.urdf'
        xml = ET.parse(urdf)
        # Official rubber hands have visuals/inertia but no collisions. Add matching
        # collision geometry to this derived copy, preserving the original source.
        for side in ['left', 'right']:
            link = xml.find(f'.//link[@name="{side}_rubber_hand"]')
            visual = link.find('visual')
            collision = ET.SubElement(link, 'collision')
            for key in ['origin', 'geometry']:
                collision.append(copy.deepcopy(visual.find(key)))
        xml.write(urdf)
        converter = UrdfConverter(UrdfConverterCfg(
            asset_path=str(urdf), usd_dir=str(output/'robot'), usd_file_name='g1.usd',
            fix_base=True, merge_fixed_joints=True, make_instanceable=False,
            collider_type='convex_hull', self_collision=False,
            joint_drive=UrdfConverterCfg.JointDriveCfg(
                target_type='position', gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=100, damping=5))))
        robot_stage = Usd.Stage.Open(converter.usd_path)
        rubber = UsdShade.Material.Define(robot_stage, str(robot_stage.GetDefaultPrim().GetPath())+'/Looks/RubberContact')
        material = UsdPhysics.MaterialAPI.Apply(rubber.GetPrim())
        material.CreateStaticFrictionAttr(1.2)
        material.CreateDynamicFrictionAttr(1.0)
        material.CreateRestitutionAttr(0)
        for prim in robot_stage.Traverse():
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                props = PhysxSchema.PhysxCollisionAPI.Apply(prim)
                props.CreateContactOffsetAttr(.001)
                props.CreateRestOffsetAttr(0)
                if 'wrist_yaw' in str(prim.GetPath()) or 'rubber_hand' in str(prim.GetPath()):
                    UsdShade.MaterialBindingAPI.Apply(prim).Bind(rubber, materialPurpose='physics')
        robot_stage.GetRootLayer().Save()
        if not omni.usd.get_context().open_stage(str(source/'scene.usda')):
            raise RuntimeError('Cannot open reviewed scene')
        dt = 1/240
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            device='cpu', dt=dt, render_interval=8,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True)))
        q_home = standing_pose()
        q_home['waist_pitch_joint'] = .38
        root_xyz = (.015, 0, .77792370309)
        robot = Articulation(ArticulationCfg(
            prim_path='/World/G1', spawn=sim_utils.UsdFileCfg(
                usd_path=converter.usd_path, activate_contact_sensors=True,
                rigid_props=sim_utils.RigidBodyPropertiesCfg(max_depenetration_velocity=1),
                articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                    enabled_self_collisions=False, solver_position_iteration_count=16, solver_velocity_iteration_count=4)),
            init_state=ArticulationCfg.InitialStateCfg(pos=root_xyz, joint_pos=q_home),
            actuators={
                'body': ImplicitActuatorCfg(joint_names_expr=['.*hip.*', '.*knee.*', '.*ankle.*', 'waist.*'],
                    stiffness=300, damping=12, effort_limit_sim=88),
                'arms': ImplicitActuatorCfg(joint_names_expr=['.*shoulder.*', '.*elbow.*', '.*wrist_roll.*'],
                    stiffness=120, damping=8, effort_limit_sim=25),
                'wrists': ImplicitActuatorCfg(joint_names_expr=['.*wrist_pitch.*', '.*wrist_yaw.*'],
                    stiffness=50, damping=4, effort_limit_sim=5),
            }))
        laptop = Articulation(ArticulationCfg(prim_path='/World/Laptop', spawn=None, actuators={}))
        mouse = RigidObject(RigidObjectCfg(prim_path='/World/Mouse', spawn=None))
        contacts = {side: ContactSensor(ContactSensorCfg(
            prim_path=f'/World/G1/.*{side}_wrist_yaw_link', update_period=0,
            filter_prim_paths_expr=['/World/Laptop/lid', '/World/Mouse/body'])) for side in ['left', 'right']}
        camera = Camera(CameraCfg(prim_path='/World/RobotCamera', width=1280, height=960,
            data_types=['rgb'], update_period=0, spawn=sim_utils.PinholeCameraCfg(
                focal_length=26, horizontal_aperture=32, clipping_range=(.03, 100))))
        eye, target = [1.55, 1.55, 1.65], [.28, 0, .79]
        sim.set_camera_view(eye, target)
        sim.reset()
        robot.write_joint_state_to_sim(robot.data.default_joint_pos.clone(), robot.data.default_joint_vel.clone())
        robot.set_joint_position_target(robot.data.default_joint_pos.clone())
        camera.set_world_poses_from_view(torch.tensor([eye]), torch.tensor([target]))
        for _ in range(240):
            robot.set_joint_effort_target(robot.root_physx_view.get_gravity_compensation_forces())
            robot.write_data_to_sim()
            sim.step(render=False)
            robot.update(dt)
            laptop.update(dt)
            mouse.update(dt)
        kinematics = RobotKinematics(urdf, root_xyz=root_xyz)
        (output/'robot-joint-names.json').write_text(json.dumps(robot.joint_names))
        (output/'robot-body-names.json').write_text(json.dumps(robot.body_names))
        print('ROBOT_ROOT', robot.data.root_state_w[0].cpu().numpy().tolist(), flush=True)
        q = {name: float(value) for name, value in zip(robot.joint_names, robot.data.joint_pos[0])}
        q.update(q_home)
        initial_laptop_angle = float(laptop.data.joint_pos[0, 0])
        baseline_mouse = mouse.data.root_pos_w[0].cpu().numpy().copy()
        base_pose = laptop.data.root_state_w[0].cpu().numpy().copy()
        rb = Rotation.from_quat(np.r_[base_pose[4:7], base_pose[3]]).as_matrix()
        tip = np.array([.17218, .0361, .00667])
        palm = np.array([.115, .02165, 0])
        left_orientation = Rotation.from_euler('x', -math.pi/2).as_matrix()
        frames, ik_log = [], []

        def add_segment(label, end_q, duration):
            start = q.copy()
            count = round(duration/dt)
            for step in range(count):
                alpha = (step+1)/count
                alpha = alpha*alpha*(3-2*alpha)
                frames.append((label, {n: start.get(n, 0)+(end_q.get(n, 0)-start.get(n, 0))*alpha for n in robot.joint_names}))
            q.update(end_q)

        def lid_goal(angle, penetration):
            r = rb@Rotation.from_euler('x', angle).as_matrix()
            normal = -r[:, 2]
            orientation = np.column_stack([normal, np.cross([1, 0, 0], normal), [1, 0, 0]])
            target = base_pose[:3]+rb@np.array([0, 0, .001])+r@np.array([-.125, .17, .0033])+normal*penetration
            return target, orientation

        def solve(side, target, orientation, tool, axis_only=False):
            solved, evidence = kinematics.solve_arm(side, np.array(target), orientation, tool, q,
                                                     max_nfev=140, axis_only=axis_only)
            ik_log.append({'side': side, 'target': np.asarray(target).tolist(), **evidence})
            return solved

        def cartesian_segment(label, side, target, orientation, tool, duration, axis_only=False):
            pose = kinematics.forward(q)[side+'_wrist_yaw_link']
            start = pose[:3, 3]+pose[:3, :3]@tool
            rotations = Slerp([0, 1], Rotation.from_matrix(np.stack([pose[:3, :3], orientation])))
            for alpha in np.linspace(0, 1, 9)[1:]:
                add_segment(label, solve(side, start+(np.array(target)-start)*alpha,
                            rotations(alpha).as_matrix(), tool, axis_only), duration/8)

        target, orientation = lid_goal(initial_laptop_angle, -.05)
        # Clear the side edge before raising; direct home-to-target interpolation
        # can trap the forearm underneath the tabletop.
        add_segment('RIGHT ARM MOVE OUTSIDE TABLE', solve('right', [.10, -.35, .75], np.eye(3), tip, True), 1.8)
        add_segment('RIGHT ARM LIFT OUTSIDE TABLE', solve('right', [.10, -.35, 1.10], np.eye(3), tip, True), 2)
        pre = target.copy()
        pre[0] -= .06
        pre[2] += .03
        add_segment('RIGHT ARM PREAPPROACH', solve('right', pre, orientation, tip, True), 2)
        add_segment('APPROACH DISPLAY BACK', solve('right', target, orientation, tip, True), 1.5)
        end_angle = math.radians(25)
        for angle in np.linspace(initial_laptop_angle, end_angle, 28):
            target, orientation = lid_goal(float(angle), .009)
            add_segment('PUSH DISPLAY CLOSED - PHYSICAL CONTACT', solve('right', target, orientation, tip, True), .25)
        target, orientation = lid_goal(end_angle, -.06)
        add_segment('RIGHT HAND RELEASE', solve('right', target, orientation, tip, True), 1.2)
        cartesian_segment('RIGHT HAND CLEAR OBJECTS', 'right', [.43, .10, 1.03], np.eye(3), tip, 1.5, True)
        cartesian_segment('RIGHT ARM CLEAR TABLE', 'right', [.10, -.28, 1.05], np.eye(3), tip, 2, True)
        add_segment('RIGHT ARM PARK CLEAR OF OBJECTS', q.copy(), 1)

        add_segment('LEFT ARM MOVE OUTSIDE TABLE', solve('left', [.08, .35, .75], left_orientation, palm), 1.8)
        add_segment('LEFT ARM LIFT OUTSIDE TABLE', solve('left', [.08, .35, 1.05], left_orientation, palm), 2)
        target = baseline_mouse+np.array([0, 0, .095])
        add_segment('LEFT HAND APPROACH MOUSE', solve('left', target, left_orientation, palm), 2)
        target[2] = baseline_mouse[2]+.04235-.012
        add_segment('PRESS MOUSE WITH RUBBER PALM', solve('left', target, left_orientation, palm), 1.3)
        for x in np.linspace(0, .05, 16)[1:]:
            goal = target+np.array([x, 0, 0])
            add_segment('DRAG MOUSE BY FRICTION - PHYSICAL CONTACT', solve('left', goal, left_orientation, palm), .18)
        target += [.05, 0, .08]
        add_segment('LEFT HAND RELEASE', solve('left', target, left_orientation, palm), 1.3)
        add_segment('SETTLE AND INSPECT', q.copy(), 1.5)
        (output/'ik-plan.json').write_text(json.dumps(ik_log, indent=2)+'\n')
        print('ROBOT_PLAN', len(frames), 'steps; IK max error', max(r['position_error_m'] for r in ik_log), flush=True)
        video = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '1280x960', '-r', '30', '-i', 'pipe:0',
            '-an', '-c:v', 'h264_nvenc', '-cq', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            str(output/'g1-interaction.mp4')], stdin=subprocess.PIPE)
        rows = []
        try:
            for step, (phase, command) in enumerate(frames):
                joint_target = torch.tensor([[command[n] for n in robot.joint_names]], dtype=torch.float32)
                robot.set_joint_position_target(joint_target)
                robot.set_joint_effort_target(robot.root_physx_view.get_gravity_compensation_forces())
                robot.write_data_to_sim()
                sim.step(render=step % 8 == 0)
                robot.update(dt)
                laptop.update(dt)
                mouse.update(dt)
                forces = {}
                for side, sensor in contacts.items():
                    sensor.update(dt)
                    forces[side] = sensor.data.force_matrix_w[0, 0].cpu().numpy().tolist()
                row = {'t': (step+1)*dt, 'phase': phase, 'hinge_deg': math.degrees(float(laptop.data.joint_pos[0, 0])),
                       'mouse_position': mouse.data.root_pos_w[0].cpu().numpy().tolist(),
                       'laptop_root_state': laptop.data.root_state_w[0].cpu().numpy().tolist(),
                       'robot_joint_positions': robot.data.joint_pos[0].cpu().numpy().tolist(),
                       'joint_targets': joint_target[0].cpu().numpy().tolist(),
                       'filtered_hand_forces_N': forces}
                tool_positions = {}
                for side, offset in [('right', tip), ('left', palm)]:
                    body_id = robot.body_names.index(side+'_wrist_yaw_link')
                    body = robot.data.body_state_w[0, body_id].cpu().numpy()
                    rotation = Rotation.from_quat(np.r_[body[4:7], body[3]]).as_matrix()
                    tool_positions[side] = (body[:3]+rotation@offset).tolist()
                row['tool_positions_w'] = tool_positions
                rows.append(row)
                if step % 8 == 0:
                    camera.update(dt*8, force_recompute=True)
                    image = Image.fromarray(camera.data.output['rgb'][0, ..., :3].cpu().numpy().astype(np.uint8))
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((0, 0, 1280, 66), fill=(24, 27, 34))
                    draw.text((14, 12), f'Unitree G1 | FIXED PELVIS | {phase}', fill='white')
                    draw.text((14, 37), f't={step*dt:.2f}s | lid={row["hinge_deg"]:.1f}deg | mouse displacement={np.linalg.norm(np.array(row["mouse_position"])[:2]-baseline_mouse[:2])*1000:.1f}mm', fill='white')
                    video.stdin.write(image.tobytes())
                    if step % 480 == 0:
                        image.save(output/f'frame-{step:05}.png')
                if step % 480 == 0:
                    print('ROBOT_STEP', step, phase, row['hinge_deg'], row['mouse_position'], flush=True)
                    expected = kinematics.forward(command)
                    print('ROBOT_TRACKING', tool_positions,
                          'right_expected', (expected['right_wrist_yaw_link'][:3, 3]+expected['right_wrist_yaw_link'][:3, :3]@tip).tolist(),
                          'joint_error', float(torch.max(abs(robot.data.joint_pos-joint_target))), flush=True)
        finally:
            video.stdin.close()
            if video.wait(timeout=30):
                raise RuntimeError('Video encoder failed')
        final_angle = rows[-1]['hinge_deg']
        displacement = float(np.linalg.norm(np.array(rows[-1]['mouse_position'])[:2]-baseline_mouse[:2]))
        drag_rows = [r for r in rows if r['phase'].startswith('DRAG MOUSE')]
        drag_displacement = float(np.linalg.norm(np.array(drag_rows[-1]['mouse_position'])[:2]-np.array(drag_rows[0]['mouse_position'])[:2]))
        drag_contact_duration = sum(np.linalg.norm(r['filtered_hand_forces_N']['left'][1]) > .3 for r in drag_rows)*dt
        right_contact = max(np.linalg.norm(r['filtered_hand_forces_N']['right'][0]) for r in rows)
        left_contact = max(np.linalg.norm(r['filtered_hand_forces_N']['left'][1]) for r in rows)
        checks = {'laptop_closes_below_5deg': final_angle < 5,
                  'right_hand_contacts_lid_over_0p5N': right_contact > .5,
                  'mouse_moves_over_20mm_during_drag': drag_displacement > .02,
                  'left_hand_contacts_mouse_over_0p3N': left_contact > .3,
                  'mouse_drag_contact_over_0p2s': drag_contact_duration > .2,
                  'mouse_remains_on_table': all(abs(r['mouse_position'][2]-.75) < .005 for r in rows),
                  'all_recorded_states_finite': all(np.isfinite(r['robot_joint_positions']).all() and math.isfinite(r['hinge_deg']) and np.isfinite(r['mouse_position']).all() for r in rows)}
        checks = {k: bool(v) for k, v in checks.items()}
        result = {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
                  'initial_lid_deg': math.degrees(initial_laptop_angle), 'final_lid_deg': final_angle,
                  'mouse_displacement_m': displacement, 'right_hand_lid_peak_force_N': float(right_contact),
                  'mouse_drag_displacement_m': drag_displacement, 'mouse_drag_contact_duration_s': drag_contact_duration,
                  'left_hand_mouse_peak_force_N': float(left_contact), 'fixed_pelvis': True,
                  'object_direct_controls': False, 'robot_control': 'URDF IK + joint PD + gravity compensation',
                  'robot_self_collision': False, 'rubber_friction_assumed': [1.2, 1.0],
                  'robot_urdf_sha256': digest(urdf), 'source_scene_sha256': digest(source/'scene.usda'),
                  'scope': 'Scripted fixed-base contact demo; no balance policy or learned autonomous planner.'}
        (output/'trajectory.json').write_text(json.dumps(rows)+'\n')
        (output/'validation.json').write_text(json.dumps(result, indent=2)+'\n')
        print('ROBOT_RESULT', json.dumps(result), flush=True)
        code = 0 if result['status'] == 'passed' else 2
    except BaseException:
        import traceback
        traceback.print_exc()
        output.mkdir(parents=True, exist_ok=True)
        (output/'failure.json').write_text(json.dumps({'traceback': traceback.format_exc()}, indent=2))
    finally:
        sys.stdout.flush()
        app.close(wait_for_replicator=False)
    return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.input.resolve(), args.output.resolve()))
