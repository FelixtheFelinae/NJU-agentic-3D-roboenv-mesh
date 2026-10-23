"""Import Artisan candidates, adapt to PhysX 5.1, validate the actual final USD."""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def adapt_asset(source, destination, spec):
    """Preserve SDK geometry/inertia, flatten nested rigid links, add native passive friction."""
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    shutil.copytree(source.parent, destination.parent, dirs_exist_ok=True)
    stage = Usd.Stage.Open(str(destination))
    root = stage.GetDefaultPrim()
    if spec['kind'] == 'rigid':
        root.RemoveAPI(UsdPhysics.ArticulationRootAPI)
    else:
        lid = next(p for p in stage.Traverse() if p.GetName() == 'lid' and p.HasAPI(UsdPhysics.RigidBodyAPI))
        old = str(lid.GetPath())
        new = str(root.GetPath())+'/lid'
        if old != new:
            Sdf.CopySpec(stage.GetRootLayer(), old, stage.GetRootLayer(), new)
            stage.RemovePrim(old)
            for p in stage.Traverse():
                for rel in p.GetRelationships():
                    targets = rel.GetTargets()
                    if targets:
                        rel.SetTargets([Sdf.Path(str(t).replace(old, new)) for t in targets])
        joint = next(p for p in stage.Traverse() if p.IsA(UsdPhysics.RevoluteJoint))
        # Native PhysX 5.1 joint-axis friction. USD angular rates use degrees/s,
        # so convert the intended .03 Nm*s/rad viscous coefficient at the boundary.
        joint.AddAppliedSchema('PhysxJointAxisAPI:angular')
        for name, value in [('staticFrictionEffort', .55), ('dynamicFrictionEffort', .42),
                            ('viscousFrictionCoefficient', .03*math.pi/180)]:
            joint.CreateAttribute('physxJointAxis:angular:'+name, Sdf.ValueTypeNames.Float).Set(value)
        joint.GetAttribute('physxJoint:jointFriction').Set(0)
        drive = UsdPhysics.DriveAPI.Get(joint, 'angular')
        if drive:
            drive.CreateStiffnessAttr(0)
            drive.CreateDampingAttr(0)
            drive.CreateMaxForceAttr(0)
        joint.CreateAttribute('roboenv:frictionSource', Sdf.ValueTypeNames.String).Set(
            'Assumed passive Coulomb hinge, static=.55 Nm dynamic=.42 Nm viscous=.03 Nm*s/rad; no position servo')
    material = UsdShade.Material.Define(stage, str(root.GetPath())+'/Looks/Contact')
    api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    static, dynamic = (.35, .25) if spec['category'] == 'mouse' else (.6, .5)
    api.CreateStaticFrictionAttr(static)
    api.CreateDynamicFrictionAttr(dynamic)
    api.CreateRestitutionAttr(0)
    from pxr import PhysxSchema
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            # Artisan's generic USD exporter leaves collision shapes visible and
            # marks authored visuals as guides. Isaac should render the real mesh.
            UsdGeom.Imageable(prim).MakeInvisible()
            collision = PhysxSchema.PhysxCollisionAPI.Apply(prim)
            collision.CreateContactOffsetAttr(.001)
            collision.CreateRestOffsetAttr(0)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, materialPurpose='physics')
        elif prim.GetName().startswith('vis_'):
            UsdGeom.Imageable(prim).CreatePurposeAttr(UsdGeom.Tokens.default_)
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            body = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            body.CreateSolverPositionIterationCountAttr(16)
            body.CreateSolverVelocityIterationCountAttr(4)
            body.CreateMaxDepenetrationVelocityAttr(1)
            body.CreateLinearDampingAttr(.02)
            body.CreateAngularDampingAttr(.05)
    from roboenv_perception.construction.asset_appearance import apply_appearance
    apply_appearance(stage, root, source.parent.parent, destination.parent, spec)
    stage.GetRootLayer().Save()
    return str(root.GetPath())


