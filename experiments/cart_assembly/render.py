"""Blender presentation of the actual exported bench and recorded PhysX poses.

Rendering replays measured poses; it is not a second physical simulation.
"""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

parser = argparse.ArgumentParser()
parser.add_argument("--run", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
args.output.mkdir(parents=True, exist_ok=False)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.usd_import(filepath=str((args.run / "initial_scene.usda").resolve()))
target = bpy.data.objects.get("normal")
if target is None:
    raise RuntimeError("normal test subtree not imported")
keep = set([target, *target.children_recursive])
for obj in list(bpy.data.objects):
    if obj not in keep:
        bpy.data.objects.remove(obj, do_unlink=True)
bolt = next(o for o in keep if o.name.split(".")[0] == "bolt")
rows = json.loads((args.run / "traces.json").read_text())["normal"]
initial = rows[0]["position_m"]


def material(name, color, metal=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bs = mat.node_tree.nodes.get("Principled BSDF")
    bs.inputs["Base Color"].default_value = (*color, 1)
    bs.inputs["Metallic"].default_value = metal
    bs.inputs["Roughness"].default_value = 0.3
    return mat


steel = material("beam_steel", (0.36, 0.43, 0.5), 0.65)
gold = material("selected_screw", (0.7, 0.28, 0.045), 0.6)
support = material("fixed_support", (0.08, 0.12, 0.17), 0.3)
for obj in keep:
    if obj.type == "MESH":
        obj.data.materials.clear()
        obj.data.materials.append(
            gold if obj.parent == bolt else support if "support" in obj.name else steel
        )
        bevel = obj.modifiers.new("display_edge", "BEVEL")
        bevel.width = 0.00015
        bevel.segments = 2
        obj.modifiers.new("display_normals", "WEIGHTED_NORMAL")

# A dark surface mark makes axial rotation legible without claiming a real recess.
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
mark = bpy.context.object
mark.name = "rotation_marker_visual_only"
mark.parent = bolt
mark.location = (0.001, 0, 0.00202)
mark.dimensions = (0.003, 0.0006, 0.00008)
mark.data.materials.append(material("marker", (0.02, 0.025, 0.03)))
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 32
scene.render.resolution_x = 1100
scene.render.resolution_y = 850
scene.render.resolution_percentage = 100
scene.world = bpy.data.worlds.new("world")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.13, 0.16, 0.2, 1)
scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.5
bpy.ops.mesh.primitive_plane_add(size=200, location=(5, 0, -0.0002))
bpy.context.object.data.materials.append(material("floor", (0.12, 0.15, 0.19)))
bpy.ops.object.light_add(type="AREA", location=(5.06, -0.08, 0.16))
bpy.context.object.data.energy = 0.5
bpy.context.object.data.shape = "DISK"
bpy.context.object.data.size = 0.12
bpy.ops.object.camera_add(location=(5.12, -0.14, 0.14))
camera = bpy.context.object
camera.rotation_euler = (
    (Vector((5, 0, 0.035)) - camera.location).to_track_quat("-Z", "Y").to_euler()
)
camera.data.type = "ORTHO"
camera.data.ortho_scale = 0.14
camera.data.clip_start = 0.001
scene.camera = camera


def pose(row):
    bolt.location = row["position_m"]
    bolt.rotation_mode = "QUATERNION"
    bolt.rotation_quaternion = row["quaternion_wxyz"]


snapshots = [
    ("assembled", rows[0]),
    ("loosening", min(rows, key=lambda r: abs(r["relative_position_m"][2] - 0.002))),
    ("removed", min(rows, key=lambda r: abs(r["relative_position_m"][2] - 0.025))),
]
manifest = []
for label, row in snapshots:
    pose(row)
    scene.render.filepath = str((args.output / (label + ".png")).resolve())
    bpy.ops.render.render(write_still=True)
    manifest.append(
        {
            "image": label + ".png",
            "t_s": row["t_s"],
            "dz_m": row["relative_position_m"][2],
            "theta_rad": row["theta_rad"],
            "state": row["state"],
            "source": "actual PhysX trajectory",
        }
    )
pose(rows[0])
bpy.ops.object.select_all(action="DESELECT")
for obj in keep | {mark}:
    obj.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=str((args.output / "local_assembly.glb").resolve()), use_selection=True
)
end = next((i for i, r in enumerate(rows) if r["relative_position_m"][2] >= 0.03), len(rows) - 1)
samples = rows[: end + 1 : max(1, end // 120)]
for frame, row in enumerate(samples, 1):
    pose(row)
    bolt.keyframe_insert(data_path="location", frame=frame)
    bolt.keyframe_insert(data_path="rotation_quaternion", frame=frame)
scene.frame_end = len(samples)
scene.render.fps = 30
scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath=str((args.output / "trajectory_replay.blend").resolve()))
(args.output / "render_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
