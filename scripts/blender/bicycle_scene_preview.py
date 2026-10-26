"""Render an existing bicycle blend with the observed floor atlas (no animation)."""

import argparse
import sys
from pathlib import Path

import bpy
from mathutils import Vector

parser = argparse.ArgumentParser()
parser.add_argument("--asset", type=Path, required=True)
parser.add_argument("--ground", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--drivetrain-cutaway", action="store_true")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
args.output.mkdir(parents=True, exist_ok=False)
bpy.ops.wm.open_mainfile(filepath=str(args.asset.resolve()))
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=2)
floor = bpy.context.object
floor.name = "ObservedBrickGround"
floor.scale = (1.8, 1.5, 1)
material = bpy.data.materials.new("ObservedVideoRGB")
material.use_nodes = True
nodes = material.node_tree.nodes
texture = nodes.new("ShaderNodeTexImage")
texture.image = bpy.data.images.load(str(args.ground.resolve()))
texture.image.pack()
shader = nodes.get("Principled BSDF")
material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
shader.inputs["Roughness"].default_value = 0.9
floor.data.materials.append(material)
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 32
scene.cycles.use_denoising = True
scene.render.resolution_x = 1200
scene.render.resolution_y = 900
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "Medium High Contrast"
scene.view_settings.exposure = -0.5
scene.world.color = (0.3, 0.3, 0.3)
for name, location, power, size in [("Key", (1, -2, 4), 350, 4), ("Fill", (-2, 2, 3), 200, 3)]:
    data = bpy.data.lights.new(name, "AREA")
    data.energy = power
    data.size = size
    light = bpy.data.objects.new(name, data)
    scene.collection.objects.link(light)
    light.location = location
    light.rotation_euler = (
        (Vector((0, 0, 0.5)) - light.location).to_track_quat("-Z", "Y").to_euler()
    )
data = bpy.data.cameras.new("SceneCamera")
camera = bpy.data.objects.new("SceneCamera", data)
scene.collection.objects.link(camera)
scene.camera = camera
camera.data.type = "ORTHO"
camera.data.ortho_scale = 2.65
views = [
    ("preview", (1.8, -2.8, 1.8)),
    ("opposite", (-1.5, 2.8, 1.6)),
    ("side", (0, -3, 1.0)),
]
target = (0, 0, 0.48)
if args.drivetrain_cutaway:
    for name in ["chain_guard", "chain_guard_front"]:
        bpy.data.objects["export_" + name].hide_render = True
    camera.data.ortho_scale = 0.95
    target = (-0.28, -0.052, 0.30)
    views = [("drivetrain-cutaway", (-0.28, -3, 0.6))]
for name, eye in views:
    camera.location = eye
    camera.rotation_euler = (Vector(target) - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.filepath = str(args.output.resolve() / f"{name}.png")
    bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve() / "scene.blend"))
