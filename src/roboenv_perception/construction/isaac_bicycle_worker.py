"""Adapt, simulate and independently reload the bicycle/observed-ground scene.

No controller writes joint positions during measured phases. External equal and
opposite body torques exercise the joints. All physical parameters are priors.
"""

import argparse
import json
import math
import shutil
import subprocess
import traceback
from pathlib import Path


def adapt_bicycle(source, destination):
    from pxr import PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    shutil.copytree(source.parent, destination.parent, dirs_exist_ok=True)
    stage = Usd.Stage.Open(str(destination))
    root = stage.GetDefaultPrim()
    prefix = str(root.GetPath())
    cache = UsdGeom.XformCache()
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    matrices = {str(p.GetPath()): cache.GetLocalToWorldTransform(p) for p in bodies}
    mappings = {str(p.GetPath()): prefix + "/" + p.GetName() for p in bodies}
    for old in sorted(mappings, key=len, reverse=True):
        new = mappings[old]
        if old == new:
            continue
        Sdf.CopySpec(stage.GetRootLayer(), old, stage.GetRootLayer(), new)
        stage.RemovePrim(old)
        xf = UsdGeom.Xformable(stage.GetPrimAtPath(new))
        xf.ClearXformOpOrder()
        xf.AddTransformOp().Set(matrices[old])
    for prim in stage.Traverse():
        for rel in prim.GetRelationships():
            values = []
            for target in rel.GetTargets():
                value = str(target)
                for old in sorted(mappings, key=len, reverse=True):
                    if value == old or value.startswith(old + "/"):
                        value = mappings[old] + value[len(old) :]
                        break
                values.append(Sdf.Path(value))
            if values:
                rel.SetTargets(values)
    contact = UsdShade.Material.Define(stage, prefix + "/Looks/Contact")
    api = UsdPhysics.MaterialAPI.Apply(contact.GetPrim())
    api.CreateStaticFrictionAttr(0.8)
    api.CreateDynamicFrictionAttr(0.65)
    api.CreateRestitutionAttr(0)
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdGeom.Imageable(prim).MakeInvisible()
            props = PhysxSchema.PhysxCollisionAPI.Apply(prim)
            props.CreateContactOffsetAttr(0.001)
            props.CreateRestOffsetAttr(0)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(contact, materialPurpose="physics")
        elif prim.GetName().startswith("vis_"):
            UsdGeom.Imageable(prim).CreatePurposeAttr(UsdGeom.Tokens.default_)
            if prim.IsA(UsdGeom.Mesh):
                UsdGeom.Mesh(prim).CreateSubdivisionSchemeAttr("none")
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            props = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            props.CreateSolverPositionIterationCountAttr(32)
            props.CreateSolverVelocityIterationCountAttr(8)
            props.CreateMaxDepenetrationVelocityAttr(1)
            props.CreateLinearDampingAttr(0.02)
            props.CreateAngularDampingAttr(0.02)
        if prim.IsA(UsdPhysics.RevoluteJoint):
            drive = UsdPhysics.DriveAPI.Get(prim, "angular")
            if drive:
                drive.CreateStiffnessAttr(0)
                drive.CreateDampingAttr(0)
                drive.CreateMaxForceAttr(0)
            PhysxSchema.PhysxJointAPI.Apply(prim).CreateJointFrictionAttr(0)
            prim.AddAppliedSchema("PhysxJointAxisAPI:angular")
            for key, value in [
                ("staticFrictionEffort", 0.01),
                ("dynamicFrictionEffort", 0.008),
                ("viscousFrictionCoefficient", 0.03 * math.pi / 180),
            ]:
                prim.CreateAttribute("physxJointAxis:angular:" + key, Sdf.ValueTypeNames.Float).Set(
                    value
                )
    # Adjacent shaft/bores are explicitly clearanced in Blender. Keep the full
    # articulation self-collision enabled to expose interference during tests.
    PhysxSchema.PhysxArticulationAPI.Apply(root).CreateEnabledSelfCollisionsAttr(True)
    manifest_path = destination.parent / "drivetrain/manifest.json"
    if manifest_path.is_file():
        from pxr import Gf

        manifest = json.loads(manifest_path.read_text())
        for entry in manifest["visual_only_meshes"]:
            vertices, faces = [], []
            for line in (manifest_path.parent / entry["file"]).read_text().splitlines():
                if line.startswith("v "):
                    vertices.append(tuple(map(float, line.split()[1:])))
                elif line.startswith("f "):
                    faces.append([int(v.split("/")[0]) - 1 for v in line.split()[1:]])
            path = prefix + "/" + entry["part"] + "/" + entry["name"]
            mesh = UsdGeom.Mesh.Define(stage, path)
            mesh.CreatePointsAttr(vertices)
            mesh.CreateFaceVertexCountsAttr([len(f) for f in faces])
            mesh.CreateFaceVertexIndicesAttr([v for f in faces for v in f])
            mesh.CreateSubdivisionSchemeAttr("none")
            mesh.CreateDisplayColorAttr([Gf.Vec3f(*entry["rgba"][:3])])
            mesh.GetPrim().SetCustomDataByKey(
                "role",
                "Visual envelope; native joint constraint owns transmission, no chain-link collision",
            )
        root.SetCustomDataByKey("drivetrain", "ideal_fixed_gear_ratio_2.5_no_freewheel")
        # This PhysX version rejects unlimited revolute mimic joints. Bound
        # both shafts explicitly (USD angles are degrees), far beyond this test.
        for prim in stage.Traverse():
            if prim.GetName() in {"joint_pedal_spin", "joint_rear_spin"}:
                joint = UsdPhysics.RevoluteJoint(prim)
                joint.CreateLowerLimitAttr(-math.degrees(1000))
                joint.CreateUpperLimitAttr(math.degrees(1000))
                prim.SetCustomDataByKey("runtime_angle_bound_rad", 1000.0)
    stage.GetRootLayer().Save()
    return {
        "rigid_bodies": len(bodies),
        "joints": sum(p.IsA(UsdPhysics.RevoluteJoint) for p in stage.Traverse()),
        "body_paths": mappings,
        "passive_friction_Nm": 0.01,
        "viscous_damping_Nm_s_per_rad": 0.03,
    }


