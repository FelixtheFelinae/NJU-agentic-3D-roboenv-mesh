"""Render the actual generated meshes. These are asset previews, not robot experiments."""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--visual", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output.mkdir(parents=True, exist_ok=False)
bpy.ops.wm.open_mainfile(filepath=str(args.visual / "asset.blend"))
scene = bpy.context.scene
scene.cycles.samples = 48
scene.render.resolution_x = 1200
scene.render.resolution_y = 800
scene.render.resolution_percentage = 100
scene.world.color = (.18, .20, .23)
camera = scene.camera
for obj in bpy.data.objects:
    if obj.name.startswith("export_") and obj.type == "MESH":
        # Smooth only round surface walls; preserve real hex/prismatic edges.
        if obj.name in {"export_body_shell", "export_rear_bumper", "export_rotor_shaft",
                        "export_bit_holder", "export_grip_left", "export_grip_right"}:
            for poly in obj.data.polygons:
                poly.use_smooth = len(poly.vertices) == 4


def render(name, target, offset, span):
    target = Vector(target)
    camera.location = target + Vector(offset)
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.ortho_scale = span
    scene.render.filepath = str(args.output / name)
    bpy.ops.render.render(write_still=True)


render("hero.png", (0, .018, 0), (.27, .20, .19), .275)
render("tip.png", (0, .092, 0), (.10, .12, .075), .115)
render("side.png", (0, .018, 0), (.3, 0, .05), .265)
result = json.loads((args.visual / "result.json").read_text())
for item in result["meshes"]:
    if item["part"] == "spindle":
        bpy.data.objects["export_" + item["name"]].location.y += .055
render("exploded.png", (0, .045, 0), (.27, .20, .19), .33)
(args.output / "render_notes.json").write_text(json.dumps({
    "source_blend": str(args.visual / "asset.blend"),
    "kind": "actual generated geometry rendered in Blender",
    "exploded_translation_m": [0, .055, 0],
    "exploded_scope": "housing/spindle illustration; not a verified disassembly path",
    "robot_or_physics_screenshot": False,
}, indent=2))