def author_stand(stage, spec, position, quaternion):
    from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics, UsdShade

    stand = UsdGeom.Xform.Define(stage, '/World/LaptopStand')
    stand.AddTranslateOp().Set(Gf.Vec3d(*position))
    stand.AddOrientOp().Set(Gf.Quatf(quaternion[0], Gf.Vec3f(*quaternion[1:])))
    material = UsdShade.Material.Define(stage, '/World/LaptopStand/Contact')
    contact = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    contact.CreateStaticFrictionAttr(.9)
    contact.CreateDynamicFrictionAttr(.7)
    contact.CreateRestitutionAttr(0)

    def box(name, size, xyz):
        cube = UsdGeom.Cube.Define(stage, '/World/LaptopStand/'+name)
        cube.CreateSizeAttr(1)
        cube.AddTranslateOp().Set(Gf.Vec3d(*xyz))
        cube.AddScaleOp().Set(Gf.Vec3f(*size))
        cube.CreateDisplayColorAttr([Gf.Vec3f(.65, .67, .66)])
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
        props = PhysxSchema.PhysxCollisionAPI.Apply(cube.GetPrim())
        props.CreateContactOffsetAttr(.001)
        props.CreateRestOffsetAttr(0)
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material, materialPurpose='physics')

    depth = spec['depth_m']
    # Observed two-rail support, hidden bracing simplified. Top faces match base underside.
    for i, x in enumerate([-.095, .095]):
        box(f'Rail{i}', [.012, depth*.97, .006], [x, depth/2, -spec['base_thickness_m']-.003])
        # The lower legs are authored in world coordinates to meet the tabletop.
        tilt = spec['scene_base_tilt_rad']
        for j, y in enumerate([.025, depth-.025]):
            z_world = position[2]+math.sin(tilt)*y-math.cos(tilt)*(spec['base_thickness_m']+.006)
            height = max(.002, z_world-.75)
            # Vertical struts transformed back into the stand's tilted frame.
            leg = UsdGeom.Cube.Define(stage, f'/World/LaptopStandLeg{i}_{j}')
            leg.CreateSizeAttr(1)
            yaw = spec['pose_provisional']['yaw']
            dx, dy = x, math.cos(tilt)*y+math.sin(tilt)*(spec['base_thickness_m']+.006)
            leg.AddTranslateOp().Set(Gf.Vec3d(position[0]+math.cos(yaw)*dx-math.sin(yaw)*dy,
                                            position[1]+math.sin(yaw)*dx+math.cos(yaw)*dy,
                                            .75+height/2))
            leg.AddScaleOp().Set(Gf.Vec3f(.009, .009, height))
            leg.CreateDisplayColorAttr([Gf.Vec3f(.58, .60, .59)])
            UsdPhysics.CollisionAPI.Apply(leg.GetPrim())