def author_ground(stage, texture):
    from pxr import Gf, PhysxSchema, Sdf, UsdGeom, UsdPhysics, UsdShade

    mesh = UsdGeom.Mesh.Define(stage, "/World/Ground/Surface")
    mesh.CreatePointsAttr([(-1.8, -1.5, 0), (1.8, -1.5, 0), (1.8, 1.5, 0), (-1.8, 1.5, 0)])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateSubdivisionSchemeAttr("none")
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex").Set(
        [(0, 0), (1, 0), (1, 1), (0, 1)]
    )
    mat = UsdShade.Material.Define(stage, "/World/Ground/Looks/Brick")
    surf = UsdShade.Shader.Define(stage, str(mat.GetPath()) + "/Surface")
    surf.CreateIdAttr("UsdPreviewSurface")
    surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
    tex = UsdShade.Shader.Define(stage, str(mat.GetPath()) + "/Texture")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(str(texture)))
    tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
    uv = UsdShade.Shader.Define(stage, str(mat.GetPath()) + "/UV")
    uv.CreateIdAttr("UsdPrimvarReader_float2")
    uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(), "result")
    surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        tex.ConnectableAPI(), "rgb"
    )
    mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
    ground = UsdGeom.Cube.Define(stage, "/World/Ground/Collision")
    ground.CreateSizeAttr(1)
    ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.04))
    ground.AddScaleOp().Set(Gf.Vec3f(3.6, 3, 0.08))
    ground.MakeInvisible()
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    props = PhysxSchema.PhysxCollisionAPI.Apply(ground.GetPrim())
    props.CreateContactOffsetAttr(0.001)
    props.CreateRestOffsetAttr(0)
    contact = UsdShade.Material.Define(stage, "/World/Ground/Looks/Contact")
    api = UsdPhysics.MaterialAPI.Apply(contact.GetPrim())
    api.CreateStaticFrictionAttr(0.8)
    api.CreateDynamicFrictionAttr(0.65)
    api.CreateRestitutionAttr(0)
    UsdShade.MaterialBindingAPI.Apply(ground.GetPrim()).Bind(contact, materialPurpose="physics")


