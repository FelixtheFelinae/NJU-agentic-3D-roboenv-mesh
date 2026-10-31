"""Independent rigid-body screw bench in Isaac/PhysX, driven only by forces.

Initial scene is exported then reloaded. Runtime changes the force constraint
set, not the source USD. The rotating bolt is never assigned a runtime pose.
"""

import argparse
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

from mechanics import Parameters, ScrewModel, tool_access


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantics", type=Path, required=True)
    parser.add_argument("--dt", type=float, default=0.001)
    args = parser.parse_args()
    if not math.isfinite(args.dt) or not 0 < args.dt <= 0.001:
        raise ValueError("this explicit-force bench requires 0 < dt <= 0.001 s")
    args.output.mkdir(parents=True, exist_ok=False)
    for name in ("isaac_worker.py", "mechanics.py"):
        (args.output / name).write_bytes(Path(__file__).with_name(name).read_bytes())
    semantics = json.loads(args.semantics.read_text())
    (args.output / "semantics_snapshot.json").write_bytes(args.semantics.read_bytes())
    p = Parameters(**semantics["mechanics"])
    if (
        semantics["connection"]["axis"] != [0, 0, 1]
        or semantics["connection"]["model"] != "screw_into_fixed_threaded_support"
    ):
        raise ValueError(
            "this bench compiler supports only the declared local +Z tapped-support hypothesis"
        )
    from isaaclab.app import AppLauncher

    app = AppLauncher(
        headless=True, device="cpu", kit_args=f"--portable-root {args.output / 'kit-runtime'}"
    ).app
    import isaaclab.sim as sim_utils
    import omni.usd
    import torch
    from isaaclab.assets import RigidObject, RigidObjectCfg
    from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

    torch.set_num_threads(2)
    case_names = [
        "zero",
        "unengaged",
        "low_torque",
        "wrong_direction",
        "pull_before",
        "normal",
        "tool_blocked",
        "post_release_lateral",
    ]
    scene_path = args.output / "initial_scene.usda"
    stage = Usd.Stage.CreateNew(str(scene_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    def cube(path, xyz, size, color, collision=True):
        obj = UsdGeom.Cube.Define(stage, path)
        obj.CreateSizeAttr(1.0)
        obj.AddTranslateOp().Set(Gf.Vec3d(*xyz))
        obj.AddScaleOp().Set(Gf.Vec3f(*size))
        obj.CreateDisplayColorAttr([Gf.Vec3f(*color)])
        if collision:
            UsdPhysics.CollisionAPI.Apply(obj.GetPrim())
            contact = PhysxSchema.PhysxCollisionAPI.Apply(obj.GetPrim())
            contact.CreateContactOffsetAttr(0.0001)
            contact.CreateRestOffsetAttr(0.0)
        return obj

    z0 = 0.0336
    for i, name in enumerate(case_names):
        root = f"/World/{name}"
        UsdGeom.Xform.Define(stage, root)
        ox = float(i)
        # A square bore remains real geometry; no filled convex hull or disabled pairs.
        for label, xyz, size in [
            ("support_left", (-0.006, 0, 0.015), (0.0066, 0.018, 0.03)),
            ("support_right", (0.006, 0, 0.015), (0.0066, 0.018, 0.03)),
            ("support_front", (0, -0.006, 0.015), (0.0054, 0.0066, 0.03)),
            ("support_back", (0, 0.006, 0.015), (0.0054, 0.0066, 0.03)),
            ("beam_front", (0, -0.0076, 0.03075), (0.1, 0.0098, 0.0015)),
            ("beam_back", (0, 0.0076, 0.03075), (0.1, 0.0098, 0.0015)),
            ("beam_flange_front", (0, -0.0125, 0.024), (0.1, 0.0015, 0.012)),
            ("beam_flange_back", (0, 0.0125, 0.024), (0.1, 0.0015, 0.012)),
        ]:
            cube(root + "/" + label, (xyz[0] + ox, xyz[1], xyz[2]), size, (0.45, 0.5, 0.55))
        # Observed repeated holes; spacing and cross-section are experimental assumptions.
        holes = [j * 0.0127 for j in range(-3, 4)]
        intervals = list(
            zip([-0.05] + [x + 0.0027 for x in holes], [x - 0.0027 for x in holes] + [0.05])
        )
        for j, (a, b) in enumerate(intervals):
            cube(
                root + f"/beam_web_{j}",
                (ox + (a + b) / 2, 0, 0.03075),
                (b - a, 0.0054, 0.0015),
                (0.45, 0.5, 0.55),
            )
        bolt = UsdGeom.Xform.Define(stage, root + "/bolt")
        bolt.AddTranslateOp().Set(Gf.Vec3d(ox, 0, z0))
        UsdPhysics.RigidBodyAPI.Apply(bolt.GetPrim())
        rigid = PhysxSchema.PhysxRigidBodyAPI.Apply(bolt.GetPrim())
        rigid.CreateDisableGravityAttr(True)
        rigid.CreateLinearDampingAttr(0)
        rigid.CreateAngularDampingAttr(0)
        rigid.CreateMaxAngularVelocityAttr(1000)
        rigid.CreateSolverPositionIterationCountAttr(16)
        mass = UsdPhysics.MassAPI.Apply(bolt.GetPrim())
        mass.CreateMassAttr(0.012)
        mass.CreateCenterOfMassAttr(Gf.Vec3f(0, 0, 0))
        # Declared effective screw + ideal engaged tool inertia, not measured screw inertia.
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(2e-6, 2e-6, 2e-6))
        for label, radius, height, z in [("head", 0.004, 0.004, 0), ("shank", 0.002, 0.01, -0.007)]:
            obj = UsdGeom.Cylinder.Define(stage, root + "/bolt/" + label)
            obj.CreateRadiusAttr(radius)
            obj.CreateHeightAttr(height)
            obj.CreateAxisAttr("Z")
            obj.AddTranslateOp().Set(Gf.Vec3d(0, 0, z))
            obj.CreateDisplayColorAttr([Gf.Vec3f(0.8, 0.45, 0.12)])
            UsdPhysics.CollisionAPI.Apply(obj.GetPrim())
            contact = PhysxSchema.PhysxCollisionAPI.Apply(obj.GetPrim())
            contact.CreateContactOffsetAttr(0.0001)
            contact.CreateRestOffsetAttr(0)
        if name == "tool_blocked":
            cube(root + "/tool_obstacle", (ox, 0, z0 + 0.025), (0.02, 0.02, 0.006), (0.7, 0.1, 0.1))
    stage.GetRootLayer().Save()
    source_hash = digest(scene_path)
    if not omni.usd.get_context().open_stage(str(scene_path.resolve())):
        raise RuntimeError("could not reload initial scene")
    for _ in range(5):
        app.update()
    # Physics setup belongs in a runtime layer, never the exported asset layer.
    live_stage = omni.usd.get_context().get_stage()
    live_stage.SetEditTarget(Usd.EditTarget(live_stage.GetSessionLayer()))
    sim = sim_utils.SimulationContext(
        sim_utils.SimulationCfg(
            device="cpu",
            dt=args.dt,
            gravity=(0.0, 0.0, 0.0),
            render_interval=1000,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True),
        )
    )
    bodies = {
        name: RigidObject(RigidObjectCfg(prim_path=f"/World/{name}/bolt", spawn=None))
        for name in case_names
    }
    models = {name: ScrewModel(p) for name in case_names}
    access = {}
    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    for i, name in enumerate(case_names):
        obstacles = []
        for prim in live_stage.Traverse():
            if str(prim.GetPath()).startswith(f"/World/{name}/") and prim.IsA(UsdGeom.Cube):
                box = bounds.ComputeWorldBound(prim).ComputeAlignedRange()
                origin = (float(i), 0, z0 + 0.002)
                obstacles.append(
                    {
                        "id": str(prim.GetPath()),
                        "min": [float(box.GetMin()[j]) - origin[j] for j in range(3)],
                        "max": [float(box.GetMax()[j]) - origin[j] for j in range(3)],
                    }
                )
        access[name] = tool_access(
            semantics["tool"]["radius_m"],
            0.01175,
            semantics["tool"]["approach_length_m"],
            obstacles,
        )
        access[name]["geometry_source"] = "reloaded USD static collision-box bounds"
    sim.reset()
    for body in bodies.values():
        body.reset()
    traces = {name: [] for name in case_names}
    events = {name: [] for name in case_names}
    initial = {name: body.data.root_state_w[0, :3].tolist() for name, body in bodies.items()}
    duration = 5.2
    for step in range(round(duration / args.dt)):
        t = step * args.dt
        for name, body in bodies.items():
            model = models[name]
            s = body.data.root_state_w[0].tolist()
            rel = [s[j] - initial[name][j] for j in range(3)]
            event = model.observe(s[3:7], rel[2])
            if event:
                events[name].append(
                    {"t_s": t, **event, "artificial_guides_active": model.thread_active}
                )
            drive = 0.004 if t >= 0.25 else 0.0
            if name in {"zero", "pull_before"}:
                drive = 0.0
            if name == "low_torque":
                drive *= 0.25
            if name == "wrong_direction":
                drive = -0.004 if 0.25 <= t < 0.75 else 0.0
            pull = 0.08 if name == "pull_before" and t >= 0.25 else 0.0
            if not model.thread_active:
                drive = 0.0
                pull = 0.08
            lateral = 0.01 if name == "post_release_lateral" and rel[2] > 0.018 else 0.0
            force, torque, metrics = model.forces(
                rel,
                s[7:10],
                s[10:13],
                drive,
                pull,
                engaged=name != "unengaged",
                blocked=not access[name]["accessible"],
                transverse_pull=lateral,
            )
            # Tilt guide only while threaded; released with the helical/lateral guide.
            if model.thread_active:
                qw, qx, qy, qz = s[3:7]
                # World-frame axis error is invariant to screw yaw and q -> -q.
                axis_x = 2 * (qx * qz + qw * qy)
                axis_y = 2 * (qy * qz - qw * qx)
                torque[0] = 0.03 * axis_y - 0.001 * s[10]
                torque[1] = -0.03 * axis_x - 0.001 * s[11]
            # Instantaneous composer refreshes the world->body transform each step.
            # The installed permanent composer caches poses until explicitly reset.
            body.instantaneous_wrench_composer.set_forces_and_torques(
                torch.tensor([[force]], dtype=torch.float32),
                torch.tensor([[torque]], dtype=torch.float32),
                is_global=True,
            )
            body.write_data_to_sim()
            traces[name].append(
                {
                    "t_s": t,
                    "position_m": s[:3],
                    "relative_position_m": rel,
                    "quaternion_wxyz": s[3:7],
                    "velocity_m_s": s[7:10],
                    "angular_velocity_rad_s": s[10:13],
                    "theta_rad": model.theta,
                    "state": model.state,
                    "thread_active": model.thread_active,
                    "force_N": force,
                    "torque_Nm": torque,
                    "axial_pull_N": pull,
                    **metrics,
                }
            )
        sim.step(render=False)
        for body in bodies.values():
            body.update(args.dt)
    report = {
        "backend": "Isaac Lab / PhysX CPU rigid bodies",
        "dt_s": args.dt,
        "scene_sha256": source_hash,
        "source_unchanged": digest(scene_path) == source_hash,
        "semantics_sha256": digest(args.output / "semantics_snapshot.json"),
        "worker_sha256": digest(args.output / "isaac_worker.py"),
        "mechanics_sha256": digest(args.output / "mechanics.py"),
        "parameters": asdict(p),
        "runtime_pose_writes": 0,
        "initial_positions": initial,
        "tool_access": access,
        "events": events,
        "limitations": [
            "Hypothesized connection, not identified real hardware",
            "Zero-gravity local bench and fixed mating part; other fasteners not removed",
            "Ideal torque engagement, no tool contact slip or robot controller",
            "Effective mass/inertia include ideal tool; not measured",
            "No native screw joint: force-based compliant constraints removed at disengagement",
            "No full-car or beam-removal validation",
        ],
    }
    (args.output / "traces.json").write_text(json.dumps(traces))
    (args.output / "runtime.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                name: {
                    "state": models[name].state,
                    "dz": traces[name][-1]["relative_position_m"][2],
                }
                for name in case_names
            }
        )
    )
    sim.stop()
    app.close()


if __name__ == "__main__":
    main()
