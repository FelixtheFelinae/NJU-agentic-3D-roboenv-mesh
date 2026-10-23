"""Build and independently reload the tablet/audio-headphones tabletop scene."""

import argparse
import hashlib
import json
import math
import shutil
import sys
import traceback
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quaternion(yaw):
    return [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


def table_geometry(spec):
    width, depth, height, thickness = (
        spec["width_m"],
        spec["depth_m"],
        spec["top_height_m"],
        spec["thickness_m"],
    )
    return {
        "vertices_local_m": [
            [-width / 2, -depth / 2, 0],
            [width / 2, -depth / 2, 0],
            [width / 2, depth / 2, 0],
            [-width / 2, depth / 2, 0],
        ],
        "uv": [[0, 1], [1, 1], [1, 0], [0, 0]],
        "width_m": width,
        "depth_m": depth,
        "top_height_m": height,
        "thickness_m": thickness,
        "position_world_m": [0, 0, height],
        "collision_size_m": [depth, width, thickness],
        "collision_center_local_m": [0, 0, -thickness / 2],
    }


def table_spec():
    return {
        "width_m": 0.80,
        "width_source": "estimated",
        "width_evidence": "iPad Pro 12.9-inch 2021 user confirmation and video proportions.",
        "top_height_m": 0.75,
        "height_source": "task_design",
        "thickness_m": 0.03,
        "static_friction": 0.6,
        "dynamic_friction": 0.5,
        "restitution": 0,
        "contact_offset_m": 0.002,
        "rest_offset_m": 0,
        "dt_seconds": 1 / 240,
        "solver_position_iterations": 8,
        "solver_velocity_iterations": 2,
        "texture": "simple",
        "robot": "unitree_g1",
    }


def run_build(root, output):
    from isaaclab.app import AppLauncher

    launcher = AppLauncher(
        headless=True,
        enable_cameras=True,
        device="cpu",
        kit_args=f"--portable-root {output.parent / 'kit-runtime'}",
    )
    app = launcher.app
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
        from pxr import Gf, PhysxSchema, Usd, UsdGeom, UsdPhysics

        from roboenv_perception.construction.isaac_asset_worker import adapt_asset
        from roboenv_perception.construction.isaac_table_worker import export_asset

        output.mkdir(parents=True, exist_ok=False)
        scene_spec = json.loads((root / "scene-build-spec.json").read_text())
        (output / "asset").mkdir()
        shutil.copyfile(root / "table/table.png", output / "asset/table.png")
        geometry = table_geometry(scene_spec["table"])
        table_path = export_asset(output, geometry, table_spec())
        table_stage = Usd.Stage.Open(str(table_path))
        for prim in table_stage.Traverse():
            path = str(prim.GetPath())
            if "/Visual/Side" in path or "/Legs/" in path:
                UsdGeom.Gprim(prim).CreateDisplayColorAttr([Gf.Vec3f(0.84, 0.84, 0.82)])
        table_stage.GetRootLayer().Save()

        files, specs = {}, {}
        for entry in scene_spec["objects"]:
            work = root / "objects" / entry["asset_id"]
            spec = json.loads((work / "build-spec.json").read_text())
            specs[entry["asset_id"]] = spec
            destination = output / "assets" / entry["category"] / "object.usd"
            destination.parent.mkdir(parents=True, exist_ok=True)
            adapt_asset(work / "export/object.usd", destination, spec)
            files[entry["asset_id"]] = destination

        dt = 1 / 240
        sim = sim_utils.SimulationContext(
            sim_utils.SimulationCfg(
                device="cpu",
                dt=dt,
                render_interval=8,
                enable_scene_query_support=True,
                physx=sim_utils.PhysxCfg(
                    solver_type=1, enable_external_forces_every_iteration=True
                ),
            )
        )
        table = sim_utils.UsdFileCfg(usd_path=str(table_path))
        table.func("/World/Table", table, translation=(0, 0, scene_spec["table"]["top_height_m"]))
        floor = sim_utils.CuboidCfg(
            size=(3, 3, 0.05),
            collision_props=sim_utils.CollisionPropertiesCfg(collision_enabled=True),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.17, 0.18, 0.20)),
        )
        floor.func("/World/Floor", floor, translation=(0, 0, -0.025))
        light = sim_utils.DomeLightCfg(intensity=900)
        light.func("/World/Light", light)

        objects, initial = {}, {}
        for entry in scene_spec["objects"]:
            asset_id = entry["asset_id"]
            pos = list(entry["position"])
            pos[2] += 0.02
            rot = quaternion(entry["yaw"])
            initial[entry["category"]] = {"position": pos, "quaternion": rot}
            if entry["kind"] == "articulated":
                objects[entry["category"]] = Articulation(
                    ArticulationCfg(
                        prim_path="/World/" + entry["category"],
                        spawn=sim_utils.UsdFileCfg(
                            usd_path=str(files[asset_id]), activate_contact_sensors=True
                        ),
                        init_state=ArticulationCfg.InitialStateCfg(
                            pos=tuple(pos),
                            rot=tuple(rot),
                            joint_pos={".*lid_hinge": entry.get("opening_rad", 0.0)},
                        ),
                        actuators={},
                    )
                )
            else:
                objects[entry["category"]] = RigidObject(
                    RigidObjectCfg(
                        prim_path="/World/" + entry["category"],
                        spawn=sim_utils.UsdFileCfg(
                            usd_path=str(files[asset_id]), activate_contact_sensors=True
                        ),
                        init_state=RigidObjectCfg.InitialStateCfg(
                            pos=tuple(pos), rot=tuple(rot)
                        ),
                    )
                )

        sim.reset()
        for obj in objects.values():
            obj.reset()
        rows = []
        for step in range(300):
            sim.step(render=False)
            for obj in objects.values():
                obj.update(dt)
            rows.append(
                {
                    "t": (step + 1) * dt,
                    "states": {
                        name: obj.data.root_state_w[0].cpu().numpy().tolist()
                        for name, obj in objects.items()
                    },
                }
            )
        settle_speeds = {
            name: float(np.linalg.norm(np.asarray(rows[-1]["states"][name][7:10])))
            for name in objects
        }

        pushes = {}
        for name in ["tablet_case", "headphones"]:
            obj = objects[name]
            mass = specs[
                next(e["asset_id"] for e in scene_spec["objects"] if e["category"] == name)
            ]["parts"][0]["est_mass_kg"]
            start = obj.data.root_state_w[0, :3].cpu().numpy().copy()
            for _ in range(24):
                forces = torch.zeros((1, obj.num_bodies, 3), dtype=torch.float32)
                forces[0, 0, 0] = 0.9 * mass * 9.81
                obj.set_external_force_and_torque(forces, torch.zeros_like(forces), is_global=True)
                obj.write_data_to_sim()
                sim.step(render=False)
                obj.update(dt)
            end = obj.data.root_state_w[0, :3].cpu().numpy()
            pushes[name] = float(np.linalg.norm(end - start))

        hinge = objects["earbud_case"]
        hinge_pos = hinge.data.joint_pos.clone()
        hinge_pos[0, 0] = 1.5
        hinge.write_joint_state_to_sim(hinge_pos, torch.zeros_like(hinge_pos))
        hinge.write_data_to_sim()
        for _ in range(120):
            sim.step(render=False)
            hinge.update(dt)

        final = {
            name: obj.data.root_state_w[0].cpu().numpy().tolist()
            for name, obj in objects.items()
        }
        hinge_angle = float(hinge.data.joint_pos[0, 0])
        checks = {
            "scene_primitives_present": all(
                any(entry["category"] in str(p.GetPath()) for p in sim.stage.Traverse())
                for entry in scene_spec["objects"]
            ),
            "all_states_finite": all(
                np.isfinite(np.asarray(state)).all() for state in final.values()
            ),
            "objects_above_table": all(float(state[2]) > 0.74 for state in final.values()),
            "objects_settle_below_3cm_s": all(speed < 0.03 for speed in settle_speeds.values()),
            "tablet_push_over_1mm": pushes["tablet_case"] > 0.001,
            "headphones_push_over_1mm": pushes["headphones"] > 0.001,
            "freeclip_hinge_moves": 1.0 < hinge_angle < 2.2,
            "freeclip_hinge_finite": math.isfinite(hinge_angle),
        }

        portable = sim.stage.GetRootLayer().ExportToString()
        for asset_id, path in files.items():
            portable = portable.replace(str(path), "./" + str(path.relative_to(output)))
        portable = portable.replace(str(table_path), "./asset/table.usda")
        scene_path = output / "scene.usda"
        scene_path.write_text(portable)
        exported = Usd.Stage.Open(str(scene_path))
        world = exported.GetPrimAtPath("/World")
        if not world.IsValid():
            raise ValueError("scene export has no /World root")
        exported.SetDefaultPrim(world)
        joint = next(p for p in exported.Traverse() if p.IsA(UsdPhysics.RevoluteJoint))
        state_api = PhysxSchema.JointStateAPI.Apply(joint, "angular")
        state_api.CreatePositionAttr(math.degrees(specs["asset-0005"]["scene_opening_rad"]))
        state_api.CreateVelocityAttr(0)
        lid = next(
            p
            for p in exported.Traverse()
            if p.GetName() == "lid" and p.HasAPI(UsdPhysics.RigidBodyAPI)
        )
        xform = UsdGeom.Xformable(lid)
        xform.AddOrientOp().Set(
            Gf.Quatf(
                math.cos(specs["asset-0005"]["scene_opening_rad"] / 2),
                Gf.Vec3f(math.sin(specs["asset-0005"]["scene_opening_rad"] / 2), 0, 0),
            )
        )
        exported.GetRootLayer().Save()

        result = {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": {name: bool(value) for name, value in checks.items()},
            "final_root_states": final,
            "settled_linear_speed_m_s": settle_speeds,
            "push_displacement_m": pushes,
            "freeclip_hinge_angle_rad": hinge_angle,
            "table_geometry": geometry,
            "objects": scene_spec["objects"],
            "scale_source": scene_spec["scale_source"],
            "placement_source": scene_spec["placement_source"],
            "physics_parameters_source": "assumed simulator parameters; not measured",
            "simulator": "Isaac Sim 5.1 / Isaac Lab 2.3.2; CPU PhysX TGS; dt=1/240",
            "scope": "Tabletop assembly, settling, push response and passive FreeClip lid hinge. No robot or real-world metrology.",
            "candidate_sources": {
                asset_id: {
                    "model_sha256": digest(root / "objects" / asset_id / "model.py"),
                    "usd_sha256": digest(files[asset_id]),
                    "usd_ref": str(files[asset_id].relative_to(output)),
                    "dependency_hashes": {
                        str(p.relative_to(files[asset_id].parent)): digest(p)
                        for p in files[asset_id].parent.rglob("*")
                        if p.is_file()
                    },
                }
                for asset_id in files
            },
            "scene_ref": "scene.usda",
            "scene_sha256": digest(scene_path),
        }
        (output / "trajectory.json").write_text(json.dumps(rows) + "\n")
        (output / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
        print("AUDIO_SCENE_RESULT", json.dumps(result), flush=True)
        return 0 if result["status"] == "passed" else 2
    except BaseException:
        if output.exists():
            (output / "failure.json").write_text(
                json.dumps({"status": "failed", "traceback": traceback.format_exc()}, indent=2)
            )
        traceback.print_exc()
        return 2
    finally:
        sys.stdout.flush()
        app.close(wait_for_replicator=False)


def run_check(scene, output):
    from isaaclab.app import AppLauncher

    launcher = AppLauncher(
        headless=True,
        enable_cameras=True,
        device="cpu",
        kit_args=f"--portable-root {output.parent / 'kit-runtime'}",
    )
    app = launcher.app
    try:
        import isaaclab.sim as sim_utils
        import numpy as np
        import torch
        from isaaclab.assets import (
            Articulation,
            ArticulationCfg,
            RigidObject,
            RigidObjectCfg,
        )
        from isaaclab.sensors import Camera, CameraCfg
        from PIL import Image

        output.mkdir(parents=True, exist_ok=False)
        sim = sim_utils.SimulationContext(
            sim_utils.SimulationCfg(
                device="cpu",
                dt=1 / 240,
                render_interval=8,
                enable_scene_query_support=True,
                physx=sim_utils.PhysxCfg(
                    solver_type=1, enable_external_forces_every_iteration=True
                ),
            )
        )
        scene_cfg = sim_utils.UsdFileCfg(usd_path=str(scene))
        scene_cfg.func("/World/Scene", scene_cfg, translation=(0, 0, 0))
        floor = sim_utils.CuboidCfg(
            size=(3, 3, 0.05),
            collision_props=sim_utils.CollisionPropertiesCfg(collision_enabled=True),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.17, 0.18, 0.20)),
        )
        floor.func("/World/Floor", floor, translation=(0, 0, -0.025))
        camera = Camera(
            CameraCfg(
                prim_path="/World/Overview",
                width=960,
                height=720,
                data_types=["rgb"],
                update_period=0,
                spawn=sim_utils.PinholeCameraCfg(
                    focal_length=30,
                    horizontal_aperture=28,
                    clipping_range=(0.05, 100),
                ),
            )
        )
        objects = {}
        for name in ["tablet_case", "headphones"]:
            objects[name] = RigidObject(
                RigidObjectCfg(
                    prim_path=f"/World/Scene/{name}",
                    init_state=RigidObjectCfg.InitialStateCfg(pos=(0, 0, 0.75)),
                )
            )
        objects["earbud_case"] = Articulation(
            ArticulationCfg(
                prim_path="/World/Scene/earbud_case",
                init_state=ArticulationCfg.InitialStateCfg(pos=(0, 0, 0.75)),
                actuators={},
            )
        )
        sim.reset()
        camera.set_world_poses_from_view(
            torch.tensor([[1.00, 0.90, 1.35]], dtype=torch.float32),
            torch.tensor([[0.0, 0.0, 0.82]], dtype=torch.float32),
        )
        for _ in range(4):
            sim.render()
        camera.update(1 / 240, force_recompute=True)
        Image.fromarray(
            camera.data.output["rgb"][0, ..., :3].cpu().numpy().astype(np.uint8)
        ).save(output / "preview-initial.png")
        for _ in range(240):
            sim.step(render=False)
        for obj in objects.values():
            obj.update(1 / 240)
        camera.update(1, force_recompute=True)
        sim.render()
        Image.fromarray(
            camera.data.output["rgb"][0, ..., :3].cpu().numpy().astype(np.uint8)
        ).save(output / "preview-after-1s.png")
        states = {
            name: obj.data.root_state_w[0].cpu().numpy().tolist()
            for name, obj in objects.items()
        }
        checks = {
            "scene_loaded": all(
                any(name in str(p.GetPath()) for p in sim.stage.Traverse()) for name in objects
            ),
            "states_finite": all(np.isfinite(np.asarray(s)).all() for s in states.values()),
            "objects_above_table": all(float(s[2]) > 0.74 for s in states.values()),
        }
        result = {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": {name: bool(value) for name, value in checks.items()},
            "states": states,
            "scene_sha256": digest(scene),
        }
        (output / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
        print("AUDIO_SCENE_RELOAD", json.dumps(result), flush=True)
        return 0 if result["status"] == "passed" else 2
    except BaseException:
        if output.exists():
            (output / "failure.json").write_text(
                json.dumps({"status": "failed", "traceback": traceback.format_exc()}, indent=2)
            )
        traceback.print_exc()
        return 2
    finally:
        sys.stdout.flush()
        app.close(wait_for_replicator=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["build", "check"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "build":
        return run_build(args.input.resolve(), args.output.resolve())
    return run_check(args.input.resolve(), args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
