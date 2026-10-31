"""Exercise the unchanged delivered electric tool in Isaac, with declared test fixtures."""

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.asset = args.asset.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    adapter_path = Path(__file__).resolve().parents[2] / "src/roboenv_perception/tools/power_tool_usd.py"
    spec = importlib.util.spec_from_file_location("power_tool_usd", adapter_path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    source_hashes = {str(p): digest(p) for p in (args.asset, adapter_path, Path(__file__).resolve())}
    (args.output / "request.json").write_text(json.dumps({"source_hashes": source_hashes,
        "fixture": "bench housing fixed; free tool gravity disabled for reaction check; third tool dropped on plane",
        "robot_grasp": "not_run", "screw_engagement": "not_run"}, indent=2))
    from isaaclab.app import AppLauncher

    app = AppLauncher(headless=True, device="cpu",
                      kit_args=f"--portable-root {args.output / 'kit-runtime'}").app
    sim = None
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg
        from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

        torch.set_num_threads(2)
        scene_path = args.output / "test_scene.usda"
        stage = Usd.Stage.CreateNew(str(scene_path))
        world = UsdGeom.Xform.Define(stage, "/World")
        stage.SetDefaultPrim(world.GetPrim())
        UsdGeom.SetStageUpAxis(stage, "Z")
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        paths = {}
        for name, pos in [("bench", (0, 0, .08)), ("reaction", (-.3, 0, .08)),
                          ("table", (.3, 0, .04))]:
            root = UsdGeom.Xform.Define(stage, "/World/" + name)
            root.GetPrim().GetReferences().AddReference(str(args.asset))
            root.AddTranslateOp(opSuffix="placement").Set(Gf.Vec3d(*pos))
            manifest = json.loads(root.GetPrim().GetAttribute("envcreator:toolSpec").Get())
            if name == "bench":
                fixture = UsdPhysics.FixedJoint.Define(stage, f"/World/{name}/test_fixture")
                fixture.CreateBody1Rel().SetTargets([f"/World/{name}" + manifest["usd_paths"]["housing"]])
                fixture.CreateLocalPos0Attr(Gf.Vec3f(*pos))
                root.GetPrim().RemoveAPI(UsdPhysics.ArticulationRootAPI)
                UsdPhysics.ArticulationRootAPI.Apply(fixture.GetPrim())
                paths[name] = str(fixture.GetPath())
            else:
                paths[name] = str(root.GetPath())
            if name == "reaction":
                for body in ("housing", "spindle"):
                    prim = stage.GetPrimAtPath(f"/World/{name}" + manifest["usd_paths"][body])
                    PhysxSchema.PhysxRigidBodyAPI.Apply(prim).CreateDisableGravityAttr(True)
        floor = UsdGeom.Cube.Define(stage, "/World/tabletop")
        floor.CreateSizeAttr(1)
        floor.AddTranslateOp().Set(Gf.Vec3d(0, 0, -.025))
        floor.AddScaleOp().Set(Gf.Vec3f(1, .5, .05))
        UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
        stage.GetRootLayer().Save()
        if not omni.usd.get_context().open_stage(str(scene_path)):
            raise RuntimeError("failed to load test scene")
        live = omni.usd.get_context().get_stage()
        live.SetEditTarget(Usd.EditTarget(live.GetSessionLayer()))
        dt = 1 / 480
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            device="cpu", dt=dt, render_interval=480,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True)))
        assets = {name: Articulation(ArticulationCfg(prim_path=path, spawn=None, actuators={}))
                  for name, path in paths.items()}
        sim.reset()
        for asset in assets.values():
            asset.reset()
        bench = assets["bench"]
        indices = {name: {"joint": asset.joint_names.index(manifest["motor"]["simulation_joint_name"]),
                          "housing": asset.body_names.index("housing"),
                          "spindle": asset.body_names.index("spindle")}
                   for name, asset in assets.items()}
        traces = []
        phases = [("baseline", .75, 0, False, 0), ("forward", 2.5, 30, True, 0),
                  ("coast", .25, 0, False, 0), ("brake", .25, 0, True, 0),
                  ("reverse", 1.0, -30, True, 0), ("brake_reverse", .25, 0, True, 0),
                  ("loaded", .75, 30, True, -.05), ("brake_loaded", .25, 0, True, 0)]
        step = 0
        for phase, duration, rpm, enabled, load in phases:
            adapter.set_motor(live, "/World/bench", rpm, enabled=enabled)
            # Only the first forward phase drives the freely suspended comparison.
            adapter.set_motor(live, "/World/reaction", 30, enabled=phase == "forward")
            for local_step in range(round(duration / dt)):
                torques = torch.zeros((1, len(bench.body_names), 3))
                torques[0, indices["bench"]["spindle"], 1] = load
                bench.instantaneous_wrench_composer.set_forces_and_torques(
                    torch.zeros_like(torques), torques, is_global=True)
                for asset in assets.values():
                    asset.write_data_to_sim()
                sim.step(render=False)
                for asset in assets.values():
                    asset.update(dt)
                if local_step % 8 == 0 or local_step == round(duration / dt) - 1:
                    row = {"time_s": (step + 1) * dt, "phase": phase, "command_rpm": rpm,
                           "enabled": enabled, "opposing_load_Nm": load}
                    for name, asset in assets.items():
                        ids = indices[name]
                        row[name] = {"angle_rad": float(asset.data.joint_pos[0, ids["joint"]]),
                                     "velocity_rad_s": float(asset.data.joint_vel[0, ids["joint"]]),
                                     "housing_position_m": asset.data.body_pos_w[0, ids["housing"]].tolist(),
                                     "housing_omega_world_rad_s": asset.data.body_ang_vel_w[0, ids["housing"]].tolist(),
                                     "spindle_omega_world_rad_s": asset.data.body_ang_vel_w[0, ids["spindle"]].tolist()}
                    traces.append(row)
                step += 1
        # PhysX reports a wrapped continuous-joint coordinate. Accumulate measured
        # increments; preserve the raw observation and cross-check against velocity.
        for name in assets:
            unwrapped = np.unwrap([r[name]["angle_rad"] for r in traces])
            for row, angle in zip(traces, unwrapped, strict=True):
                row[name]["unwrapped_angle_rad"] = float(angle)
        by_phase = {phase: [r for r in traces if r["phase"] == phase] for phase, *_ in phases}
        final = {phase: rows[-1] for phase, rows in by_phase.items()}
        def speed(phase):
            return final[phase]["bench"]["velocity_rad_s"]
        reaction = final["forward"]["reaction"]
        housing_w = reaction["housing_omega_world_rad_s"][1]
        spindle_w = reaction["spindle_omega_world_rad_s"][1]
        momentum = .000054 * housing_w + .00000036 * spindle_w
        table_pos = np.asarray([r["table"]["housing_position_m"] for r in traces if r["time_s"] >= .5])
        baseline = [r["bench"]["angle_rad"] for r in by_phase["baseline"]]
        forward_delta = final["forward"]["bench"]["unwrapped_angle_rad"] - final["baseline"]["bench"]["unwrapped_angle_rad"]
        reverse_delta = final["reverse"]["bench"]["unwrapped_angle_rad"] - final["brake"]["bench"]["unwrapped_angle_rad"]
        times = np.asarray([r["time_s"] for r in traces])
        velocities = np.asarray([r["bench"]["velocity_rad_s"] for r in traces])
        measured_travel = traces[-1]["bench"]["unwrapped_angle_rad"] - traces[0]["bench"]["unwrapped_angle_rad"]
        integrated_velocity = float(np.sum(np.diff(times) * (velocities[1:] + velocities[:-1]) / 2))
        values = [v for r in traces for name in assets for v in
                  [r[name]["angle_rad"], r[name]["velocity_rad_s"], *r[name]["housing_position_m"],
                   *r[name]["housing_omega_world_rad_s"], *r[name]["spindle_omega_world_rad_s"]]]
        checks = {
            "source_unchanged": all(digest(Path(p)) == h for p, h in source_hashes.items()),
            "finite_states": bool(np.isfinite(values).all()),
            "angle_velocity_consistent": abs(measured_travel - integrated_velocity) < .05,
            "disabled_baseline": max(baseline) - min(baseline) < .01,
            "continuous_more_than_one_turn": forward_delta > math.tau,
            "forward_30rpm": abs(speed("forward") - math.pi) < .15,
            "reverse_30rpm": abs(speed("reverse") + math.pi) < .15 and reverse_delta < -2.5,
            "coasts_when_disabled": speed("coast") > 1.0,
            "brakes_in_both_directions": abs(speed("brake")) < .05 and abs(speed("brake_reverse")) < .05,
            "turns_under_opposing_005Nm": .1 < speed("loaded") < speed("forward"),
            "housing_reacts_oppositely": housing_w < -.001 and spindle_w > 1.0,
            "reaction_angular_momentum": abs(momentum) < .05 * abs(.00000036 * spindle_w),
            "table_supported": float(table_pos[:, 2].min()) > .012 and float(table_pos[:, 2].max()) < .032,
            "table_settled": float(np.ptp(table_pos[-60:], axis=0).max()) < .003,
        }
        report = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
                  "source_hashes": source_hashes, "physics": "Isaac CPU PhysX; dt=1/480 s",
                  "measurements": {"forward_angle_rad": forward_delta, "reverse_angle_rad": reverse_delta,
                                   "forward_rpm": speed("forward") * 30 / math.pi,
                                   "reverse_rpm": speed("reverse") * 30 / math.pi,
                                   "loaded_rpm": speed("loaded") * 30 / math.pi,
                                   "braked_rad_s": speed("brake"),
                                   "housing_omega_y_rad_s": housing_w, "spindle_omega_y_rad_s": spindle_w,
                                   "free_total_angular_momentum_y": momentum,
                                   "table_final_housing_z_m": float(table_pos[-1, 2])},
                  "limitations": ["bench fixture replaces a robot grasp",
                                  "reaction test disables gravity only on its free tool",
                                  "opposing torque is an applied test load, not screw engagement",
                                  "no loaded grasp, bit fit, fastening or robot control tested"],
                  "runtime_pose_writes": False}
        (args.output / "trace.json").write_text(json.dumps(traces, indent=2) + "\n")
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        return 0 if report["status"] == "passed" else 2
    finally:
        if sim is not None:
            # Remove Isaac Lab's STOP subscriber before stopping: its desktop
            # lifecycle handler otherwise keeps pumping frames after this worker.
            sim.clear_all_callbacks()
            sim.clear_instance()
            sim.stop()
        app.close(wait_for_replicator=False)


if __name__ == "__main__":
    raise SystemExit(main())
