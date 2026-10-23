"""Standalone Isaac Lab worker: author USD, import it, and measure tabletop contacts.

Run in a separate Isaac Sim Python environment. Import Kit before USD/Isaac modules.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def export_asset(output, geometry, spec):
    import numpy as np
    from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    path = output / "asset/table.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Table")
    stage.SetDefaultPrim(root.GetPrim())
    root.GetPrim().SetCustomDataByKey("scaleSource", spec["width_source"])
    root.GetPrim().SetCustomDataByKey("heightSource", spec["height_source"])
    material = UsdShade.Material.Define(stage, "/Table/Looks/Wood")
    surface = UsdShade.Shader.Define(stage, "/Table/Looks/Wood/Surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    surface.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.65)
    surface.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0)
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    texture = UsdShade.Shader.Define(stage, "/Table/Looks/Wood/Texture")
    texture.CreateIdAttr("UsdUVTexture")
    texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("./table.png"))
    texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
    texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
    uv_reader = UsdShade.Shader.Define(stage, "/Table/Looks/Wood/UV")
    uv_reader.CreateIdAttr("UsdPrimvarReader_float2")
    uv_reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_reader.ConnectableAPI(), "result")
    surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.ConnectableAPI(), "rgb")

    top = UsdGeom.Mesh.Define(stage, "/Table/Visual/Top")
    vertices = np.asarray(geometry["vertices_local_m"])
    indices = [0, 1, 2, 3]
    if np.cross(vertices[1]-vertices[0], vertices[2]-vertices[0])[2] < 0:
        indices.reverse()
    top.CreatePointsAttr([Gf.Vec3f(*p) for p in vertices])
    top.CreateFaceVertexCountsAttr([4])
    top.CreateFaceVertexIndicesAttr(indices)
    top.CreateSubdivisionSchemeAttr("none")
    top.CreateDoubleSidedAttr(True)
    UsdGeom.PrimvarsAPI(top).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex").Set(
        [Gf.Vec2f(*p) for p in geometry["uv"]]
    )
    UsdShade.MaterialBindingAPI.Apply(top.GetPrim()).Bind(material)

    physics = UsdShade.Material.Define(stage, "/Table/Looks/Contact")
    api = UsdPhysics.MaterialAPI.Apply(physics.GetPrim())
    api.CreateStaticFrictionAttr(spec["static_friction"])
    api.CreateDynamicFrictionAttr(spec["dynamic_friction"])
    api.CreateRestitutionAttr(spec["restitution"])
    physx_material = PhysxSchema.PhysxMaterialAPI.Apply(physics.GetPrim())
    physx_material.CreateFrictionCombineModeAttr("average")
    physx_material.CreateRestitutionCombineModeAttr("average")

    def cube(name, size, position, visible=True, collision=True):
        shape = UsdGeom.Cube.Define(stage, f"/Table/{name}")
        shape.CreateSizeAttr(1)
        shape.AddTranslateOp().Set(Gf.Vec3d(*position))
        shape.AddScaleOp().Set(Gf.Vec3f(*size))
        shape.CreateDisplayColorAttr([Gf.Vec3f(0.24, 0.16, 0.10)])
        if not visible:
            shape.MakeInvisible()
        if collision:
            UsdPhysics.CollisionAPI.Apply(shape.GetPrim()).CreateCollisionEnabledAttr(True)
            contact = PhysxSchema.PhysxCollisionAPI.Apply(shape.GetPrim())
            contact.CreateContactOffsetAttr(spec["contact_offset_m"])
            contact.CreateRestOffsetAttr(spec["rest_offset_m"])
            UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(physics, materialPurpose="physics")
        return shape

    depth, width, thick = geometry["collision_size_m"]
    cube("Collision/Top", [depth, width, thick], [0, 0, -thick/2], visible=False)
    # Four thin visual side strips leave the UV top unobstructed and avoid z-fighting.
    for sign in [-1, 1]:
        cube(f"Visual/SideX{sign+1}", [0.002, width, thick], [sign*(depth/2-0.001), 0, -thick/2], collision=False)
        cube(f"Visual/SideY{sign+1}", [depth-0.004, 0.002, thick], [0, sign*(width/2-0.001), -thick/2], collision=False)
    leg_height = geometry["top_height_m"]-thick
    for i, (sx, sy) in enumerate([(-1, -1), (-1, 1), (1, -1), (1, 1)]):
        cube(f"Legs/Leg{i}", [0.04, 0.04, leg_height],
             [sx*(depth/2-0.055), sy*(width/2-0.055), -thick-leg_height/2])
    stage.GetRootLayer().Save()
    # Check the exported file rather than relying on construction intentions.
    check = Usd.Stage.Open(str(path))
    collider = check.GetPrimAtPath("/Table/Collision/Top")
    assert collider.HasAPI(UsdPhysics.CollisionAPI)
    assert not any(p.HasAPI(UsdPhysics.RigidBodyAPI) for p in check.Traverse())
    return path


def export_g1_reference(reference, output):
    """A static visual reference at a URDF-derived pose, with no robot physics/controller."""
    import numpy as np
    from pxr import Gf, Usd, UsdGeom, Vt

    data = json.loads(reference.read_text())
    path = output / "asset/g1-reference.usdc"
    stage = Usd.Stage.CreateNew(str(path))
    root = UsdGeom.Xform.Define(stage, "/G1Reference")
    root.GetPrim().SetCustomDataByKey("role", "Static URDF visual reference; no robot physics or controller")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    bounds = []
    dtype = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    for entry in data["visuals"]:
        content = (reference.parent / "references" / entry["mesh_ref"]).read_bytes()
        triangles = int.from_bytes(content[80:84], "little")
        if len(content) != 84+50*triangles:
            raise ValueError("reference mesh must be a binary STL")
        points = np.frombuffer(content, dtype=dtype, offset=84)["vertices"].reshape(-1, 3).copy()
        mesh = UsdGeom.Mesh.Define(stage, "/G1Reference/"+entry["link"])
        mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(triangles, 3, np.int32)))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(np.arange(len(points), dtype=np.int32)))
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateDisplayColorAttr([Gf.Vec3f(*entry["color"])])
        transform = np.asarray(entry["world_transform"])
        mesh.AddTransformOp().Set(Gf.Matrix4d(transform.T))
        world = points @ transform[:3, :3].T+transform[:3, 3]
        bounds.append(np.stack([world.min(axis=0), world.max(axis=0)]))
    stage.GetRootLayer().Save()
    extent = np.stack(bounds)
    report = {"world_min_m": extent[:, 0].min(axis=0).tolist(), "world_max_m": extent[:, 1].max(axis=0).tolist(),
              "role": "Static visual reference only; contact tests do not validate G1 control"}
    (output / "g1-visual-reference.json").write_text(json.dumps(report, indent=2)+"\n")
    return path


def run(job, output, gui=False, g1_reference=None):
    from isaaclab.app import AppLauncher

    runtime = output.parent / "kit-runtime"
    runtime.mkdir(exist_ok=True)
    launcher = AppLauncher(
        headless=not gui, enable_cameras=True, device="cpu",
        kit_args=f"--portable-root {runtime}",
    )
    app = launcher.app
    exit_code = 1
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import torch
        from isaaclab.assets import RigidObject, RigidObjectCfg
        from isaaclab.sensors import Camera, CameraCfg, ContactSensor, ContactSensorCfg
        from PIL import Image, ImageDraw
        from pxr import Usd, UsdPhysics

        from roboenv_perception.construction.common import source_identity, verify_run

        torch.set_num_threads(4)
        verify_run(job, "job.json")
        spec = json.loads((job / "spec.json").read_text())
        geometry = json.loads((job / "geometry.json").read_text())
        output.mkdir(parents=True, exist_ok=False)
        (output / "asset").mkdir()
        shutil.copyfile(job / "asset/table.png", output / "asset/table.png")
        asset_path = export_asset(output, geometry, spec)
        dt = spec["dt_seconds"]
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            device="cpu", dt=dt, render_interval=8, enable_scene_query_support=True,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True),
        ))
        table = sim_utils.UsdFileCfg(usd_path=str(asset_path))
        table.func("/World/Table", table, translation=tuple(geometry["position_world_m"]))
        g1_path = None
        if g1_reference:
            g1_path = export_g1_reference(g1_reference, output)
            visual = sim_utils.UsdFileCfg(usd_path=str(g1_path))
            visual.func("/World/G1Reference", visual)
        floor = sim_utils.CuboidCfg(
            size=(4, 4, 0.05), collision_props=sim_utils.CollisionPropertiesCfg(collision_enabled=True),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.18, 0.20, 0.23)),
        )
        floor.func("/World/Floor", floor, translation=(0, 0, -0.025))
        light = sim_utils.DomeLightCfg(intensity=1600, color=(0.9, 0.94, 1.0))
        light.func("/World/Light", light)
        material = sim_utils.RigidBodyMaterialCfg(
            static_friction=spec["static_friction"], dynamic_friction=spec["dynamic_friction"],
            restitution=spec["restitution"], friction_combine_mode="average",
        )
        probes = []
        half = 0.02
        positions = []
        for i, (x, y) in enumerate((x, y) for x in [-0.8, 0, 0.8] for y in [-0.8, 0, 0.8]):
            position = [geometry["position_world_m"][0]+x*geometry["depth_m"]/2,
                        y*geometry["width_m"]/2, geometry["top_height_m"]+half+0.08]
            positions.append(position)
            probe = RigidObject(RigidObjectCfg(
                prim_path=f"/World/Probe_{i}",
                spawn=sim_utils.CuboidCfg(
                    size=(2*half,)*3, mass_props=sim_utils.MassPropertiesCfg(mass=0.1),
                    rigid_props=sim_utils.RigidBodyPropertiesCfg(
                        solver_position_iteration_count=spec["solver_position_iterations"],
                        solver_velocity_iteration_count=spec["solver_velocity_iterations"],
                        linear_damping=0, angular_damping=0, max_depenetration_velocity=1,
                    ),
                    collision_props=sim_utils.CollisionPropertiesCfg(
                        collision_enabled=True, contact_offset=spec["contact_offset_m"],
                        rest_offset=spec["rest_offset_m"],
                    ),
                    physics_material=material, activate_contact_sensors=True,
                    visual_material=sim_utils.PreviewSurfaceCfg(
                        diffuse_color=(0.85, 0.22, 0.12) if i == 4 else (0.12, 0.48, 0.82),
                    ),
                ), init_state=RigidObjectCfg.InitialStateCfg(pos=tuple(position)),
            ))
            probes.append(probe)
        contacts = ContactSensor(ContactSensorCfg(prim_path="/World/Probe_.*", update_period=0))
        camera = Camera(CameraCfg(
            prim_path="/World/Overview", width=960, height=720, data_types=["rgb"], update_period=0,
            spawn=sim_utils.PinholeCameraCfg(focal_length=24, horizontal_aperture=28, clipping_range=(0.05, 100)),
        ))
        eye = [1.8, 1.6, 1.65] if g1_reference else [1.65, 1.35, 1.65]
        target = [0.35, 0, 0.70] if g1_reference else [0.55, 0, 0.65]
        sim.set_camera_view(eye, target)
        # Export a portable scene with the probes still above the table, ready to play.
        text = sim.stage.GetRootLayer().ExportToString().replace(str(asset_path), "./asset/table.usda")
        if g1_path:
            text = text.replace(str(g1_path), "./asset/g1-reference.usdc")
        (output / "scene.usda").write_text(text)
        sim.reset()
        for probe in probes:
            probe.reset()
        camera.set_world_poses_from_view(torch.tensor([eye]), torch.tensor([target]))
        for _ in range(8):
            sim.render()
        video = subprocess.Popen([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", "960x720", "-r", "30", "-i", "pipe:0", "-an", "-c:v", "h264_nvenc",
            "-preset", "p5", "-cq", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(output / "contact-preview.mp4"),
        ], stdin=subprocess.PIPE)
        rows = []
        low_start = low_end = high_start = high_end = None
        try:
            for step in range(round(4/dt)):
                time = step*dt
                force = 0.3*0.1*9.81 if 2 <= time < 2.5 else (0.9*0.1*9.81 if 2.5 <= time < 2.65 else 0.0)
                probes[4].set_external_force_and_torque(
                    torch.tensor([[[force, 0, 0]]], dtype=torch.float32), torch.zeros((1, 1, 3)), is_global=True,
                )
                for probe in probes:
                    probe.write_data_to_sim()
                render = step % 8 == 0
                sim.step(render=render)
                for probe in probes:
                    probe.update(dt)
                contacts.update(dt)
                states = np.stack([p.data.root_state_w[0].cpu().numpy() for p in probes])
                fz = contacts.data.net_forces_w[0, :, 2].cpu().numpy()
                rows.append({"t": time+dt, "states": states.tolist(), "contact_fz": fz.tolist(), "push_N": force})
                if step == round(2/dt)-1:
                    low_start = states[4, :3].copy()
                if step == round(2.5/dt)-1:
                    low_end = high_start = states[4, :3].copy()
                if step == round(2.65/dt)-1:
                    high_end = states[4, :3].copy()
                if render:
                    camera.update(dt*8, force_recompute=True)
                    rgb = camera.data.output["rgb"][0, ..., :3].cpu().numpy().astype(np.uint8)
                    image = Image.fromarray(rgb)
                    draw = ImageDraw.Draw(image)
                    phase = "DROP / SETTLE" if time < 2 else ("LOW FORCE / HOLD" if time < 2.5 else ("HIGH FORCE / SLIDE" if time < 2.65 else "SETTLE AGAIN"))
                    draw.rectangle((0, 0, 960, 40), fill=(28, 31, 38))
                    draw.text((14, 13), f"Isaac Lab | {phase} | t={time:.2f}s | table top={geometry['top_height_m']:.2f}m", fill="white")
                    video.stdin.write(image.tobytes())
                    if step in [0, round(1.5/dt), round(3.5/dt)]:
                        image.save(output / f"frame-{step:04d}.png")
                if step % 240 == 0:
                    print(f"STEP {step} / {round(4/dt)}", flush=True)
        finally:
            video.stdin.close()
            if video.wait(timeout=60):
                raise RuntimeError("video encoder failed")
        last = np.asarray([r["states"] for r in rows[-120:]])
        final = last[-1]
        # The vertical support extent of a rotated cube is h*(|Rzx|+|Rzy|+|Rzz|).
        w, x, y, z = final[:, 3:7].T
        extent = half*(np.abs(2*(x*z-w*y))+np.abs(2*(y*z+w*x))+np.abs(1-2*(x*x+y*y)))
        bottom_error = final[:, 2]-extent-geometry["top_height_m"]
        final_fz = np.asarray([r["contact_fz"] for r in rows[-120:]]).mean(axis=0)
        low_drift = float(np.linalg.norm(low_end[:2]-low_start[:2]))
        high_motion = float(np.linalg.norm(high_end[:2]-high_start[:2]))
        exported_stage = Usd.Stage.Open(str(asset_path))
        checks = {
            "all_states_finite": bool(np.isfinite(np.asarray([r["states"] for r in rows])).all()),
            "all_nine_bottoms_on_table_within_2mm": bool((np.abs(bottom_error) < 0.002).all()),
            "all_nine_have_persistent_contact": bool((final_fz > 0.5*0.1*9.81).all()),
            "all_nine_settled_linear_speed_below_1cm_s": bool((np.linalg.norm(final[:, 7:10], axis=1) < 0.01).all()),
            "subthreshold_push_drift_below_2mm": low_drift < 0.002,
            "superthreshold_push_slides_over_5mm": high_motion > 0.005,
            "static_table_has_no_rigid_body_or_mass": not any(
                p.HasAPI(UsdPhysics.RigidBodyAPI) for p in exported_stage.Traverse()
            ),
        }
        result = {
            "status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "bottom_error_m": bottom_error.tolist(), "mean_final_contact_fz_N": final_fz.tolist(),
            "low_force_displacement_m": low_drift, "high_force_displacement_m": high_motion,
            "final_linear_speed_m_s": np.linalg.norm(final[:, 7:10], axis=1).tolist(),
            "parameters": spec, "geometry": geometry, "job_source": source_identity(job),
            "probe_mass_kg": 0.1, "probe_size_m": 0.04,
            "simulator": "Isaac Sim 5.1 / Isaac Lab 2.3.2 / CPU PhysX TGS; RTX rendering",
            "scope": "Configured collider contact sanity; no real-world calibration or robot controller validation",
            "independent_review": False, "real_world_accuracy": None,
            "g1_role": "static visual reference" if g1_reference else "not_spawned",
            "scene_ref": "scene.usda", "asset_ref": "asset/table.usda",
        }
        (output / "trajectory.json").write_text(json.dumps(rows)+"\n")
        (output / "validation.json").write_text(json.dumps(result, indent=2)+"\n")
        print("TABLE_CONTACT_RESULT", json.dumps({"status": result["status"], "checks": checks}), flush=True)
        exit_code = 0 if result["status"] == "passed" else 2
        if gui:
            while app.is_running():
                sim.step()
    except BaseException:
        import traceback
        traceback.print_exc()
        if output.is_dir():
            (output / "failure.json").write_text(json.dumps({
                "status": "failed", "traceback": traceback.format_exc(),
            }, indent=2)+"\n")
    finally:
        # Replicator may stay in a running state even after the explicit video writer ends.
        # Flush all our outputs before asking Kit to stop without waiting on that orchestrator.
        sys.stdout.flush()
        sys.stderr.flush()
        app.close(wait_for_replicator=False)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--g1-reference", type=Path)
    args = parser.parse_args()
    return run(args.input.resolve(), args.output.resolve(), args.gui, args.g1_reference)


if __name__ == "__main__":
    raise SystemExit(main())
