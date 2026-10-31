"""Reload visual asset and test a five-body rolling approximation in Isaac.

The detailed assembly is preserved visually; conservative collision proxies
are declared separately. Roller micro-joints and motor transmissions are absent.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--visual", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    spec = json.loads(args.spec.read_text())
    hashes = {str(p.resolve()): digest(p) for p in [args.spec, args.visual, Path(__file__)]}
    from isaaclab.app import AppLauncher

    app = AppLauncher(
        headless=True, device="cpu", kit_args=f"--portable-root {args.output / 'kit-runtime'}"
    ).app
    import isaaclab.sim as sim_utils
    import omni.usd
    import torch
    from isaaclab.assets import Articulation, ArticulationCfg
    from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

    torch.set_num_threads(2)
    path = args.output / "cart_scene.usda"
    stage = Usd.Stage.CreateNew(str(path.resolve()))
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    parts = {p["id"]: p for p in spec["parts"]}
    wheel_ids = [p["id"] for p in spec["parts"] if p["id"] == p["body"]]
    visual_stage = Usd.Stage.Open(str(args.visual.resolve()))
    visual_root = str(visual_stage.GetDefaultPrim().GetPath())

    def proxy_cube(path, center, size):
        c = UsdGeom.Cube.Define(stage, path)
        c.CreateSizeAttr(1)
        c.AddTranslateOp().Set(Gf.Vec3d(*center))
        c.AddScaleOp().Set(Gf.Vec3f(*size))
        c.CreateVisibilityAttr("invisible")
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
        a = PhysxSchema.PhysxCollisionAPI.Apply(c.GetPrim())
        a.CreateContactOffsetAttr(0.0005)
        a.CreateRestOffsetAttr(0.0)

    for case, offset in [("free", (0.0, 0.0, 0.012)), ("wheel_probe", (1.0, 0.0, 0.22))]:
        root = f"/World/{case}"
        UsdGeom.Xform.Define(stage, root)
        UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath(root))
        for body in ["chassis", *wheel_ids]:
            center = parts[body]["center"] if body != "chassis" else [0, 0, 0]
            body_path = root + "/" + body
            prim = UsdGeom.Xform.Define(stage, body_path)
            prim.AddTranslateOp().Set(Gf.Vec3d(*[center[i] + offset[i] for i in range(3)]))
            UsdPhysics.RigidBodyAPI.Apply(prim.GetPrim())
            ma = UsdPhysics.MassAPI.Apply(prim.GetPrim())
            ma.CreateMassAttr(2.0 if body == "chassis" else 0.15)
            if body == "chassis":
                ma.CreateCenterOfMassAttr(Gf.Vec3f(0, 0, 0.075))
                ma.CreateDiagonalInertiaAttr(Gf.Vec3f(0.018, 0.016, 0.025))
            else:
                ma.CreateDiagonalInertiaAttr(Gf.Vec3f(0.00018, 0.0001, 0.0001))
            for part in spec["parts"]:
                if part["body"] != body:
                    continue
                vis = UsdGeom.Xform.Define(stage, body_path + "/" + part["id"] + "_visual")
                source = visual_root + "/" + part["id"]
                if not visual_stage.GetPrimAtPath(source):
                    raise ValueError("missing visual part " + source)
                vis.GetPrim().GetReferences().AddReference(str(args.visual.resolve()), source)
                # Override authored transform with body-local origin; wheel axes stay in world X.
                xf = UsdGeom.Xformable(vis)
                xf.ClearXformOpOrder()
                xf.AddTransformOp(opSuffix="body_local").Set(
                    Gf.Matrix4d().SetTranslate(
                        Gf.Vec3d(*[part["center"][i] - center[i] for i in range(3)])
                    )
                )
            if body == "chassis":
                for part in spec["parts"]:
                    if part["body"] != "chassis" or part["category"] not in (
                        "perforated_channel",
                        "screen_controller",
                        "black_module",
                        "black_end_block",
                    ):
                        continue
                    lo = [
                        min(
                            s["center"][i] - s["size"][i] / 2
                            for s in part["solids"]
                            if s["shape"] == "box"
                        )
                        for i in range(3)
                    ]
                    hi = [
                        max(
                            s["center"][i] + s["size"][i] / 2
                            for s in part["solids"]
                            if s["shape"] == "box"
                        )
                        for i in range(3)
                    ]
                    proxy_cube(
                        body_path + "/collision_" + part["id"],
                        [part["center"][i] + (lo[i] + hi[i]) / 2 for i in range(3)],
                        [hi[i] - lo[i] for i in range(3)],
                    )
            else:
                radius = 0.051 if "front" in body else 0.05
                width = 0.026 if "front" in body else 0.012
                c = UsdGeom.Mesh.Define(stage, body_path + "/collision_wheel")
                points = [
                    Gf.Vec3f(
                        x,
                        radius * math.sin(j * 2 * math.pi / 48),
                        radius * math.cos(j * 2 * math.pi / 48),
                    )
                    for x in [-width / 2, width / 2]
                    for j in range(48)
                ]
                faces = [list(reversed(range(48))), list(range(48, 96))] + [
                    [j, (j + 1) % 48, (j + 1) % 48 + 48, j + 48] for j in range(48)
                ]
                c.CreatePointsAttr(points)
                c.CreateFaceVertexCountsAttr([len(f) for f in faces])
                c.CreateFaceVertexIndicesAttr([i for f in faces for i in f])
                c.CreateVisibilityAttr("invisible")
                UsdPhysics.CollisionAPI.Apply(c.GetPrim())
                UsdPhysics.MeshCollisionAPI.Apply(c.GetPrim()).CreateApproximationAttr("convexHull")
                cp = PhysxSchema.PhysxCollisionAPI.Apply(c.GetPrim())
                cp.CreateContactOffsetAttr(0.0005)
                cp.CreateRestOffsetAttr(0.0)
        for body in wheel_ids:
            joint = UsdPhysics.RevoluteJoint.Define(stage, root + "/spin_" + body)
            joint.CreateBody0Rel().SetTargets([root + "/chassis"])
            joint.CreateBody1Rel().SetTargets([root + "/" + body])
            joint.CreateAxisAttr("X")
            joint.CreateLocalPos0Attr(Gf.Vec3f(*parts[body]["center"]))
            joint.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
        if case == "wheel_probe":
            joint = UsdPhysics.FixedJoint.Define(stage, root + "/fixture")
            joint.CreateBody1Rel().SetTargets([root + "/chassis"])
            joint.CreateLocalPos0Attr(Gf.Vec3f(*offset))
            stage.GetPrimAtPath(root).RemoveAPI(UsdPhysics.ArticulationRootAPI)
            UsdPhysics.ArticulationRootAPI.Apply(joint.GetPrim())
    floor = UsdGeom.Cube.Define(stage, "/World/ground")
    floor.CreateSizeAttr(1)
    floor.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.025))
    floor.AddScaleOp().Set(Gf.Vec3f(20, 20, 0.05))
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
    stage.GetRootLayer().Save()
    scene_hash = digest(path)
    omni.usd.get_context().open_stage(str(path.resolve()))
    for _ in range(5):
        app.update()
    live = omni.usd.get_context().get_stage()
    live.SetEditTarget(Usd.EditTarget(live.GetSessionLayer()))
    dt = 1 / 240
    sim = sim_utils.SimulationContext(
        sim_utils.SimulationCfg(
            device="cpu",
            dt=dt,
            render_interval=240,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True),
        )
    )
    free = Articulation(ArticulationCfg(prim_path="/World/free", spawn=None, actuators={}))
    probe = Articulation(
        ArticulationCfg(prim_path="/World/wheel_probe/fixture", spawn=None, actuators={})
    )
    sim.reset()
    free.reset()
    probe.reset()
    traces = []
    bi = free.body_names.index("chassis")
    pi = probe.body_names.index("wheel_front_left")
    pci = probe.body_names.index("chassis")
    for step in range(960):
        t = step * dt
        active = 1.5 <= t < 2.5
        f = torch.zeros((1, len(free.body_names), 3))
        tau = torch.zeros_like(f)
        if active:
            f[0, bi, 1] = 5.0
        free.instantaneous_wrench_composer.set_forces_and_torques(f, tau, is_global=True)
        free.write_data_to_sim()
        pt = torch.zeros((1, len(probe.body_names), 3))
        if active:
            pt[0, pi, 0] = 0.002
            pt[0, pci, 0] = -0.002
        probe.instantaneous_wrench_composer.set_forces_and_torques(
            torch.zeros_like(pt), pt, is_global=True
        )
        probe.write_data_to_sim()
        sim.step(render=False)
        free.update(dt)
        probe.update(dt)
        traces.append(
            {
                "t_s": (step + 1) * dt,
                "force_N": 5.0 if active else 0.0,
                "probe_torque_Nm": 0.002 if active else 0.0,
                "root": free.data.root_state_w[0].tolist(),
                "joints": free.data.joint_pos[0].tolist(),
                "probe_joints": probe.data.joint_pos[0].tolist(),
                "body_positions": free.data.body_pos_w[0].tolist(),
            }
        )
    baseline = [r for r in traces if 1.0 <= r["t_s"] <= 1.5]
    drive = [r for r in traces if 1.5 <= r["t_s"] <= 2.5]
    idx = probe.joint_names.index("spin_wheel_front_left")
    metrics = {
        "baseline_z_span_m": max(r["root"][2] for r in baseline)
        - min(r["root"][2] for r in baseline),
        "push_displacement_m": drive[-1]["root"][1] - baseline[-1]["root"][1],
        "probe_rotation_rad": drive[-1]["probe_joints"][idx] - baseline[-1]["probe_joints"][idx],
        "wheel_rotation_rad": max(
            abs(a - b) for a, b in zip(drive[-1]["joints"], baseline[-1]["joints"])
        ),
    }
    checks = {
        "finite": all(
            math.isfinite(x) for r in traces for x in r["root"] + r["joints"] + r["probe_joints"]
        ),
        "stable_baseline": metrics["baseline_z_span_m"] < 0.003,
        "push_response": metrics["push_displacement_m"] > 0.02,
        "wheel_turns": metrics["wheel_rotation_rad"] > 0.1,
        "torque_response": metrics["probe_rotation_rad"] > 0.2,
        "no_chassis_fall": min(r["root"][2] for r in drive) > -0.015,
        "upright": all(2 * (r["root"][4] ** 2 + r["root"][5] ** 2) < 0.05 for r in drive),
        "source_unchanged": digest(path) == scene_hash
        and all(digest(k) == v for k, v in hashes.items()),
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "source_hashes": hashes,
        "scene_sha256": scene_hash,
        "body_names": free.body_names,
        "joint_names": free.joint_names,
        "dt_s": dt,
        "runtime_pose_writes": 0,
        "collision_model": "conservative box chassis and cylindrical tires; all tire rollers fused, not omni contact",
        "mass_source": "assumed 2 kg chassis + 0.15 kg each wheel",
        "drive_source": "external push and fixed-base single-wheel torque, no inferred drivetrain",
        "scope": "rolling approximation, not full assembly release",
    }
    (args.output / "traces.json").write_text(json.dumps(traces))
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    sim.stop()
    app.close()


if __name__ == "__main__":
    main()
