"""Causal drivetrain tests using native PhysX coupling and declared passive fixtures."""

import argparse
import hashlib
import json
import shutil
import subprocess
import traceback
from pathlib import Path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    from isaaclab.app import AppLauncher

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    inputs = args.input.resolve()
    source_paths = (
        [
            inputs / "scene.usda",
            inputs / "ground-observed.png",
            *sorted((inputs / "assets").rglob("*")),
        ]
        if args.mode == "check"
        else [
            *sorted((inputs / "export").rglob("*")),
            args.ground.resolve() / "ground-observed.png",
        ]
    )
    source_hashes = {str(p): sha256(p) for p in source_paths if p.is_file()}
    (output / "run-inputs.json").write_text(
        json.dumps(
            {
                "mode": args.mode,
                "source_hashes": source_hashes,
                "worker_sha256": sha256(Path(__file__)),
            },
            indent=2,
        )
        + "\n"
    )
    app = AppLauncher(
        headless=True,
        enable_cameras=True,
        device="cpu",
        kit_args=f"--portable-root {output / 'kit-runtime'}",
    ).app
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import Camera, CameraCfg
        from PIL import Image, ImageDraw
        from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

        from roboenv_perception.experiments.bicycle.isaac_bicycle_worker import (
            adapt_bicycle,
            author_ground,
        )

        torch.set_num_threads(4)
        dt = 1 / 240
        protocols = {
            "bench_driven": dict(
                x=0.0, y=4.0, z=1.0, ground=False, coupled=True, pedal=2.0, brake=0.0
            ),
            "bench_disabled": dict(
                x=0.0, y=6.0, z=1.0, ground=False, coupled=False, pedal=2.0, brake=0.0
            ),
            "bench_loaded": dict(
                x=0.0, y=8.0, z=1.0, ground=False, coupled=True, pedal=2.0, brake=0.5
            ),
            "bench_zero": dict(
                x=0.0, y=10.0, z=1.0, ground=False, coupled=False, pedal=0.0, brake=0.0
            ),
            "ground_driven": dict(
                x=-0.5, y=0.0, z=0.002, ground=True, coupled=True, pedal=4.0, brake=0.0
            ),
            "ground_zero": dict(
                x=-0.5, y=-1.0, z=0.002, ground=True, coupled=True, pedal=0.0, brake=0.0
            ),
            "ground_disabled": dict(
                x=-0.5, y=1.0, z=0.002, ground=True, coupled=False, pedal=4.0, brake=0.0
            ),
        }
        if args.mode == "check":
            source = args.input.resolve() / "scene.usda"
            if not omni.usd.get_context().open_stage(str(source)):
                raise RuntimeError("Failed to reload drivetrain scene")
        sim = sim_utils.SimulationContext(
            sim_utils.SimulationCfg(
                device="cpu",
                dt=dt,
                render_interval=8,
                physx=sim_utils.PhysxCfg(
                    solver_type=1, enable_external_forces_every_iteration=True
                ),
            )
        )
        if args.mode == "build":
            asset = output / "assets/bicycle/object.usd"
            adapt_bicycle(args.input.resolve() / "export/object.usd", asset)
            # A second asset differs only in its native coupling. No physical
            # mass, geometry, damping or collision settings change in controls.
            control = output / "assets/disconnected/object.usd"
            shutil.copytree(asset.parent, control.parent)
            stage = Usd.Stage.Open(str(control))
            for prim in stage.Traverse():
                for schema in list(prim.GetAppliedSchemas()):
                    if schema.startswith("PhysxMimicJointAPI:"):
                        prim.RemoveAppliedSchema(schema)
            stage.GetRootLayer().Save()
            shutil.copyfile(
                args.ground.resolve() / "ground-observed.png", output / "ground-observed.png"
            )
            author_ground(sim.stage, output / "ground-observed.png")
            # Extend support for the rolling test, keeping the original observed
            # patch visible; the extended collider is a declared flat fixture.
            xf = UsdGeom.Xformable(sim.stage.GetPrimAtPath("/World/Ground/Collision"))
            for op in xf.GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeScale:
                    op.Set(Gf.Vec3f(12, 3, 0.08))
            light = sim_utils.DomeLightCfg(intensity=1100)
            light.func("/World/Light", light)
            for name, p in protocols.items():
                path = "/World/" + name
                cfg = sim_utils.UsdFileCfg(usd_path=str(asset if p["coupled"] else control))
                cfg.func(path, cfg, translation=(p["x"], p["y"], p["z"]))
                # Fix the frame in air, or constrain only its transverse and
                # rotational DOFs with a frictionless longitudinal guide.
                fixture = (
                    UsdPhysics.PrismaticJoint if p["ground"] else UsdPhysics.FixedJoint
                ).Define(sim.stage, path + "/Fixture")
                fixture.CreateBody1Rel().SetTargets([path + "/frame"])
                fixture.CreateLocalPos0Attr(Gf.Vec3f(p["x"], p["y"], p["z"]))
                fixture.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
                if p["ground"]:
                    # PhysX treats a world-to-root joint as a fixed base. Use
                    # an explicit anchored root and put both slides downstream.
                    anchor_path = path + "/GuideAnchor"
                    anchor = UsdGeom.Xform.Define(sim.stage, anchor_path).GetPrim()
                    UsdPhysics.RigidBodyAPI.Apply(anchor)
                    mass = UsdPhysics.MassAPI.Apply(anchor)
                    mass.CreateMassAttr(0.01)
                    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.001))
                    fixed = UsdPhysics.FixedJoint.Define(sim.stage, path + "/AnchorToWorld")
                    fixed.CreateBody1Rel().SetTargets([anchor_path])
                    fixed.CreateLocalPos0Attr(Gf.Vec3f(p["x"], p["y"], p["z"]))
                    fixture.CreateBody0Rel().SetTargets([anchor_path])
                    fixture.CreateLocalPos0Attr(Gf.Vec3f(0))
                    # A second passive slide allows gravity to load the tyres;
                    # a single X slide would carry the weight above the floor.
                    rail_path = path + "/GuideRail"
                    rail = UsdGeom.Xform.Define(sim.stage, rail_path).GetPrim()
                    UsdPhysics.RigidBodyAPI.Apply(rail)
                    mass = UsdPhysics.MassAPI.Apply(rail)
                    mass.CreateMassAttr(0.01)
                    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.001))
                    fixture.CreateBody1Rel().SetTargets([rail_path])
                    vertical = UsdPhysics.PrismaticJoint.Define(sim.stage, path + "/VerticalSlide")
                    vertical.CreateBody0Rel().SetTargets([rail_path])
                    vertical.CreateBody1Rel().SetTargets([path + "/frame"])
                    vertical.CreateAxisAttr("Z")
                    vertical.CreateLowerLimitAttr(-0.02)
                    vertical.CreateUpperLimitAttr(0.02)
                    fixture.CreateAxisAttr("X")
                    fixture.CreateLowerLimitAttr(-4.0)
                    fixture.CreateUpperLimitAttr(4.0)
                    # Passive steering fixture: no servo input or longitudinal force.
                    for prim in list(sim.stage.Traverse()):
                        if not str(prim.GetPath()).startswith(path + "/"):
                            continue
                        if prim.GetName() == "joint_steer":
                            joint = UsdPhysics.RevoluteJoint(prim)
                            joint.CreateLowerLimitAttr(0)
                            joint.CreateUpperLimitAttr(0)
                        if str(prim.GetPath()).startswith(path + "/frame/") and prim.IsA(
                            UsdGeom.Mesh
                        ):
                            points = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get())
                            if len(points) and points[:, 2].min() < 0.1:
                                if prim.HasAPI(UsdPhysics.CollisionAPI):
                                    UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
                                UsdGeom.Imageable(prim).MakeInvisible()
                fixture.GetPrim().SetCustomDataByKey(
                    "role", "Test fixture only; no longitudinal actuator"
                )
            portable = sim.stage.GetRootLayer().ExportToString()
            for path in [asset, control, output / "ground-observed.png"]:
                portable = portable.replace(str(path), "./" + str(path.relative_to(output)))
            (output / "scene.usda").write_text(portable)
        objects = {
            name: Articulation(
                ArticulationCfg(prim_path="/World/" + name, spawn=None, actuators={})
            )
            for name in protocols
        }
        camera_path = (
            "/World/ReloadDrivetrainCamera" if args.mode == "check" else "/World/DrivetrainCamera"
        )
        camera = Camera(
            CameraCfg(
                prim_path=camera_path,
                width=960,
                height=720,
                data_types=["rgb"],
                update_period=0,
                spawn=sim_utils.PinholeCameraCfg(
                    focal_length=28, horizontal_aperture=32, clipping_range=(0.05, 50)
                ),
            )
        )
        sim.reset()
        camera.set_world_poses_from_view(
            torch.tensor([[1.7, -2.8, 1.7]]), torch.tensor([[-0.2, 0.0, 0.5]])
        )
        ids = {
            name: {
                "crank": obj.body_names.index("crank"),
                "frame": obj.body_names.index("frame"),
                "rear": obj.body_names.index("rear_wheel"),
                "pedal_joint": next(
                    i for i, n in enumerate(obj.joint_names) if n.endswith("pedal_spin")
                ),
                "rear_joint": next(
                    i for i, n in enumerate(obj.joint_names) if n.endswith("rear_spin")
                ),
                "front_joint": next(
                    i for i, n in enumerate(obj.joint_names) if n.endswith("front_spin")
                ),
            }
            for name, obj in objects.items()
        }

        def advance(step, record):
            for name, obj in objects.items():
                p = protocols[name]
                idx = ids[name]
                torques = torch.zeros((1, obj.num_bodies, 3))
                active = record and step < 240
                pedal = p["pedal"] if active else 0.0
                brake = p["brake"] if active else 0.0
                torques[0, idx["crank"], 1] = pedal
                torques[0, idx["rear"], 1] = -brake
                torques[0, idx["frame"], 1] = -pedal + brake
                obj.set_external_force_and_torque(
                    torch.zeros_like(torques), torques, is_global=True
                )
                obj.write_data_to_sim()
            sim.step(render=record and step % 8 == 0)
            for obj in objects.values():
                obj.update(dt)

        for k in range(240):
            advance(k, False)
        initial = {}
        for name, obj in objects.items():
            idx = ids[name]
            initial[name] = {
                "x_m": float(obj.data.body_pos_w[0, idx["frame"], 0]),
                "crank_rad": float(obj.data.joint_pos[0, idx["pedal_joint"]]),
                "rear_rad": float(obj.data.joint_pos[0, idx["rear_joint"]]),
                "front_rad": float(obj.data.joint_pos[0, idx["front_joint"]]),
            }
        (output / "initial-state.json").write_text(json.dumps(initial, indent=2) + "\n")
        traces = {name: [] for name in protocols}
        video = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                "960x720",
                "-r",
                "30",
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output / "drivetrain.mp4"),
            ],
            stdin=subprocess.PIPE,
        )
        try:
            for k in range(720):
                advance(k, True)
                for name, obj in objects.items():
                    idx = ids[name]
                    q = obj.data.joint_pos[0].cpu().numpy()
                    qd = obj.data.joint_vel[0].cpu().numpy()
                    traces[name].append(
                        {
                            "t": (k + 1) * dt,
                            "crank_rad": float(q[idx["pedal_joint"]]),
                            "rear_rad": float(q[idx["rear_joint"]]),
                            "front_rad": float(q[idx["front_joint"]]),
                            "crank_rad_s": float(qd[idx["pedal_joint"]]),
                            "rear_rad_s": float(qd[idx["rear_joint"]]),
                            "x_m": float(obj.data.body_pos_w[0, idx["frame"], 0]),
                            "vx_m_s": float(obj.data.body_lin_vel_w[0, idx["frame"], 0]),
                            "pedal_input_Nm": protocols[name]["pedal"] if k < 240 else 0.0,
                        }
                    )
                if k % 8 == 0:
                    # Ground demonstration first, then the suspended transmission.
                    focus = "ground_driven" if k < 480 else "bench_driven"
                    # Render only the inspected fixture, retaining every body's
                    # collision and dynamics. The saved scene stays unmodified.
                    for name in protocols:
                        view = UsdGeom.Imageable(sim.stage.GetPrimAtPath("/World/" + name))
                        if name == focus:
                            view.MakeVisible()
                        else:
                            view.MakeInvisible()
                    if focus == "bench_driven":
                        # These are the two guard visuals in this exported asset.
                        # Keep their physical colliders and the delivered cover.
                        for guard in ["vis_15", "vis_16"]:
                            UsdGeom.Imageable(
                                sim.stage.GetPrimAtPath("/World/bench_driven/frame/" + guard)
                            ).MakeInvisible()
                    p = protocols[focus]
                    x = traces[focus][-1]["x_m"]
                    eye = [x + 0.3, p["y"] - 3.4, p["z"] + 1.35]
                    target = [x, p["y"], p["z"] + 0.5]
                    if focus == "bench_driven":
                        eye = [x - 0.27, p["y"] - 1.4, p["z"] + 0.55]
                        target = [x - 0.27, p["y"], p["z"] + 0.30]
                    camera.set_world_poses_from_view(
                        torch.tensor([eye]),
                        torch.tensor([target]),
                    )
                    sim.render()
                    camera.update(dt * 8, force_recompute=True)
                    im = Image.fromarray(
                        camera.data.output["rgb"][0, ..., :3].cpu().numpy().astype("uint8")
                    )
                    draw = ImageDraw.Draw(im)
                    draw.rectangle((0, 0, 960, 60), fill=(20, 25, 30))
                    row = traces[focus][-1]
                    draw.text(
                        (12, 10),
                        f"{focus} | crank-only input {row['pedal_input_Nm']:.1f} Nm | t={row['t']:.2f}s",
                        fill="white",
                    )
                    if focus == "bench_driven":
                        draw.text(
                            (12, 78),
                            "CUTAWAY: guard visuals hidden; stationary chain envelope",
                            fill="black",
                        )
                    draw.text(
                        (12, 34),
                        f"crank={row['crank_rad']:.3f} rear={row['rear_rad']:.3f} rad | native ratio 2.5 | passive fixture",
                        fill="white",
                    )
                    video.stdin.write(im.tobytes())
                    if k in [232, 704]:
                        im.save(
                            output / ("ground-preview.png" if k == 232 else "bench-preview.png")
                        )
        finally:
            video.stdin.close()
            (output / "trajectory.json").write_text(json.dumps(traces) + "\n")
            if video.wait(timeout=30):
                raise RuntimeError("Video encoding failed")
        metrics = {}
        for name, rows in traces.items():
            last = rows[-1]
            p = protocols[name]
            travel = last["x_m"] - initial[name]["x_m"]
            rear_delta = last["rear_rad"] - initial[name]["rear_rad"]
            front_delta = last["front_rad"] - initial[name]["front_rad"]
            metrics[name] = {
                "travel_m": travel,
                "rear_angle_rad": rear_delta,
                "crank_angle_rad": last["crank_rad"] - initial[name]["crank_rad"],
                "speed_at_release_rad_s": rows[239]["crank_rad_s"],
                "max_ratio_residual_rad": max(
                    abs(r["rear_rad"] - 2.5 * r["crank_rad"]) for r in rows
                ),
                "rear_rolling_error_m": abs(travel - 0.30 * rear_delta),
                "front_rolling_error_m": abs(travel - 0.30 * front_delta),
            }
        md = metrics["bench_driven"]
        ml = metrics["bench_loaded"]
        gd = metrics["ground_driven"]
        checks = {
            "bench_crank_drives_rear": md["rear_angle_rad"] > 0.1,
            "native_ratio_preserved": md["max_ratio_residual_rad"] < 0.03,
            "disconnected_rear_matches_unpowered_baseline": max(
                abs(a["rear_rad"] - b["rear_rad"])
                for a, b in zip(traces["bench_disabled"], traces["bench_zero"], strict=True)
            )
            < 0.03,
            "rear_load_reaches_crank": 0
            < ml["speed_at_release_rad_s"]
            < 0.85 * md["speed_at_release_rad_s"],
            "ground_pedal_input_causes_travel": gd["travel_m"] > 0.1,
            "ground_zero_input_stationary": abs(metrics["ground_zero"]["travel_m"]) < 0.03,
            "ground_disconnected_control_stationary": abs(metrics["ground_disabled"]["travel_m"])
            < 0.03,
            "ground_native_ratio_preserved": gd["max_ratio_residual_rad"] < 0.03,
            "ground_rear_rolling_consistency": gd["rear_rolling_error_m"] < 0.05,
            "ground_front_rolling_consistency": gd["front_rolling_error_m"] < 0.05,
            "finite_states": all(
                np.isfinite(list(r.values())).all() for rows in traces.values() for r in rows
            ),
            "source_unchanged": all(sha256(Path(p)) == h for p, h in source_hashes.items()),
        }
        # Check the applied schema and its relationship after all reparenting.
        native = []
        for prim in sim.stage.Traverse():
            if any(s.startswith("PhysxMimicJointAPI:") for s in prim.GetAppliedSchemas()):
                api = PhysxSchema.PhysxMimicJointAPI.Get(prim, "rotZ")
                native.append(
                    {
                        "path": str(prim.GetPath()),
                        "gearing": api.GetGearingAttr().Get(),
                        "reference": [str(p) for p in api.GetReferenceJointRel().GetTargets()],
                    }
                )
        checks["four_native_couplings"] = len(native) == 4
        checks["native_targets_and_gearing"] = all(
            n["gearing"] == -2.5
            and n["reference"] == [n["path"].split("/rear_wheel/")[0] + "/crank/joint_pedal_spin"]
            and bool(sim.stage.GetPrimAtPath(n["reference"][0]))
            for n in native
        )
        checks["runtime_angle_bounds_not_reached"] = all(
            abs(row[key]) < 900
            for rows in traces.values()
            for row in rows
            for key in ["crank_rad", "rear_rad"]
        )
        report = {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "metrics": metrics,
            "native_couplings": native,
            "protocols": protocols,
            "dt": dt,
            "initial_state": initial,
            "rolling_radius_m": 0.30,
            "ground_input_scope": "4 Nm for 1 s, followed by 2 s release; higher torque not qualified",
            "source_hashes": source_hashes,
            "scope": "Ideal fixed-gear transmission under declared fixtures; not free-balancing riding",
            "chain_visual": "Continuous stationary envelope attached to frame; sprockets rotate with bodies; no simulated links",
        }
        (output / "trajectory.json").write_text(json.dumps(traces) + "\n")
        (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps({"status": report["status"], "checks": checks, "metrics": metrics}),
            flush=True,
        )
        return 0 if all(checks.values()) else 2
    except BaseException:
        (output / "failure.json").write_text(
            json.dumps({"traceback": traceback.format_exc()}, indent=2) + "\n"
        )
        raise
    finally:
        app.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["build", "check"])
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--ground", type=Path)
    args = p.parse_args()
    if args.mode == "build" and not args.ground:
        p.error("build requires --ground")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
