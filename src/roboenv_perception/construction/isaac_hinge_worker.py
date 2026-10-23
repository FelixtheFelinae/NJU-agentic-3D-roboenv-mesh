"""Specialist hinge tests on the reviewed scene. Never change the asset parameters."""

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(source, output, suite='primary'):
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
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import Camera, CameraCfg
        from PIL import Image, ImageDraw
        from pxr import UsdPhysics

        from roboenv_perception.construction.hinge import evaluate_hinge_case, hinge_cases

        torch.set_num_threads(4)
        output.mkdir(parents=True, exist_ok=False)
        bindings = json.loads((source/'validation.json').read_text())
        paths = [source/'scene.usda']
        for binding in bindings['candidate_sources'].values():
            base = (source/binding['usd_ref']).parent
            paths += [base/ref for ref in binding['dependency_hashes']]
        hashes = {str(p): digest(p) for p in paths}
        if not omni.usd.get_context().open_stage(str(source/'scene.usda')):
            raise RuntimeError('Cannot open the reviewed scene')
        dt = 1/240
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            device='cpu', dt=dt, render_interval=8,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True)))
        laptop = Articulation(ArticulationCfg(prim_path='/World/Laptop', spawn=None, actuators={}))
        camera = Camera(CameraCfg(prim_path='/World/HingeTestCamera', width=960, height=720,
            data_types=['rgb'], update_period=0, spawn=sim_utils.PinholeCameraCfg(
                focal_length=30, horizontal_aperture=28, clipping_range=(.03, 100))))
        eye, target = [1.08, .30, 1.12], [.55, .035, .90]
        sim.set_camera_view(eye, target)
        sim.reset()
        camera.set_world_poses_from_view(torch.tensor([eye]), torch.tensor([target]))
        for _ in range(240):
            sim.step(render=False)
            laptop.update(dt)
        initial = laptop.data.root_state_w.clone()
        initial[:, 7:] = 0
        base_id, lid_id = laptop.body_names.index('base'), laptop.body_names.index('lid')
        joint = next(p for p in sim.stage.Traverse() if p.IsA(UsdPhysics.RevoluteJoint))
        limits = UsdPhysics.RevoluteJoint(joint)
        lower = math.radians(limits.GetLowerLimitAttr().Get())
        upper = math.radians(limits.GetUpperLimitAttr().Get())
        if joint.GetAttribute('drive:angular:physics:stiffness').Get() not in {None, 0}:
            raise RuntimeError('Test requires a passive hinge without a position servo')
        video = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '960x720', '-r', '30', '-i', 'pipe:0',
            '-an', '-c:v', 'h264_nvenc', '-cq', '20', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            str(output/'hinge-interaction.mp4')], stdin=subprocess.PIPE)
        results, traces = [], []
        try:
            for case in hinge_cases(suite):
                laptop.set_external_force_and_torque(torch.zeros((1, 2, 3)), torch.zeros((1, 2, 3)), is_global=True)
                laptop.write_root_state_to_sim(initial.clone())
                laptop.write_joint_state_to_sim(torch.tensor([[math.radians(case['initial_deg'])]], dtype=torch.float32),
                                                 torch.zeros((1, 1), dtype=torch.float32))
                laptop.reset()
                rows = []
                for step in range(round(case['duration']/dt)):
                    t = step*dt
                    torque = case['torque_Nm'] if case['force_start'] <= t < case['force_end'] else 0
                    forces = torch.zeros((1, 2, 3), dtype=torch.float32)
                    torques = torch.zeros_like(forces)
                    w, x, y, z = laptop.data.root_quat_w[0].cpu().numpy()
                    axis = torch.tensor([1-2*(y*y+z*z), 2*(x*y+w*z), 2*(x*z-w*y)], dtype=torch.float32)
                    torques[0, lid_id] = torque*axis
                    torques[0, base_id] = -torque*axis
                    laptop.set_external_force_and_torque(forces, torques, is_global=True)
                    laptop.write_data_to_sim()
                    render = step % 8 == 0
                    sim.step(render=render)
                    laptop.update(dt)
                    row = {'t': t+dt, 'q_rad': float(laptop.data.joint_pos[0, 0]),
                           'qd_rad_s': float(laptop.data.joint_vel[0, 0]), 'torque_Nm': torque,
                           'root_state': laptop.data.root_state_w[0].cpu().numpy().tolist()}
                    rows.append(row)
                    if render:
                        camera.update(dt*8, force_recompute=True)
                        rgb = camera.data.output['rgb'][0, ..., :3].cpu().numpy().astype(np.uint8)
                        image = Image.fromarray(rgb)
                        draw = ImageDraw.Draw(image)
                        draw.rectangle((0, 0, 960, 68), fill=(25, 28, 35))
                        direction = 'OPEN +' if torque > 0 else ('CLOSE -' if torque < 0 else 'RELEASE / HOLD')
                        draw.text((14, 12), f'{case["name"]} | {direction} | t={t:.2f}s', fill='white')
                        draw.text((14, 38), f'q={math.degrees(row["q_rad"]):.2f} deg | speed={math.degrees(row["qd_rad_s"]):.2f} deg/s | torque={torque:+.2f} Nm', fill='white')
                        video.stdin.write(image.tobytes())
                        if step == round(case['duration']/dt)-8:
                            image.save(output/(case['name']+'.png'))
                result = evaluate_hinge_case(case, rows, lower, upper)
                results.append(result)
                traces.append({'protocol': case, 'samples': rows})
                print('HINGE_CASE', json.dumps(result), flush=True)
        finally:
            video.stdin.close()
            if video.wait(timeout=30):
                raise RuntimeError('Video encoding failed')
        unchanged = all(digest(Path(p)) == value for p, value in hashes.items())
        checks = {r['name']: r['status'] == 'passed' for r in results}
        checks['reviewed_scene_and_assets_unchanged'] = unchanged
        result = {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
                  'cases': results, 'source_hashes': hashes, 'source_scene': str(source/'scene.usda'),
                  'parameters_modified': False, 'actuators': [], 'base_fixed': False,
                  'test_method': 'Each trial initializes an angle and the settled free base; equal/opposite external body torque couple; release has zero applied torque.',
                  'limits_deg': [math.degrees(lower), math.degrees(upper)],
                  'suite': suite,
                  'scope': 'Simulation behavior of the current assumed hinge parameters, not measured MacBook hinge behavior.'}
        (output/'trajectory.json').write_text(json.dumps(traces)+'\n')
        (output/'validation.json').write_text(json.dumps(result, indent=2)+'\n')
        print('HINGE_RESULT', result['status'], flush=True)
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
    parser.add_argument('--suite', choices=['primary', 'followup'], default='primary')
    args = parser.parse_args()
    raise SystemExit(run(args.input.resolve(), args.output.resolve(), args.suite))
