"""Package original Artisan MJCF with baked 3DGS floor and run MuJoCo checks."""

import argparse
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--asset", type=Path, required=True)
    p.add_argument("--bake", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--tile", action="store_true")
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(a.asset / "assets", a.output / "assets")
    (a.output / "textures").mkdir()
    for name in ["floor_3dgs.png", "coverage_mask.png", "floor_material_tile.png"]:
        shutil.copyfile(a.bake / name, a.output / "textures" / name)
    tree = ET.parse(a.asset / "object_scene.xml")
    root = tree.getroot()
    root.set("model", "video_cart_on_3dgs_floor")
    compiler = root.find("compiler")
    compiler.set("texturedir", "textures")
    option = root.find("option")
    option.set("timestep", "0.002")
    option.set("gravity", "0 0 -9.81")
    option.set("integrator", "implicitfast")
    visual = ET.SubElement(root, "visual")
    ET.SubElement(visual, "global", offwidth="1280", offheight="960")
    ET.SubElement(
        visual, "headlight", ambient=".15 .15 .15", diffuse=".25 .25 .25", specular=".1 .1 .1"
    )
    assets = root.find("asset")
    ET.SubElement(
        assets,
        "texture",
        name="floor_from_3dgs",
        type="2d",
        file="floor_material_tile.png" if a.tile else "floor_3dgs.png",
    )
    ET.SubElement(
        assets,
        "material",
        name="floor_material",
        texture="floor_from_3dgs",
        texrepeat=(
            " ".join(
                [
                    str(
                        2.4
                        / json.loads((a.bake / "material_tile.json").read_text())[
                            "tile_width_m_assumed"
                        ]
                    )
                ]
                * 2
            )
            if a.tile
            else "1 1"
        ),
        texuniform="false",
        reflectance="0",
        specular="0",
        shininess="0",
    )
    world = root.find("worldbody")
    ground = world.find("geom[@name='scene_ground']")
    ground.set("size", "1.2 1.2 .05")
    ground.set("material", "floor_material")
    ground.set("rgba", "1 1 1 1")
    ground.set("friction", "0.8 0.005 0.0001")
    chassis = world.find("body[@name='chassis']")
    chassis.set("pos", "0 0 .012")
    world.find("light[@name='scene_light']").set("diffuse", ".45 .45 .45")
    # Preserve visuals and joints; replace problematic exported convex collisions with declared proxies.
    spec = json.loads(a.spec.read_text())
    for body in root.findall(".//body"):
        for geom in list(body.findall("geom")):
            if geom.get("class") == "collision":
                body.remove(geom)
        name = body.get("name")
        if name.startswith("wheel_"):
            ET.SubElement(
                body,
                "geom",
                name="proxy_" + name,
                type="cylinder",
                size=".051 .013" if "front" in name else ".05 .006",
                quat=".70710678 0 .70710678 0",
                group="3",
                friction=".8 .005 .0001",
            )
        elif name == "chassis":
            for part in spec["parts"]:
                if part["body"] != "chassis" or part["category"] not in (
                    "perforated_channel",
                    "screen_controller",
                    "black_module",
                    "black_end_block",
                ):
                    continue
                boxes = [x for x in part["solids"] if x["shape"] == "box"]
                lo = [min(x["center"][i] - x["size"][i] / 2 for x in boxes) for i in range(3)]
                hi = [max(x["center"][i] + x["size"][i] / 2 for x in boxes) for i in range(3)]
                ET.SubElement(
                    body,
                    "geom",
                    name="proxy_" + part["id"],
                    type="box",
                    pos=" ".join(str(part["center"][i] + (lo[i] + hi[i]) / 2) for i in range(3)),
                    size=" ".join(str((hi[i] - lo[i]) / 2) for i in range(3)),
                    group="3",
                )
    # Directly connected chassis-wheel pairs represent ideal bearings and must not self-collide.
    option.find("flag").set("filterparent", "enable")
    # Colors are display assumptions.

    for geom in root.findall(".//geom"):
        if geom.get("class") != "visual":
            continue
        name = geom.get("name", "").lower()
        color = ".48 .53 .57 1"
        if any(k in name for k in ["wheel", "module", "pad", "bearing", "power", "cable"]):
            color = ".07 .08 .09 1"
        if "controller" in name:
            color = ".13 .17 .22 1"
        if "yellow" in name:
            color = ".85 .62 .06 1"
        if "bumper_screw_right" in name:
            color = ".9 .32 .04 1"
        geom.set("rgba", color)
    ET.indent(root)
    scene = a.output / "scene.xml"
    tree.write(scene, encoding="unicode")
    model = mujoco.MjModel.from_xml_path(str(scene.resolve()))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    cid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "chassis")
    groundid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "scene_ground")
    traces = []
    frames = []
    renderer = mujoco.Renderer(model, height=720, width=960)
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = [0, 0.06, 0.04]
    camera.distance = 1.1
    camera.azimuth = 135
    camera.elevation = -48
    option_render = mujoco.MjvOption()
    option_render.geomgroup[3] = 0
    for step in range(2250):
        t = step * 0.002
        data.xfrc_applied[:] = 0
        if 1.5 <= t < 2.5:
            data.xfrc_applied[cid, 1] = 5.0
        mujoco.mj_step(model, data)
        if step % 5 == 0:
            contacts = sum(1 for c in data.contact[: data.ncon] if groundid in (c.geom1, c.geom2))
            traces.append(
                {
                    "t_s": float(data.time),
                    "qpos": data.qpos.tolist(),
                    "qvel": data.qvel.tolist(),
                    "ground_contacts": contacts,
                    "push_N": 5.0 if 1.5 <= t < 2.5 else 0.0,
                }
            )
        if step % 25 == 0:
            camera.lookat[:2] = data.qpos[:2]
            renderer.update_scene(data, camera, scene_option=option_render)
            frames.append(Image.fromarray(renderer.render()))
        if step == 700:
            renderer.update_scene(data, camera, scene_option=option_render)
            Image.fromarray(renderer.render()).save(a.output / "scene.png")
    frames[0].save(
        a.output / "simulation.gif", save_all=True, append_images=frames[1:], duration=50, loop=0
    )
    b = [r for r in traces if 1 <= r["t_s"] < 1.5]
    d = [r for r in traces if 1.5 <= r["t_s"] < 2.5]
    metrics = {
        "baseline_z_span_m": max(r["qpos"][2] for r in b) - min(r["qpos"][2] for r in b),
        "baseline_planar_drift_m": float(
            np.linalg.norm(np.array(b[-1]["qpos"][:2]) - b[0]["qpos"][:2])
        ),
        "push_displacement_m": d[-1]["qpos"][1] - b[-1]["qpos"][1],
        "wheel_rotation_rad": max(abs(x - y) for x, y in zip(d[-1]["qpos"][7:], b[-1]["qpos"][7:])),
        "max_abs_vertical_speed_after_settle": max(abs(r["qvel"][2]) for r in b + d),
        "minimum_ground_contacts": min(r["ground_contacts"] for r in b + d),
    }
    checks = {
        "finite": all(np.isfinite(r["qpos"] + r["qvel"]).all() for r in traces),
        "baseline_stable": metrics["baseline_z_span_m"] < 0.003
        and metrics["baseline_planar_drift_m"] < 0.01,
        "ground_contact": metrics["minimum_ground_contacts"] > 0,
        "push_response": metrics["push_displacement_m"] > 0.02,
        "wheel_response": metrics["wheel_rotation_rad"] > 0.1,
        "upright": all(2 * (r["qpos"][4] ** 2 + r["qpos"][5] ** 2) < 0.05 for r in b + d),
        "floor_texture_loaded": model.ntex > 0,
        "no_fall_through": min(r["qpos"][2] for r in b + d) > -0.015,
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "mujoco_version": mujoco.__version__,
        "nq": model.nq,
        "nv": model.nv,
        "body_count": model.nbody,
        "geom_count": model.ngeom,
        "runtime_pose_writes": 0,
        "source_mjcf_sha256": digest(a.asset / "object_scene.xml"),
        "scene_sha256": digest(scene),
        "floor_texture_sha256": digest(
            a.bake / ("floor_material_tile.png" if a.tile else "floor_3dgs.png")
        ),
        "collision_adapter": "declared chassis boxes + tire cylinders; parent-child bearing contacts filtered; original visual meshes and joints retained",
        "floor_mode": "tiled GS appearance"
        if a.tile
        else "observed GS atlas with neutral unknowns",
        "floor_collision": "flat plane z=0; texture is baked 3DGS appearance, not Gaussian native rendering",
        "friction_source": "assumed; no identification from image",
        "placement": "approximate centre/heading, metric scale assumed from vehicle footprint",
        "assembly_runtime": "not ported: this MJCF retains original fixed chassis with four wheel joints; no screw removal in MuJoCo",
    }
    (a.output / "physics_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (a.output / "traces.json").write_text(json.dumps(traces))
    print(json.dumps(report))
    renderer.close()


if __name__ == "__main__":
    main()
