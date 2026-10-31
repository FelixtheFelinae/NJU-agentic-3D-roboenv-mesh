"""Actual exported screwdriver, ideal XYZ carriage, matching screw and negative controls."""

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import traceback
from dataclasses import asdict
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.asset, args.output = args.asset.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).parent
    motor = load("tool_motor", source / "power_tool_usd.py")
    engagement = load("tool_engagement", source / "screwdriving.py")
    fastener = load("tool_fastener", source / "fastener.py")
    hashes = {str(p.resolve()): digest(p) for p in [args.asset, Path(__file__),
              source / "power_tool_usd.py", source / "screwdriving.py", source / "fastener.py"]}
    cases = ["aligned", "offset", "tilted", "no_contact", "wrong_socket", "motor_off"]
    dt, duration, face_z = .0005, 12.5, .804
    parameters = fastener.Parameters()
    (args.output / "request.json").write_text(json.dumps({
        "source_hashes": hashes, "cases": cases, "dt_s": dt, "duration_s": duration,
        "thread_parameters": asdict(parameters), "engagement_parameters": asdict(engagement.EngagementParameters()),
        "assumptions": ["ideal force-driven XYZ carriage, prescribed axis from target frame",
                        "zero gravity isolates tool/thread mechanics", "fixed tapped support",
                        "bit/socket pair contact replaced by geometry-gated reciprocal compliant torque",
                        "3.1 mm socket selected for the existing 3 mm bit; not the cart screw"],
        "runtime_pose_writes": False, "robot": "not_present"}, indent=2))
    from isaaclab.app import AppLauncher

    app = AppLauncher(headless=True, device="cpu",
                      kit_args=f"--portable-root {args.output / 'kit-runtime'}").app
    sim = None
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
        from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

        torch.set_num_threads(2)
        scene_path = args.output / "initial_scene.usda"
        stage = Usd.Stage.CreateNew(str(scene_path))
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        UsdGeom.SetStageUpAxis(stage, "Z")
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        drives, roots = [], []
        initial_housing = []

        def dynamic(prim, mass, inertia):
            UsdPhysics.RigidBodyAPI.Apply(prim)
            physics = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            physics.CreateLinearDampingAttr(0)
            physics.CreateAngularDampingAttr(0)
            physics.CreateMaxAngularVelocityAttr(1000)
            physics.CreateSolverPositionIterationCountAttr(16)
            ma = UsdPhysics.MassAPI.Apply(prim)
            ma.CreateMassAttr(mass)
            ma.CreateDiagonalInertiaAttr(Gf.Vec3f(*inertia))
            ma.CreateCenterOfMassAttr(Gf.Vec3f(0))

        def cube(path, pos, size, color=(.45, .49, .53)):
            c = UsdGeom.Cube.Define(stage, path)
            c.CreateSizeAttr(1)
            c.AddTranslateOp().Set(Gf.Vec3d(*pos))
            c.AddScaleOp().Set(Gf.Vec3f(*size))
            c.CreateDisplayColorAttr([Gf.Vec3f(*color)])
            UsdPhysics.CollisionAPI.Apply(c.GetPrim())
            return c

        for i, name in enumerate(cases):
            base = f"/World/Case_{name}"
            UsdGeom.Xform.Define(stage, base)
            ox = i * .4
            angle = 12 if name == "tilted" else 0
            orientation = Gf.Rotation(Gf.Vec3d(1, 0, 0), -90) * Gf.Rotation(Gf.Vec3d(0, 1, 0), angle)
            # quat composition follows the actual transformed TCP, not a guessed tip offset.
            quat = orientation.GetQuat()
            tip_offset = orientation.TransformDir(Gf.Vec3d(0, .130, 0))
            start_tcp = Gf.Vec3d(ox + .006, -.004, face_z + .012)
            origin = start_tcp - tip_offset
            initial_housing.append(list(origin))
            root = UsdGeom.Xform.Define(stage, base + "/Tool")
            root.GetPrim().GetReferences().AddReference(str(args.asset))
            root.AddTranslateOp(opSuffix="placement").Set(origin)
            root.AddOrientOp(opSuffix="placement").Set(Gf.Quatf(quat.GetReal(), Gf.Vec3f(*quat.GetImaginary())))
            root.GetPrim().RemoveAPI(UsdPhysics.ArticulationRootAPI)
            tool_spec = json.loads(root.GetPrim().GetAttribute("envcreator:toolSpec").Get())
            housing_path = base + "/Tool" + tool_spec["usd_paths"]["housing"]
            for label in ("anchor", "slide_x", "slide_y"):
                body = UsdGeom.Xform.Define(stage, base + "/" + label)
                body.AddTranslateOp().Set(origin)
                dynamic(body.GetPrim(), .01, (1e-5, 1e-5, 1e-5))
            fixture = UsdPhysics.FixedJoint.Define(stage, base + "/fixture")
            fixture.CreateBody1Rel().SetTargets([base + "/anchor"])
            fixture.CreateLocalPos0Attr(Gf.Vec3f(*origin))
            UsdPhysics.ArticulationRootAPI.Apply(fixture.GetPrim())
            case_drives = []
            for label, axis, parent, child in [
                ("rail_x", "X", base + "/anchor", base + "/slide_x"),
                ("rail_y", "Y", base + "/slide_x", base + "/slide_y"),
                ("rail_z", "Z", base + "/slide_y", housing_path),
            ]:
                j = UsdPhysics.PrismaticJoint.Define(stage, base + "/" + label)
                j.CreateAxisAttr(axis)
                if parent:
                    j.CreateBody0Rel().SetTargets([parent])
                else:
                    j.CreateLocalPos0Attr(Gf.Vec3f(*origin))
                    UsdPhysics.ArticulationRootAPI.Apply(j.GetPrim())
                j.CreateBody1Rel().SetTargets([child])
                if label == "rail_z":
                    inverse = quat.GetInverse()
                    j.CreateLocalRot1Attr(Gf.Quatf(inverse.GetReal(), Gf.Vec3f(*inverse.GetImaginary())))
                drive = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "linear")
                drive.CreateTypeAttr("force")
                drive.CreateStiffnessAttr(800)
                drive.CreateDampingAttr(25)
                drive.CreateMaxForceAttr(10)
                drive.CreateTargetPositionAttr(0)
                case_drives.append(drive)
            drives.append(case_drives)
            roots.append(base + "/Tool")
            # Fixed support has an actual open passage around the 4 mm shaft.
            for axis, sign in [(0, -1), (0, 1), (1, -1), (1, 1)]:
                center = [ox, 0, .790]
                center[axis] += sign * .008
                size = [.020, .020, .020]
                size[axis] = .0115
                cube(base + f"/support_{axis}_{'p' if sign > 0 else 'n'}", center, size)
            bolt = UsdGeom.Xform.Define(stage, base + "/bolt")
            bolt.AddTranslateOp().Set(Gf.Vec3d(ox, 0, face_z))
            dynamic(bolt.GetPrim(), .012, (2e-6, 2e-6, 2e-6))
            # Real hexagonal cavity geometry, with a closed bottom 2.5 mm deep.
            socket_af = .004 if name == "wrong_socket" else .0031
            inner_radius = socket_af / math.sqrt(3)
            for sector in range(6):
                a, b = sector * math.pi / 3, (sector + 1) * math.pi / 3
                xy = [(inner_radius * math.cos(a), inner_radius * math.sin(a))]
                xy += [(.004 * math.cos(a + (b-a)*j/8), .004 * math.sin(a + (b-a)*j/8)) for j in range(9)]
                xy += [(inner_radius * math.cos(b), inner_radius * math.sin(b))]
                n = len(xy)
                points = [Gf.Vec3f(x, y, z) for z in (-.0025, 0) for x, y in xy]
                faces = [list(reversed(range(n))), list(range(n, 2*n))]
                faces += [[j, (j+1) % n, (j+1) % n+n, j+n] for j in range(n)]
                mesh = UsdGeom.Mesh.Define(stage, base + f"/bolt/socket_wall_{sector}")
                mesh.CreatePointsAttr(points)
                mesh.CreateFaceVertexCountsAttr([len(f) for f in faces])
                mesh.CreateFaceVertexIndicesAttr([v for f in faces for v in f])
                mesh.CreateSubdivisionSchemeAttr("none")
                mesh.CreateDisplayColorAttr([Gf.Vec3f(.72, .56, .22)])
                UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
                UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("convexHull")
            for label, radius, height, z in [("head_bottom", .004, .0015, -.00325),
                                              ("shaft", .002, .010, -.009)]:
                c = UsdGeom.Cylinder.Define(stage, base + "/bolt/" + label)
                c.CreateRadiusAttr(radius)
                c.CreateHeightAttr(height)
                c.AddTranslateOp().Set(Gf.Vec3d(0, 0, z))
                c.CreateDisplayColorAttr([Gf.Vec3f(.72, .56, .22)])
                UsdPhysics.CollisionAPI.Apply(c.GetPrim())
            # Avoid double-counting resolved contact and the declared virtual key.
            for body in ("housing", "spindle"):
                prim = stage.GetPrimAtPath(base + "/Tool" + tool_spec["usd_paths"][body])
                UsdPhysics.FilteredPairsAPI.Apply(prim).CreateFilteredPairsRel().SetTargets([bolt.GetPath()])
        stage.GetRootLayer().Save()
        scene_hash = digest(scene_path)
        omni.usd.get_context().open_stage(str(scene_path))
        live = omni.usd.get_context().get_stage()
        live.SetEditTarget(Usd.EditTarget(live.GetSessionLayer()))
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            device="cpu", dt=dt, gravity=(0.0, 0.0, 0.0), render_interval=2000,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True)))
        tools = Articulation(ArticulationCfg(prim_path="/World/Case_.*/fixture", spawn=None, actuators={}))
        screws = RigidObject(RigidObjectCfg(prim_path="/World/Case_.*/bolt", spawn=None))
        sim.reset()
        tools.reset()
        screws.reset()
        housing_id, spindle_id = tools.body_names.index("housing"), tools.body_names.index("spindle")
        models = [fastener.ScrewModel(parameters) for _ in cases]
        couplers = [engagement.HexEngagement(engagement.EngagementParameters(
            socket_af_m=.004 if name == "wrong_socket" else .0031)) for name in cases]
        traces, events = {name: [] for name in cases}, {name: [] for name in cases}
        peak_torque = [0.0] * len(cases)
        max_residual = [0.0] * len(cases)
        engaged_steps = [0] * len(cases)
        for step in range(round(duration / dt)):
            t = step * dt
            tool_state = tools.data.body_state_w.cpu().numpy()
            screw_state = screws.data.root_state_w.cpu().numpy()
            tool_torque = np.zeros((len(cases), len(tools.body_names), 3))
            screw_force, screw_torque = [], []
            for i, name in enumerate(cases):
                s = screw_state[i]
                h, rotor = tool_state[i, housing_id], tool_state[i, spindle_id]
                tcp = rotor[:3] + engagement.rotate(rotor[3:7], tool_spec["frames"]["tcp"]["position_m"])
                state_event = models[i].observe(s[3:7], float(s[2] - face_z))
                if state_event:
                    events[name].append({"time_s": t, **state_event})
                contact = couplers[i].step(tcp, rotor[3:7], rotor[10:13], s[:3], s[3:7], s[10:13])
                if contact["event"]:
                    events[name].append({"time_s": t, "event": contact["event"]})
                engaged_steps[i] += int(contact["engaged"])
                peak_torque[i] = max(peak_torque[i], abs(contact["transmitted_Nm"]))
                tool_torque[i, spindle_id] = contact["torque_to_tool_world_Nm"]
                f, tau, metrics = models[i].forces(
                    [float(s[0] - i * .4), float(s[1]), float(s[2] - face_z)],
                    s[7:10], s[10:13], contact["transmitted_Nm"], 0.0)
                if models[i].thread_active:
                    qw, qx, qy, qz = s[3:7]
                    tau[0] = .03 * 2 * (qy*qz - qw*qx) - .001*s[10]
                    tau[1] = -.03 * 2 * (qx*qz + qw*qy) - .001*s[11]
                    max_residual[i] = max(max_residual[i], abs(metrics["coupling_residual_m"]))
                screw_force.append(f)
                screw_torque.append(tau)
                if step % 20 == 0:
                    # Translation servo follows the screw only after initial alignment.
                    ox = i * .4
                    target = np.array([ox, 0, face_z + .008]) if t < .5 else s[:3] + np.array([0, 0, -.0015])
                    if name == "offset":
                        target[0] += .004
                    if name == "no_contact":
                        target[2] = face_z + .006
                    desired_housing = target - engagement.rotate(h[3:7], (0, .130, 0))
                    for axis, drive in enumerate(drives[i]):
                        attr_path = drive.GetPrim().GetPath()
                        live.GetPrimAtPath(attr_path).GetAttribute("drive:linear:physics:targetPosition").Set(
                            float(desired_housing[axis] - initial_housing[i][axis]))
                    motor.set_motor(live, roots[i], -30 if t >= 1 and models[i].thread_active else 0,
                                    enabled=t >= 1 and name != "motor_off")
                if step % 40 == 0:
                    traces[name].append({"time_s": t, "tcp_world_m": tcp.tolist(),
                        "housing_position_m": h[:3].tolist(), "housing_quaternion_wxyz": h[3:7].tolist(),
                        "spindle_position_m": rotor[:3].tolist(), "spindle_quaternion_wxyz": rotor[3:7].tolist(),
                        "screw_position_m": s[:3].tolist(), "screw_quaternion_wxyz": s[3:7].tolist(),
                        "screw_theta_rad": models[i].theta, "screw_exit_m": float(s[2] - face_z),
                        "thread_active": models[i].thread_active, "state": models[i].state, **contact})
            tool_tensor = torch.tensor(tool_torque, dtype=torch.float32)
            tools.instantaneous_wrench_composer.set_forces_and_torques(torch.zeros_like(tool_tensor), tool_tensor, is_global=True)
            screws.instantaneous_wrench_composer.set_forces_and_torques(
                torch.tensor(screw_force, dtype=torch.float32).unsqueeze(1),
                torch.tensor(screw_torque, dtype=torch.float32).unsqueeze(1), is_global=True)
            tools.write_data_to_sim()
            screws.write_data_to_sim()
            sim.step(render=False)
            tools.update(dt)
            screws.update(dt)
        summaries = {}
        checks = {"source_unchanged": all(digest(p) == h for p, h in hashes.items()),
                  "scene_unchanged": digest(scene_path) == scene_hash}
        for i, name in enumerate(cases):
            last = traces[name][-1]
            summaries[name] = {"final_theta_rad": last["screw_theta_rad"], "exit_m": last["screw_exit_m"],
                               "thread_active": last["thread_active"], "engaged_time_s": engaged_steps[i]*dt,
                               "peak_transmitted_Nm": peak_torque[i], "max_helical_residual_m": max_residual[i],
                               "events": events[name]}
            all_numbers = [v for row in traces[name] for key in ("screw_position_m", "tcp_world_m") for v in row[key]]
            checks[name + "_finite"] = bool(np.isfinite(all_numbers).all())
            checks[name + "_reciprocal_torque"] = all(np.linalg.norm(
                np.array(row["torque_to_screw_world_Nm"]) + row["torque_to_tool_world_Nm"]) < 1e-12 for row in traces[name])
            if name == "aligned":
                checks["auto_alignment_and_insertion"] = engaged_steps[i] > 1000 and traces[name][0]["radial_error_m"] > .005
                checks["screw_turns_under_tool_motor"] = last["screw_theta_rad"] > math.tau * 4.9
                checks["thread_clear_after_five_turns"] = not last["thread_active"] and last["screw_exit_m"] >= parameters.engagement_m
                checks["helical_coupling"] = max_residual[i] < .0003
            else:
                checks[name + "_no_false_loosening"] = abs(last["screw_theta_rad"]) < .03 and abs(last["screw_exit_m"]) < .0001
                if name != "motor_off":
                    checks[name + "_no_virtual_torque"] = peak_torque[i] < 1e-10
        checks["offset_challenge_exercised"] = traces["offset"][-1]["radial_error_m"] > .003
        checks["tilt_challenge_exercised"] = traces["tilted"][-1]["axis_error_rad"] > math.radians(10)
        checks["gap_challenge_exercised"] = traces["no_contact"][-1]["insertion_m"] < -.005
        checks["size_challenge_exercised"] = "bit_socket_size_mismatch" in traces["wrong_socket"][-1]["reasons"]
        checks["motor_off_still_engages"] = engaged_steps[cases.index("motor_off")] * dt > 5
        report = {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
                  "source_hashes": hashes, "scene_sha256": scene_hash, "cases": summaries,
                  "physics": "Isaac CPU PhysX; actual exported tool; dt=0.0005 s",
                  "runtime_pose_writes": False, "robot_execution": "not_run",
                  "scope": "ideal keyed coupling + simplified helical force model; no resolved thread/bit contact",
                  "g1_preparation_allowed": all(checks.values())}
        (args.output / "trace.json").write_text(json.dumps(traces, indent=2) + "\n")
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        return 0 if report["status"] == "passed" else 2
    except BaseException:
        failure = {"status": "failed", "error": traceback.format_exc(), "source_hashes": hashes}
        (args.output / "failure.json").write_text(json.dumps(failure, indent=2))
        print(failure["error"], flush=True)
        return 2
    finally:
        if sim is not None:
            sim.clear_all_callbacks()
            sim.clear_instance()
            sim.stop()
        app.close(wait_for_replicator=False)


if __name__ == "__main__":
    raise SystemExit(main())