def run(root, output):
    from isaaclab.app import AppLauncher

    launcher = AppLauncher(headless=True, enable_cameras=True, device='cpu',
                           kit_args=f'--portable-root {output.parent / "kit-runtime"}')
    app = launcher.app
    exit_code = 2
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
        from isaaclab.sensors import Camera, CameraCfg, ContactSensor, ContactSensorCfg
        from PIL import Image, ImageDraw
        from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics
        from scipy.spatial.transform import Rotation

        from roboenv_perception.construction.isaac_table_worker import export_asset
        from roboenv_perception.construction.simulation import (
            TableSimulationSpec,
            tabletop_geometry,
        )

        torch.set_num_threads(4)
        output.mkdir(parents=True, exist_ok=False)
        scene_spec = json.loads((root/'scene-build-spec.json').read_text())
        table_plane = json.loads((root.parent/'user-video-table-20261023/table-plane.json').read_text())
        table_spec = TableSimulationSpec(width_m=scene_spec['table_width_m'], width_source='estimated',
                                         width_evidence='14-inch MBP catalog width + two-view physical corner fit')
        geometry = tabletop_geometry(table_plane, table_spec)
        (output/'asset').mkdir()
        shutil.copyfile(root.parent/'user-video-table-20261023/assets/simple/table.png', output/'asset/table.png')
        table_path = export_asset(output, geometry, table_spec.model_dump())
        specs, files = {}, {}
        for asset, name in [('asset-0001', 'Bottle'), ('asset-0002', 'Laptop'), ('asset-0003', 'Mouse')]:
            work = root/'objects'/asset
            specs[name] = json.loads((work/'build-spec.json').read_text())
            files[name] = output/f'assets/{name}/object.usd'
            adapt_asset(work/'export/object.usd', files[name], specs[name])

        dt = 1/240
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            device='cpu', dt=dt, render_interval=8, enable_scene_query_support=True,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True)))
        table = sim_utils.UsdFileCfg(usd_path=str(table_path))
        table.func('/World/Table', table, translation=tuple(geometry['position_world_m']))
        floor = sim_utils.CuboidCfg(size=(3, 3, .05),
            collision_props=sim_utils.CollisionPropertiesCfg(collision_enabled=True),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(.17, .18, .20)))
        floor.func('/World/Floor', floor, translation=(.5, 0, -.025))
        light = sim_utils.DomeLightCfg(intensity=2000)
        light.func('/World/Light', light)

        factor = scene_spec['scale_fit']['uniform_scale_correction']
        objects, initial = {}, {}
        for name in ['Bottle', 'Laptop', 'Mouse']:
            spec = specs[name]
            pose = spec['pose_provisional']
            p = pose['position']
            xyz = [.2+(p[0]-.2)*factor, p[1]*factor, .75+(p[2]-.75)*factor]
            tilt = spec.get('scene_base_tilt_rad', 0)
            xyzw = Rotation.from_euler('zx', [pose['yaw'], tilt]).as_quat()
            # Intrinsic yaw then local tilt: Rz(yaw) Rx(tilt).
            xyzw = (Rotation.from_euler('z', pose['yaw'])*Rotation.from_euler('x', tilt)).as_quat()
            quat = [float(xyzw[3]), *map(float, xyzw[:3])]
            initial[name] = {'position': xyz, 'quaternion': quat}
            if name == 'Laptop':
                author_stand(sim.stage, spec, xyz, quat)
                xyz = [*xyz[:2], xyz[2]+.008]
                obj = Articulation(ArticulationCfg(
                    prim_path='/World/Laptop', spawn=sim_utils.UsdFileCfg(usd_path=str(files[name]), activate_contact_sensors=True),
                    init_state=ArticulationCfg.InitialStateCfg(pos=tuple(xyz), rot=tuple(quat),
                        joint_pos={'.*lid_hinge': spec['scene_opening_rad']}), actuators={}))
            else:
                xyz = [*xyz[:2], xyz[2]+.02]
                obj = RigidObject(RigidObjectCfg(
                    prim_path='/World/'+name, spawn=sim_utils.UsdFileCfg(usd_path=str(files[name]), activate_contact_sensors=True),
                    init_state=RigidObjectCfg.InitialStateCfg(pos=tuple(xyz), rot=tuple(quat))))
            objects[name] = obj
        sensors = {name: ContactSensor(ContactSensorCfg(prim_path=f'/World/{name}/{"base" if name == "Laptop" else "body"}', update_period=0))
                   for name in objects}
        eye, target = [1.25, 1.15, 1.55], [.55, 0, .80]
        camera = Camera(CameraCfg(prim_path='/World/Overview', width=960, height=720,
            data_types=['rgb'], update_period=0, spawn=sim_utils.PinholeCameraCfg(
                focal_length=30, horizontal_aperture=28, clipping_range=(.05, 100))))
        sim.set_camera_view(eye, target)
        sim.reset()
        for obj in objects.values():
            obj.reset()
        laptop = objects['Laptop']
        laptop.write_joint_state_to_sim(laptop.data.default_joint_pos.clone(), laptop.data.default_joint_vel.clone())
        camera.set_world_poses_from_view(torch.tensor([eye]), torch.tensor([target]))
        for _ in range(8):
            sim.render()

        # Author the portable playable scene from the same adapted USD files.
        portable = sim.stage.GetRootLayer().ExportToString()
        for name, path in files.items():
            portable = portable.replace(str(path), f'./assets/{name}/object.usd')
        portable = portable.replace(str(table_path), './asset/table.usda')
        (output/'scene.usda').write_text(portable)
        exported = Usd.Stage.Open(str(output/'scene.usda'))
        # Save an open initial lid pose as well as the native joint state.
        lid = next(p for p in exported.Traverse() if p.GetName() == 'lid' and p.HasAPI(UsdPhysics.RigidBodyAPI))
        xform = UsdGeom.Xformable(lid)
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeOrient:
                q = specs['Laptop']['scene_opening_rad']
                op.Set(Gf.Quatf(math.cos(q/2), Gf.Vec3f(math.sin(q/2), 0, 0)))
                break
        else:
            q = specs['Laptop']['scene_opening_rad']
            xform.AddOrientOp().Set(Gf.Quatf(math.cos(q/2), Gf.Vec3f(math.sin(q/2), 0, 0)))
        joint_prim = next(p for p in exported.Traverse() if p.IsA(UsdPhysics.RevoluteJoint))
        state_api = PhysxSchema.JointStateAPI.Apply(joint_prim, 'angular')
        state_api.CreatePositionAttr(math.degrees(specs['Laptop']['scene_opening_rad']))
        state_api.CreateVelocityAttr(0)
        exported.GetRootLayer().Save()

        video = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '960x720', '-r', '30', '-i', 'pipe:0',
            '-an', '-c:v', 'h264_nvenc', '-cq', '21', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            str(output/'interaction-preview.mp4')], stdin=subprocess.PIPE)
        rows = []
        try:
            for step in range(1440):
                t = step*dt
                phase = 'DROP / CONTACT / PASSIVE HOLD'
                for name, obj in objects.items():
                    forces = torch.zeros((1, obj.num_bodies, 3), dtype=torch.float32)
                    torques = torch.zeros_like(forces)
                    if name == 'Mouse' and 2 <= t < 2.10:
                        forces[0, 0, 0] = .141*9.81*.8
                        phase = 'PUSH ORIGINAL MOUSE'
                    if name == 'Bottle' and 2.6 <= t < 2.75:
                        forces[0, 0, 1] = .45
                        torques[0, 0, 0] = -.035
                        phase = 'TIP ORIGINAL BOTTLE'
                    if name == 'Mouse' and 4.8 <= t < 4.95:
                        forces[0, 0, 2] = .141*9.81*2
                        phase = 'LIFT ORIGINAL MOUSE / RELEASE'
                    if name == 'Laptop':
                        if 3.3 <= t < 3.55:
                            # Apply a real equal/opposite torque couple to the two links.
                            # Passive articulations have no actuator model to forward effort targets.
                            axis = torch.tensor([math.cos(specs[name]['pose_provisional']['yaw']),
                                                 math.sin(specs[name]['pose_provisional']['yaw']), 0])
                            torques[0, obj.body_names.index('lid')] = -.9*axis
                            torques[0, obj.body_names.index('base')] = .9*axis
                            phase = 'EXTERNAL TORQUE / PASSIVE HINGE'
                    obj.set_external_force_and_torque(forces, torques, is_global=True)
                    obj.write_data_to_sim()
                render = step % 8 == 0
                sim.step(render=render)
                for obj in objects.values():
                    obj.update(dt)
                for sensor in sensors.values():
                    sensor.update(dt)
                states = {n: o.data.root_state_w[0].cpu().numpy().tolist() for n, o in objects.items()}
                q = float(objects['Laptop'].data.joint_pos[0, 0])
                fz = {n: float(s.data.net_forces_w[0, :, 2].sum()) for n, s in sensors.items()}
                rows.append({'t': t+dt, 'states': states, 'hinge_rad': q, 'contact_fz_N': fz})
                if render:
                    camera.update(dt*8, force_recompute=True)
                    rgb = camera.data.output['rgb'][0, ..., :3].cpu().numpy().astype(np.uint8)
                    image = Image.fromarray(rgb)
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((0, 0, 960, 34), fill=(25, 28, 35))
                    draw.text((12, 10), f'Isaac Lab | {phase} | t={t:.2f}s | no robot / no test cubes', fill='white')
                    video.stdin.write(image.tobytes())
                    if step == 432:
                        image.save(output/'frame-initial.png')
                        for name in ['Mouse', 'Bottle', 'Laptop']:
                            p = np.array(states[name][:3])
                            middle = p+np.array([0, .07, .08]) if name == 'Laptop' else p+np.array([0, 0, .07 if name == 'Bottle' else .02])
                            offset = [.46, .46, .34] if name == 'Laptop' else [.20, .20, .17]
                            camera.set_world_poses_from_view(torch.tensor([middle+offset], dtype=torch.float32),
                                                            torch.tensor([middle], dtype=torch.float32))
                            for _ in range(4):
                                sim.render()
                            camera.update(dt*8, force_recompute=True)
                            close = camera.data.output['rgb'][0, ..., :3].cpu().numpy().astype(np.uint8)
                            Image.fromarray(close).save(output/f'asset-{name.lower()}.png')
                        camera.set_world_poses_from_view(torch.tensor([eye]), torch.tensor([target]))
                        for _ in range(4):
                            sim.render()
                    if step == 1416:
                        image.save(output/'frame-final.png')
                if step % 240 == 0:
                    print('ASSET_STEP', step, flush=True)
        finally:
            video.stdin.close()
            if video.wait(timeout=30):
                raise RuntimeError('video encoder failed')

        def state(name, t):
            return np.array(rows[min(len(rows)-1, round(t/dt)-1)]['states'][name])

        hold = np.array([r['hinge_rad'] for r in rows[240:480]])
        hinge = np.array([r['hinge_rad'] for r in rows])
        mouse_push = float(np.linalg.norm(state('Mouse', 2.10)[:2]-state('Mouse', 2)[:2]))
        bottle_end = state('Bottle', 4.5)
        bw, bx, by, bz = bottle_end[3:7]
        bottle_axis_z = 1-2*(bx*bx+by*by)
        force_means = {n: float(np.mean([r['contact_fz_N'][n] for r in rows[360:480]])) for n in objects}
        speeds = {n: float(np.linalg.norm(state(n, 2)[7:10])) for n in objects}
        lift = float(max(r['states']['Mouse'][2] for r in rows[1152:1296])-state('Mouse', 4.8)[2])
        checks = {
            'all_states_finite': all(np.isfinite(np.array(r['states'][n])).all() and math.isfinite(r['hinge_rad']) for r in rows for n in objects),
            'three_assets_settle_below_1cm_s': all(v < .01 for v in speeds.values()),
            'three_assets_have_support_contacts': all(v > .2 for v in force_means.values()),
            'mouse_push_translates_over_5mm': mouse_push > .005,
            'bottle_tips_over_45deg': abs(bottle_axis_z) < .707,
            'passive_hinge_holds_within_2deg': float(np.ptp(hold)) < math.radians(2),
            'hinge_moves_over_5deg_under_external_torque': abs(rows[851]['hinge_rad']-rows[791]['hinge_rad']) > math.radians(5),
            'hinge_respects_limits_within_2deg': float(hinge.min()) > -.035 and float(hinge.max()) < 2.435,
            'mouse_lifts_over_5mm_under_upward_force': lift > .005,
            'no_robot_or_test_cube': not any('Probe' in str(p.GetPath()) or 'G1Reference' in str(p.GetPath()) for p in sim.stage.Traverse()),
            'no_laptop_joint_to_stand': not any('Stand' in str(t) for p in sim.stage.Traverse() if p.IsA(UsdPhysics.Joint) for r in p.GetRelationships() for t in r.GetTargets()),
        }
        checks = {name: bool(value) for name, value in checks.items()}
        result = {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
                  'settled_linear_speed_m_s': speeds, 'mean_support_contact_fz_N': force_means,
                  'mouse_push_displacement_m': mouse_push, 'mouse_lift_m': lift,
                  'bottle_final_axis_z': float(bottle_axis_z),
                  'hinge_hold_drift_deg': float(np.degrees(np.ptp(hold))),
                  'hinge_torque_motion_deg': float(np.degrees(rows[851]['hinge_rad']-rows[791]['hinge_rad'])),
                  'hinge_range_observed_rad': [float(hinge.min()), float(hinge.max())],
                  'native_passive_hinge': {'static_Nm': .55, 'dynamic_Nm': .42, 'viscous_Nm_s_rad': .03, 'position_servo': False},
                  'candidate_sources': {n: {'model_sha256': digest(root/'objects'/specs[n]['asset_id']/'model.py'),
                                            'usd_sha256': digest(files[n]), 'usd_ref': str(files[n].relative_to(output)),
                                            'dependency_hashes': {str(p.relative_to(files[n].parent)): digest(p)
                                                                  for p in files[n].parent.rglob('*') if p.is_file()}} for n in objects},
                  'table_geometry': geometry, 'scale_source': 'estimated; user-confirmed 14-inch MBP catalog reference',
                  'physics_parameters_source': 'assumed and tested in this simulator, not measured',
                  'simulator': 'Isaac Sim 5.1 / Isaac Lab 2.3.2; CPU PhysX TGS; dt=1/240; 16/4 iterations',
                  'scope': 'Rigid body contact and passive hinge sanity. No grasp controller, liquid, cap mechanism or real-world accuracy validation.',
                  'scene_ref': 'scene.usda', 'scene_sha256': digest(output/'scene.usda')}
        (output/'trajectory.json').write_text(json.dumps(rows)+'\n')
        (output/'validation.json').write_text(json.dumps(result, indent=2)+'\n')
        print('ASSET_RESULT', json.dumps(result), flush=True)
        exit_code = 0 if result['status'] == 'passed' else 2
    except BaseException:
        import traceback
        traceback.print_exc()
        if output.exists():
            (output/'failure.json').write_text(json.dumps({'status': 'failed', 'traceback': traceback.format_exc()}, indent=2))
    finally:
        sys.stdout.flush()
        app.close(wait_for_replicator=False)
    return exit_code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.input.resolve(), args.output.resolve()))
