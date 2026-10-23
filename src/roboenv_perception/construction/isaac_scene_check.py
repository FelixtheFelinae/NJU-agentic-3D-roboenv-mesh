"""Fresh-process reload of the portable delivered scene, without pose corrections."""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


def run(source, output):
    from isaaclab.app import AppLauncher

    launcher = AppLauncher(headless=True, device='cpu',
                           kit_args=f'--portable-root {output.parent / "kit-runtime"}')
    app = launcher.app
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg

        torch.set_num_threads(4)
        output.mkdir(parents=True, exist_ok=False)
        scene = source/'scene.usda'
        ok = omni.usd.get_context().open_stage(str(scene))
        if not ok:
            raise RuntimeError('USD context could not open portable scene')
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(device='cpu', dt=1/240,
                                                                  render_interval=240))
        laptop = Articulation(ArticulationCfg(prim_path='/World/Laptop', spawn=None, actuators={}))
        rigid = {n: RigidObject(RigidObjectCfg(prim_path='/World/'+n, spawn=None)) for n in ['Mouse', 'Bottle']}
        sim.reset()
        rows = []
        for step in range(360):
            sim.step(render=False)
            laptop.update(1/240)
            for obj in rigid.values():
                obj.update(1/240)
            rows.append({'hinge_rad': float(laptop.data.joint_pos[0, 0]),
                         'laptop': laptop.data.root_state_w[0].cpu().numpy().tolist(),
                         **{n: o.data.root_state_w[0].cpu().numpy().tolist() for n, o in rigid.items()}})
        q = np.array([r['hinge_rad'] for r in rows])
        expected = json.loads((source.parent/'objects/asset-0002/build-spec.json').read_text())['scene_opening_rad']
        checks = {'fresh_scene_all_states_finite': all(np.isfinite(r[n]).all() for r in rows for n in ['laptop', 'Mouse', 'Bottle']),
                  'fresh_scene_initial_hinge_matches_within_2deg': abs(float(q[0])-expected) < math.radians(2),
                  'fresh_scene_hinge_remains_open': abs(float(q[-1])-expected) < math.radians(2),
                  'fresh_scene_objects_stay_on_support': all(rows[-1][n][2] >= .748 for n in ['laptop', 'Mouse', 'Bottle']),
                  'fresh_scene_objects_settle': all(np.linalg.norm(rows[-1][n][7:10]) < .01 for n in ['laptop', 'Mouse', 'Bottle'])}
        checks = {k: bool(v) for k, v in checks.items()}
        result = {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
                  'initial_hinge_deg': float(np.degrees(q[0])), 'final_hinge_deg': float(np.degrees(q[-1])),
                  'last_states': rows[-1], 'scene': str(scene),
                  'scene_sha256': hashlib.sha256(scene.read_bytes()).hexdigest(),
                  'scope': 'Fresh process, exported scene loaded unchanged; no pose/joint state writes or controls.'}
        (output/'validation.json').write_text(json.dumps(result, indent=2)+'\n')
        print('SCENE_RELOAD_RESULT', json.dumps(result), flush=True)
        code = 0 if result['status'] == 'passed' else 2
    except BaseException:
        import traceback
        traceback.print_exc()
        output.mkdir(parents=True, exist_ok=True)
        (output/'failure.json').write_text(json.dumps({'traceback': traceback.format_exc()}))
        code = 2
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
