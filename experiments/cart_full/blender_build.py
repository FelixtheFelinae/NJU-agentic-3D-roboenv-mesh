"""Trusted primitive interpreter: reviewed recipe -> assembly meshes and views."""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

parser = argparse.ArgumentParser()
parser.add_argument("--spec", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
args.output.mkdir(parents=True, exist_ok=False)
spec = json.loads(args.spec.read_text())
bpy.ops.wm.read_factory_settings(use_empty=True)
colors = {
    "metal": (0.46, 0.55, 0.61),
    "steel": (0.62, 0.66, 0.69),
    "dark": (0.035, 0.042, 0.05),
    "black": (0.012, 0.015, 0.02),
    "rubber": (0.14, 0.16, 0.17),
    "housing": (0.12, 0.16, 0.20),
    "screen": (0.025, 0.048, 0.069),
    "yellow": (0.9, 0.64, 0.035),
    "target": (0.95, 0.3, 0.035),
}
mats = {}
for name, c in colors.items():
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*c, 1)
    m.use_nodes = True
    bs = m.node_tree.nodes["Principled BSDF"]
    bs.inputs["Base Color"].default_value = (*c, 1)
    bs.inputs["Metallic"].default_value = 0.7 if name in ("metal", "steel") else 0.0
    bs.inputs["Roughness"].default_value = 0.28 if name in ("metal", "steel", "screen") else 0.5
    mats[name] = m
roots = {}
statistics = []
meshdir = args.output / "meshes"
meshdir.mkdir()
for part in spec["parts"]:
    objs = []
    for solid in part["solids"]:
        shape = solid["shape"]
        center = Vector(solid.get("center", [0, 0, 0]))
        if shape == "box":
            bpy.ops.mesh.primitive_cube_add(size=1)
            obj = bpy.context.object
            obj.dimensions = solid["size"]
        elif shape == "annulus":
            import math

            n = 64
            vertices = [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z)
                        for z in (-solid['length']/2, solid['length']/2)
                        for r in (solid['radius'], solid['inner_radius']) for i in range(n)]
            faces = []
            for i in range(n):
                j = (i+1) % n
                faces.extend([(i, j, 2*n+j, 2*n+i), (n+j, n+i, 3*n+i, 3*n+j),
                              (j, i, n+i, n+j), (2*n+i, 2*n+j, 3*n+j, 3*n+i)])
            mesh = bpy.data.meshes.new(part['id'] + '_ring')
            mesh.from_pydata(vertices, [], faces)
            mesh.update()
            obj = bpy.data.objects.new(part['id'] + '_ring', mesh)
            bpy.context.collection.objects.link(obj)
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
        elif shape == "cylinder":
            bpy.ops.mesh.primitive_cylinder_add(
                vertices=solid.get("sides", 32), radius=solid["radius"], depth=solid["length"]
            )
            obj = bpy.context.object
        elif shape == "torus":
            bpy.ops.mesh.primitive_torus_add(
                major_segments=48,
                minor_segments=12,
                major_radius=solid["radius"],
                minor_radius=solid["minor"],
            )
            obj = bpy.context.object
        elif shape == "rod":
            a, b = Vector(solid["start"]), Vector(solid["end"])
            delta = b - a
            center = (a + b) / 2
            bpy.ops.mesh.primitive_cylinder_add(
                vertices=10, radius=solid["radius"], depth=delta.length
            )
            obj = bpy.context.object
            solid = dict(solid, axis=list(delta.normalized()))
        if "axis" in solid:
            obj.rotation_mode = "QUATERNION"
            obj.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(Vector(solid["axis"]))
        obj.location = center
        obj.data.materials.append(mats[solid["color"]])
        objs.append(obj)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    obj = bpy.context.object
    # Bake primitive transforms into mesh then place the stable part at its recipe origin.
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    obj.name = part["id"]
    obj.location = part["center"]
    obj["evidence_status"] = part["evidence_status"]
    obj["body"] = part["body"]
    roots[obj.name] = obj
    obj["dimension_source"] = "assumed"
    bpy.context.view_layer.update()
    # Simple positional OBJ keeps vertex topology and material groups in Blender/GLB.
    lines = []
    for v in obj.data.vertices:
        lines.append("v " + " ".join(f"{c:.9g}" for c in v.co))
    for f in obj.data.polygons:
        lines.append("f " + " ".join(str(i + 1) for i in f.vertices))
    (meshdir / (obj.name + ".obj")).write_text("\n".join(lines) + "\n")
    statistics.append(
        {
            "id": obj.name,
            "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons),
            "bbox_world": [
                list(
                    min((obj.matrix_world @ v.co)[i] for v in obj.data.vertices) for i in range(3)
                ),
                list(
                    max((obj.matrix_world @ v.co)[i] for v in obj.data.vertices) for i in range(3)
                ),
            ],
        }
    )
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 24
scene.render.resolution_x = 1400
scene.render.resolution_y = 1050
scene.render.resolution_percentage = 100
scene.world = bpy.data.worlds.new("world")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.17, 0.20, 0.25, 1)
scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.5
for loc, power, size in [((0.3, -0.4, 0.8), 7, 0.6), ((-0.4, 0.2, 0.5), 4, 0.5)]:
    bpy.ops.object.light_add(type="AREA", location=loc)
    light = bpy.context.object
    light.data.energy = power
    light.data.shape = "DISK"
    light.data.size = size
    light.rotation_euler = (-light.location).to_track_quat("-Z", "Y").to_euler()
