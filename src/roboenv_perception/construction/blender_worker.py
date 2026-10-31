"""Blender-only worker. Generators call export_mesh(name, obj, ...) in metres, Z-up."""

import hashlib
import json
import math
import re
import runpy
import sys
import traceback
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output):
    import bmesh
    import bpy
    from mathutils import Vector

    request = json.loads((output / "request.json").read_text())
    params = json.loads((output / "parameters.json").read_text())
    for filename, key in [("generator.py", "generator_sha256"), ("worker.py", "worker_sha256"),
                          ("parameters.json", "parameters_sha256")]:
        if digest(output / filename) != request[key]:
            raise ValueError(f"Blender source changed: {filename}")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 1.0
    meshes, objects = [], []

    def export_mesh(name, obj, rgba=(0.46, 0.48, 0.47, 1), material_class="matte_plastic",
                    part="body", preview_translation=(0, 0, 0)):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or any(m["name"] == name for m in meshes):
            raise ValueError(f"Invalid or duplicate export name: {name}")
        if len(rgba) != 4 or not all(math.isfinite(c) and 0 <= c <= 1 for c in rgba):
            raise ValueError("rgba must have four finite values in [0, 1]")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", part):
            raise ValueError("Invalid physical part name")
        if len(preview_translation) != 3 or not all(math.isfinite(c) for c in preview_translation):
            raise ValueError("preview_translation must be a finite XYZ translation")
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        data = bpy.data.meshes.new_from_object(evaluated)
        data.transform(obj.matrix_world)
        bm = bmesh.new()
        bm.from_mesh(data)
        # Blender curve end caps use coincident but disconnected vertices. Weld
        # only sub-micron duplicates; this does not close genuinely open surfaces.
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-7)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        closed = bool(bm.faces) and all(edge.is_manifold for edge in bm.edges)
        volume = bm.calc_volume(signed=True)
        unseen, components = set(bm.verts), 0
        while unseen:
            components += 1
            pending = [unseen.pop()]
            while pending:
                vertex = pending.pop()
                for edge in vertex.link_edges:
                    other = edge.other_vert(vertex)
                    if other in unseen:
                        unseen.remove(other)
                        pending.append(other)
        bm.to_mesh(data)
        bm.free()
        if not closed or components != 1 or not math.isfinite(volume) or volume <= 0:
            raise ValueError(f"{name}: watertight={closed}, components={components}, volume={volume}")
        data.calc_loop_triangles()
        filename = name + ".obj"
        with (output / filename).open("w") as stream:
            stream.write("# Blender backend; metres; Z-up; coordinates baked in part-local frame\n")
            for vertex in data.vertices:
                stream.write("v %.9g %.9g %.9g\n" % tuple(vertex.co))
            for normal in data.corner_normals:
                stream.write("vn %.9g %.9g %.9g\n" % tuple(normal.vector))
            for tri in data.loop_triangles:
                stream.write("f " + " ".join(f"{v + 1}//{loop + 1}" for v, loop in
                                             zip(tri.vertices, tri.loops, strict=True)) + "\n")
        coords = [vertex.co for vertex in data.vertices]
        bounds = [[min(v[i] for v in coords) for i in range(3)],
                  [max(v[i] for v in coords) for i in range(3)]]
        meshes.append({"name": name, "file": filename, "sha256": digest(output / filename),
                       "vertices": len(data.vertices), "triangles": len(data.loop_triangles),
                       "watertight": closed, "components": components, "volume_m3": volume,
                       "bounds_m": bounds, "rgba": list(rgba), "material_class": material_class,
                       "part": part, "preview_translation": list(preview_translation)})
        material = bpy.data.materials.new(name + "_finish")
        material.diffuse_color = rgba
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = rgba
        shader.inputs["Roughness"].default_value = 0.65
        rendered = bpy.data.objects.new("export_" + name, data)
        bpy.context.collection.objects.link(rendered)
        rendered.data.materials.clear()
        rendered.data.materials.append(material)
        rendered.location = preview_translation
        objects.append(rendered)
        obj.hide_render = True
        obj.hide_set(True)

    runpy.run_path(str(output / "generator.py"), init_globals={"export_mesh": export_mesh,
                                                            "parameters": params})
    if not meshes:
        raise ValueError("Generator did not call export_mesh")
    low = Vector([min(m["bounds_m"][0][i] + m["preview_translation"][i] for m in meshes) for i in range(3)])
    high = Vector([max(m["bounds_m"][1][i] + m["preview_translation"][i] for m in meshes) for i in range(3)])
    span, center = max(high - low), (high + low) / 2
    previews = []
    if request["render"]:
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 24
        scene.cycles.use_denoising = True
        scene.render.resolution_x = 720
        scene.render.resolution_y = 600
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.world.color = (0.35, 0.35, 0.35)
        scene.view_settings.view_transform = "AgX"
        for i, offset in enumerate([(0.5, -0.5, 1.8), (-0.7, 0.8, 1.3)]):
            data = bpy.data.lights.new(f"Softbox{i}", "AREA")
            data.energy, data.shape, data.size = 80 * span * span, "DISK", span * 2
            light = bpy.data.objects.new(data.name, data)
            bpy.context.collection.objects.link(light)
            light.location = center + Vector(offset) * span
            light.rotation_euler = (center - light.location).to_track_quat("-Z", "Y").to_euler()
        data = bpy.data.cameras.new("AssetCamera")
        camera = bpy.data.objects.new(data.name, data)
        bpy.context.collection.objects.link(camera)
        scene.camera = camera
        data.type, data.ortho_scale = "ORTHO", span * 1.4
        for name, offset in [("preview", (0.7, -1, 1.25)), ("front", (0, -2, 0)),
                             ("top", (0, 0, 2)),
                             ("underside", (0.6, -1, -1.2))]:
            camera.location = center + Vector(offset) * span
            camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
            scene.render.filepath = str(output / f"{name}.png")
            bpy.ops.render.render(write_still=True)
            previews.append(name + ".png")
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "asset.blend"))
    artifacts = ["generator.py", "worker.py", "parameters.json", "request.json", "asset.blend", *previews]
    result = {"status": "completed", "backend": "blender", "blender_version": bpy.app.version_string,
              "coordinate_system": "part-local, metres, Z-up; no automatic normalization",
              "meshes": meshes, "bounds_m": [list(low), list(high)], "previews": previews,
              "artifact_hashes": {ref: digest(output / ref) for ref in artifacts},
              "validation_scope": "mesh closure, connectivity, positive volume and export identity; appearance not independently reviewed"}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    directory = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    try:
        run(directory)
    except BaseException:
        (directory / "failure.json").write_text(json.dumps({"traceback": traceback.format_exc()}, indent=2) + "\n")
        raise