def run(args):
    from isaaclab.app import AppLauncher

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = AppLauncher(
        headless=True,
        enable_cameras=True,
        device="cpu",
        kit_args=f"--portable-root {output.parent / 'kit-runtime'}",
    ).app
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import Camera, CameraCfg, ContactSensor, ContactSensorCfg
        from PIL import Image, ImageDraw
        from pxr import Gf, UsdGeom

        torch.set_num_threads(4)
        dt = 1 / 240
        if args.mode == "check":
            if not omni.usd.get_context().open_stage(str(args.input.resolve() / "scene.usda")):
                raise RuntimeError("Cannot reload scene.usda")
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
            adaptation = adapt_bicycle(
                args.input.resolve() / "export/object.usd", output / "assets/bicycle/object.usd"
            )
            for name in ["ground-observed.png", "ground-observed-mask.png", "alignment.json"]:
                shutil.copyfile(args.ground.resolve() / name, output / name)
            author_ground(sim.stage, output / "ground-observed.png")
            cfg = sim_utils.UsdFileCfg(
                usd_path=str(output / "assets/bicycle/object.usd"), activate_contact_sensors=True
            )
            cfg.func("/World/Bicycle", cfg, translation=(0, 0, 0.015))
            light = sim_utils.DomeLightCfg(intensity=1100)
            light.func("/World/Light", light)
            # The modeled stand touches the ground beside the two wheels. A
            # small initial lean makes its contact loaded; no fixed base.
            xf = UsdGeom.Xformable(sim.stage.GetPrimAtPath("/World/Bicycle"))
            xf.AddOrientOp(opSuffix="parking").Set(
                Gf.Quatf(math.cos(0.18 / 2), Gf.Vec3f(math.sin(0.18 / 2), 0, 0))
            )
        else:
            adaptation = {"source_scene": str(args.input.resolve() / "scene.usda")}
        bicycle = Articulation(
            ArticulationCfg(prim_path="/World/Bicycle", spawn=None, actuators={})
        )
        contact = ContactSensor(ContactSensorCfg(prim_path="/World/Bicycle/.*", update_period=0))
        camera_path = "/World/ReloadCamera" if args.mode == "check" else "/World/InspectionCamera"
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
        sim.set_camera_view([2.1, -2.8, 1.9], [0, 0, 0.5])
        sim.reset()
        camera.set_world_poses_from_view(
            torch.tensor([[2.1, -2.8, 1.9]]), torch.tensor([[0.0, 0.0, 0.5]])
        )
        body_ids = {name: bicycle.body_names.index(name) for name in bicycle.body_names}
        rows = []
        zero = torch.zeros((1, bicycle.num_bodies, 3))

        def step(forces=None, torques=None, render=False):
            bicycle.set_external_force_and_torque(
                zero if forces is None else forces,
                zero if torques is None else torques,
                is_global=False,
            )
            bicycle.write_data_to_sim()
            sim.step(render=render)
            bicycle.update(dt)
            contact.update(dt)
            row = {
                "t": (len(rows) + 1) * dt,
                "root": bicycle.data.root_state_w[0].cpu().numpy().tolist(),
                "q": bicycle.data.joint_pos[0].cpu().numpy().tolist(),
                "qd": bicycle.data.joint_vel[0].cpu().numpy().tolist(),
                "contact_N": contact.data.net_forces_w[0].cpu().numpy().tolist(),
            }
            rows.append(row)
            return row

        def capture(label):
            for _ in range(2):
                sim.render()
            camera.update(dt, force_recompute=True)
            im = Image.fromarray(
                camera.data.output["rgb"][0, ..., :3].cpu().numpy().astype("uint8")
            )
            draw = ImageDraw.Draw(im)
            draw.rectangle((0, 0, 960, 34), fill=(20, 25, 30))
            draw.text((12, 10), label, fill="white")
            return im

        for _ in range(720):
            step()
        capture("Bicycle + observed ground | gravity/contact settling | estimated scale").save(
            output / "preview.png"
        )
        settled = bicycle.data.root_state_w.clone()
        settled[:, 7:] = 0
        settled_q = bicycle.data.joint_pos.clone()
        initial_body = bicycle.data.body_pos_w[0].cpu().numpy().copy()
        checks = {
            "five_rigid_bodies": bicycle.num_bodies == 5,
            "four_joints": bicycle.num_joints == 4,
            "finite_settle_state": bool(np.isfinite(rows[-1]["root"] + rows[-1]["q"]).all()),
            "settled_linear_speed_below_005_m_s": float(
                torch.linalg.norm(bicycle.data.root_lin_vel_w)
            )
            < 0.05,
            "wheel_centres_above_ground": bool(
                np.min(initial_body[[body_ids["rear_wheel"], body_ids["front_wheel"]], 2]) > 0.24
            ),
            "ground_contact_detected": float(
                np.linalg.norm(np.array(rows[-1]["contact_N"]), axis=1).sum()
            )
            > 10,
        }
        results = {}
        if args.mode == "build":
            # Export the physical authored scene before diagnostic resets/forces.
            portable = sim.stage.GetRootLayer().ExportToString()
            portable = portable.replace(
                str(output / "assets/bicycle/object.usd"), "./assets/bicycle/object.usd"
            )
            portable = portable.replace(
                str(output / "ground-observed.png"), "./ground-observed.png"
            )
            (output / "scene.usda").write_text(portable)
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
                "21",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output / "interaction.mp4"),
            ],
            stdin=subprocess.PIPE,
        )
        try:
            protocols = (
                [
                    ("rear_spin", "rear_wheel", "frame", [0, 1, 0], 12.0),
                    ("front_spin", "front_wheel", "steering", [0, 1, 0], 12.0),
                    ("steer", "steering", "frame", [-0.318, 0, 0.948], 5.0),
                    ("pedal_spin", "crank", "frame", [0, 1, 0], 2.0),
                ]
                if args.mode == "build"
                else []
            )
            for joint, child, parent, axis, effort in protocols:
                jid = next(i for i, n in enumerate(bicycle.joint_names) if n.endswith(joint))
                # First record an equal-duration zero-torque baseline from the
                # same settled state, then repeat with the applied body torque.
                bicycle.write_root_state_to_sim(settled.clone())
                bicycle.write_joint_state_to_sim(settled_q.clone(), torch.zeros_like(settled_q))
                bicycle.reset()
                for _ in range(60):
                    step()
                baseline = [step()["q"][jid] for _ in range(360)]
                bicycle.write_root_state_to_sim(settled.clone())
                bicycle.write_joint_state_to_sim(settled_q.clone(), torch.zeros_like(settled_q))
                bicycle.reset()
                for _ in range(60):
                    step()
                q0 = float(bicycle.data.joint_pos[0, jid])
                trace = []
                for k in range(360):
                    torque = effort if k < 120 else 0
                    torques = zero.clone()
                    torques[0, body_ids[child]] = torch.tensor(axis) * torque
                    torques[0, body_ids[parent]] = -torch.tensor(axis) * torque
                    row = step(torques=torques, render=k % 8 == 0)
                    trace.append(
                        {
                            "t": (k + 1) * dt,
                            "q": row["q"][jid],
                            "qd": row["qd"][jid],
                            "applied_Nm": torque,
                        }
                    )
                    if k % 8 == 0:
                        video.stdin.write(
                            capture(
                                f"{joint} | torque {torque:.2f} Nm | q {row['q'][jid]:.3f} rad"
                            ).tobytes()
                        )
                excursion = max(abs(r["q"] - q0) for r in trace)
                effect = max(abs(r["q"] - b) for r, b in zip(trace, baseline, strict=True))
                results[joint] = {
                    "initial_rad": q0,
                    "excursion_rad": excursion,
                    "max_difference_from_zero_torque_rad": effect,
                    "samples": trace,
                    "zero_torque_baseline_rad": baseline,
                }
                checks[joint + "_responds_to_torque"] = excursion > 0.025 and effect > 0.025
                if joint == "steer":
                    checks["steering_within_limits"] = max(abs(r["q"]) for r in trace) < 0.68
            # Orbit of a fresh settled pose, distinct from the recorded response.
            bicycle.write_root_state_to_sim(settled.clone())
            bicycle.write_joint_state_to_sim(settled_q.clone(), torch.zeros_like(settled_q))
            bicycle.reset()
            for k in range(90):
                a = 2 * math.pi * k / 90
                camera.set_world_poses_from_view(
                    torch.tensor([[2.7 * math.cos(a), 2.7 * math.sin(a), 1.65]]),
                    torch.tensor([[0.0, 0.0, 0.5]]),
                )
                for _ in range(8):
                    step(render=False)
                video.stdin.write(
                    capture(
                        "Orbit after settling | real PhysX scene | dimensions estimated"
                    ).tobytes()
                )
        finally:
            video.stdin.close()
            if video.wait(timeout=30):
                raise RuntimeError("Video encoding failed")
        checks["finite_all_states"] = all(
            np.isfinite(r["root"] + r["q"] + r["qd"]).all() for r in rows
        )
        report = {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "adaptation": adaptation,
            "body_names": bicycle.body_names,
            "joint_names": bicycle.joint_names,
            "settled_root_state": settled[0].tolist(),
            "settled_body_positions": initial_body.tolist(),
            "joint_tests": results,
            "dt_seconds": dt,
            "simulation": "CPU PhysX, free base, gravity, self collision enabled",
            "limitations": [
                "Mass, friction, damping and wheel radius are assumptions.",
                "No chain transmission or brake dynamics. Stand is fixed deployed.",
                "Joint responses tested with external body torques; this is not a riding controller.",
            ],
        }
        (output / "trajectory.json").write_text(json.dumps(rows) + "\n")
        (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": report["status"], "checks": checks}), flush=True)
        return 0 if all(checks.values()) else 2
    except BaseException:
        (output / "failure.json").write_text(
            json.dumps({"traceback": traceback.format_exc()}, indent=2) + "\n"
        )
        raise
    finally:
        app.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["build", "check"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ground", type=Path)
    args = parser.parse_args()
    if args.mode == "build" and args.ground is None:
        parser.error("build requires --ground")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
