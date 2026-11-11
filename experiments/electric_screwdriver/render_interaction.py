"""Replay measured PhysX rigid transforms using the generated Blender meshes."""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--visual", type=Path, required=True)
parser.add_argument("--experiment", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--preview-only", action="store_true")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.visual, args.experiment, args.output = args.visual.resolve(), args.experiment.resolve(), args.output.resolve()
args.output.mkdir(parents=True, exist_ok=False)
traces = json.loads((args.experiment / "trace.json").read_text())["aligned"]
meshes = json.loads((args.visual / "result.json").read_text())["meshes"]
bpy.ops.wm.open_mainfile(filepath=str(args.visual / "asset.blend"))
scene = bpy.context.scene
scene.cycles.samples = 12
scene.render.resolution_x, scene.render.resolution_y = 800, 700
scene.render.resolution_percentage = 100
before = set(bpy.data.objects)
bpy.ops.wm.usd_import(filepath=str(args.experiment / "initial_scene.usda"),
                      prim_path_mask="/World/Case_aligned/bolt")
imported = set(bpy.data.objects) - before
bolt = next(o for o in imported if o.name == "bolt")
bolt.parent = None
for o in imported:
    if o.type == "MESH":
        mat = bpy.data.materials.new(o.name + "_gold")
        mat.diffuse_color = (.58, .40, .12, 1)
        mat.use_nodes = True
        shader = mat.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = mat.diffuse_color
        shader.inputs["Metallic"].default_value = .7
        shader.inputs["Roughness"].default_value = .35
        o.data.materials.clear()
        o.data.materials.append(mat)

supports = []
for name, pos, size in [("left", (-.008, 0, .790), (.0115, .02, .02)),
                         ("right", (.008, 0, .790), (.0115, .02, .02)),
                         ("front", (0, -.008, .790), (.02, .0115, .02)),
                         ("back", (0, .008, .790), (.02, .0115, .02))]:
    bpy.ops.mesh.primitive_cube_add(size=1, location=pos)
    obj = bpy.context.object
    obj.name = "support_" + name
    obj.dimensions = size
    mat = bpy.data.materials.new(obj.name + "_finish")
    mat.diffuse_color = (.23, .28, .33, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = mat.diffuse_color
    shader.inputs["Roughness"].default_value = .7
    obj.data.materials.append(mat)
    supports.append(obj)
# These four static supports overlap in the physical scene. Union them only in
# the display mesh so coincident surfaces do not produce black render artifacts.
bpy.context.view_layer.objects.active = supports[0]
for other in supports[1:]:
    mod = supports[0].modifiers.new("display_union", "BOOLEAN")
    mod.operation, mod.object = "UNION", other
    bpy.ops.object.modifier_apply(modifier=mod.name)
    other.hide_render = True
camera = scene.camera
camera.data.clip_start = .0001  # The default 100 mm clips the entire macro view.
scene.world.color = (.22, .25, .29)
# Lights from the original local tool preview need moving to the test fixture.
for obj in bpy.data.objects:
    if obj.type == "LIGHT":
        obj.location.z += .8
        obj.rotation_euler = (Vector((0, 0, .82)) - obj.location).to_track_quat("-Z", "Y").to_euler()


def apply(row):
    for mesh in meshes:
        part = "spindle" if mesh["part"] == "spindle" else "housing"
        obj = bpy.data.objects["export_" + mesh["name"]]
        obj.location = row[part + "_position_m"]
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = Quaternion(row[part + "_quaternion_wxyz"])
    bolt.location = row["screw_position_m"]
    bolt.rotation_mode = "QUATERNION"
    bolt.rotation_quaternion = Quaternion(row["screw_quaternion_wxyz"])


def view(target, offset, scale):
    target = Vector(target)
    camera.location = target + Vector(offset)
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.ortho_scale = scale


apply(traces[0])
view((0, 0, .908), (.25, -.35, .08), .33)
scene.render.filepath = str(args.output / "overview.png")
bpy.ops.render.render(write_still=True)
indices = list(range(0, len(traces), 6))  # actual ~8.3 Hz sample of the 50 Hz trace
if args.preview_only:
    indices = [0, len(traces) - 1]
for frame, index in enumerate(indices):
    apply(traces[index])
    view((0, 0, .808), (.05, -.07, .038), .060)
    scene.render.filepath = str(args.output / f"frame-{frame:03d}.png")
    bpy.ops.render.render(write_still=True)
(args.output / "frames.json").write_text(json.dumps([traces[i] for i in indices], indent=2))
(args.output / "recording.json").write_text(json.dumps({
    "kind": "Blender replay of measured PhysX poses", "robot_present": False,
    "source_trace": str(args.experiment / "trace.json"), "scripted_success_animation": False,
    "note": "visual replay; raw physics evidence is report.json and trace.json"}, indent=2))
