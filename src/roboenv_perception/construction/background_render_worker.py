"""Render the delivered scene and compare a visual background on/off in Isaac Lab."""

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path


def render(scene: Path, output: Path, camera_path: str, width: int, height: int):
    from isaaclab.app import AppLauncher

    output.mkdir(parents=True, exist_ok=False)
    launcher = AppLauncher(headless=True, enable_cameras=True, device="cpu",
                           kit_args=f"--portable-root {output.parent / 'kit-runtime'}")
    app = launcher.app
    code = 0
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
        from isaaclab.sensors import Camera, CameraCfg
        from PIL import Image
        from pxr import UsdGeom, UsdPhysics

        if not omni.usd.get_context().open_stage(str(scene)):
            raise RuntimeError("Isaac could not load the background scene")
        stage = omni.usd.get_context().get_stage()
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(device="cpu", dt=1/240, render_interval=1))
        camera = Camera(CameraCfg(prim_path=camera_path, spawn=None, width=width, height=height,
                                   data_types=["rgb", "distance_to_image_plane"], update_period=0))
        laptop = Articulation(ArticulationCfg(prim_path="/World/Laptop", spawn=None, actuators={}))
        bodies = {name: RigidObject(RigidObjectCfg(prim_path="/World/" + name, spawn=None))
                  for name in ["Bottle", "Mouse"]}
        sim.reset()
        background = UsdGeom.Imageable(stage.GetPrimAtPath("/World/EnvCreatorBackground"))
        if not background:
            raise RuntimeError("scene is missing the expected visual background")

        def capture(filename):
            for _ in range(16):
                sim.render()
                camera.update(1/30, force_recompute=True)
            rgb = camera.data.output["rgb"][0, ..., :3].cpu().numpy().astype(np.uint8).copy()
            Image.fromarray(rgb).save(output / filename)
            depth = camera.data.output["distance_to_image_plane"][0].cpu().numpy().copy()
            return rgb, np.squeeze(depth)

        initial_states = {"Laptop": laptop.data.root_state_w[0].cpu().numpy().tolist(),
                          **{name: obj.data.root_state_w[0].cpu().numpy().tolist() for name, obj in bodies.items()}}
        hinge_initial = float(laptop.data.joint_pos[0, 0])
        background.MakeInvisible()
        off, off_depth = capture("background-off.png")
        background.MakeVisible()
        on, on_depth = capture("background-on.png")
        delta = np.abs(on.astype(np.int16) - off.astype(np.int16)).mean(axis=-1)
        changed = delta > 5
        close = np.isfinite(off_depth) & (off_depth < 2.5)
        # Rendering should not step or write to physics; this compares identical simulation states.
        render_states = {"Laptop": laptop.data.root_state_w[0].cpu().numpy().tolist(),
                         **{name: obj.data.root_state_w[0].cpu().numpy().tolist() for name, obj in bodies.items()}}
        for _ in range(120):
            sim.step(render=False)
            laptop.update(1/240)
            for obj in bodies.values():
                obj.update(1/240)
        _, _ = capture("background-after-physics.png")
        final_states = {"Laptop": laptop.data.root_state_w[0].cpu().numpy().tolist(),
                        **{name: obj.data.root_state_w[0].cpu().numpy().tolist() for name, obj in bodies.items()}}
        no_background_physics = not any(
            p.HasAPI(UsdPhysics.CollisionAPI) or p.HasAPI(UsdPhysics.RigidBodyAPI)
            for p in stage.Traverse() if str(p.GetPath()).startswith("/World/EnvCreatorBackground"))
        checks = {"scene_loaded": True, "background_visible_in_sensor": float(changed.mean()) > 0.01,
                  "background_has_no_physics": no_background_physics,
                  "render_toggle_preserves_body_states": initial_states == render_states,
                  "all_states_finite_after_120_steps": all(np.isfinite(v).all() for v in final_states.values()),
                  "task_bodies_remain_above_support": all(v[2] > 0.74 for v in final_states.values()),
                  "laptop_hinge_within_2deg": abs(float(laptop.data.joint_pos[0, 0]) - hinge_initial) < np.deg2rad(2)}
        checks = {name: bool(value) for name, value in checks.items()}
        report = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
                  "source": str(scene), "source_sha256": hashlib.sha256(scene.read_bytes()).hexdigest(),
                  "camera": camera_path, "resolution_wh": [width, height],
                  "changed_pixel_fraction": float(changed.mean()),
                  "near_surface_mean_rgb_change": float(delta[close].mean()) if close.any() else None,
                  "initial_states": initial_states, "final_states": final_states,
                  "scope": "Existing laptop fixture, real Isaac sensor RGB, fixed camera, no robot or controls.",
                  "depth_note": "Background card depth is a display distance, not recovered room geometry.",
                  "physics_scope": "120 steps for existing laptop/bottle/mouse; no room collisions introduced."}
        (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
        print("BACKGROUND_RENDER_RESULT", json.dumps(report), flush=True)
        code = 0 if report["status"] == "passed" else 2
    except BaseException:
        (output / "failure.json").write_text(json.dumps({"traceback": traceback.format_exc()}))
        traceback.print_exc()
        code = 2
    finally:
        sys.stdout.flush()
        app.close(wait_for_replicator=False)
    return code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--camera", default="/World/Overview")
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=576)
    args = parser.parse_args()
    raise SystemExit(render(args.scene.resolve(strict=True), args.output.resolve(), args.camera,
                            args.width, args.height))