bpy.ops.object.camera_add(location=(0.55, -0.65, 0.65))
camera = bpy.context.object
scene.camera = camera
camera.data.type = "ORTHO"
camera.data.ortho_scale = 0.66
camera.data.clip_start = 0.001


def render(name, location, target=(0, 0.035, 0.06), scale=0.66):
    camera.location = location
    camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.ortho_scale = scale
    scene.render.filepath = str((args.output / (name + ".png")).resolve())
    bpy.ops.render.render(write_still=True)


bpy.ops.object.select_all(action="DESELECT")
for obj in roots.values():
    obj.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=str((args.output / "cart_assembled.glb").resolve()), use_selection=True
)
bpy.ops.wm.usd_export(
    filepath=str((args.output / "cart_visual.usdc").resolve()), selected_objects_only=True
)
render("assembled", (0.55, -0.65, 0.65))
render("top", (0, 0.035, 1), scale=0.61)
render("front", (0.5, 0.8, 0.35))
render("side", (0.8, 0, 0.2))
# Exploded view is a visual inspection aid, not a validated disassembly trajectory.
for part in spec["parts"]:
    obj = roots[part["id"]]
    center = Vector(part["center"])
    category = part["category"]
    if part["body"] != "chassis":
        obj.location.x += 0.105 if center.x > 0 else -0.105
    elif category == "fastener":
        obj.location.z += 0.10
    elif part["id"] == "bumper" or part["id"].startswith("bumper_pad"):
        obj.location.y += 0.12
    elif category in ("screen_controller", "black_box_adjacent_controller", "yellow_block"):
        obj.location.z += 0.10
render("exploded", (0.7, -0.85, 0.85), scale=0.87)
for part in spec["parts"]:
    roots[part["id"]].location = part["center"]
render("connection_closeup", (0.24, 0.40, 0.27), target=(0.1016, 0.226, 0.075), scale=0.14)
camera.location = (0.55, -0.65, 0.65)
camera.rotation_euler = (
    (Vector((0, 0.035, 0.06)) - camera.location).to_track_quat("-Z", "Y").to_euler()
)
camera.data.ortho_scale = 0.66
bpy.ops.wm.save_as_mainfile(filepath=str((args.output / "cart_assembly.blend").resolve()))
(args.output / "geometry_manifest.json").write_text(
    json.dumps(
        {
            "parts": statistics,
            "part_count": len(statistics),
            "exploded_view_status": "illustrative offsets; not disassembly validation",
            "source_spec": str(args.spec.resolve()),
        },
        indent=2,
    )
    + "\n"
)
