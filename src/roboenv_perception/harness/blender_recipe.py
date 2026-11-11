"""Trusted interpreter for validated declarative geometry recipes.

Executed by construction.blender_worker, which supplies parameters/export_mesh.
No model-authored Python code reaches this file.
"""

import math

import bpy
from mathutils import Vector

parameters = globals()["parameters"]
export_mesh = globals()["export_mesh"]


for solid in parameters["solids"]:
    shape = solid["shape"]
    name = solid["name"]
    if shape == "annulus":
        n = 64
        vertices = [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z)
                    for z in (-solid['length_m']/2, solid['length_m']/2)
                    for r in (solid['radius_m'], solid['inner_radius_m']) for i in range(n)]
        faces = []
        for i in range(n):
            j = (i+1) % n
            faces.extend([(i, j, 2*n+j, 2*n+i), (n+j, n+i, 3*n+i, 3*n+j),
                          (j, i, n+i, n+j), (2*n+i, 2*n+j, 3*n+j, 3*n+i)])
        mesh = bpy.data.meshes.new(name + '_mesh')
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
    elif shape == "hollow_box":
        sx, sy, sz = solid["size_m"]
        wall = solid["length_m"]
        bpy.ops.mesh.primitive_cube_add(size=1)
        obj = bpy.context.object
        obj.dimensions = (sx, sy, sz)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bpy.ops.mesh.primitive_cube_add(size=1)
        cutter = bpy.context.object
        cutter.dimensions = (sx - 2 * wall, sy - 2 * wall, sz)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        cutter.location.z = wall
        boolean = obj.modifiers.new("open_cavity", "BOOLEAN")
        boolean.operation = "DIFFERENCE"
        boolean.solver = "EXACT"
        boolean.object = cutter
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=boolean.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
    elif shape == "hollow_prism":
        sx, sy, height = solid["size_m"]
        wall = solid["length_m"]
        bpy.ops.mesh.primitive_cylinder_add(vertices=solid["sides"], radius=1, depth=height)
        obj = bpy.context.object
        obj.dimensions = (sx, sy, height)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bpy.ops.mesh.primitive_cylinder_add(vertices=solid["sides"], radius=1, depth=height)
        cutter = bpy.context.object
        cutter.dimensions = (sx - 2 * wall, sy - 2 * wall, height)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        cutter.location.z = wall
        boolean = obj.modifiers.new("open_cavity", "BOOLEAN")
        boolean.operation = "DIFFERENCE"
        boolean.solver = "EXACT"
        boolean.object = cutter
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=boolean.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
    elif shape == "hollow_cylinder":
        outer_radius = solid["radius_m"]
        height = solid["size_m"][2]
        wall = solid["length_m"]
        vertices = 64
        bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=outer_radius, depth=height)
        obj = bpy.context.object
        bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=outer_radius - wall, depth=height)
        cutter = bpy.context.object
        cutter.location.z = wall
        boolean = obj.modifiers.new("open_cavity", "BOOLEAN")
        boolean.operation = "DIFFERENCE"
        boolean.solver = "EXACT"
        boolean.object = cutter
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=boolean.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
    elif shape == "box":
        bpy.ops.mesh.primitive_cube_add(size=1)
        obj = bpy.context.object
        obj.dimensions = solid["size_m"]
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        bevel = obj.modifiers.new("small_edge_radius", "BEVEL")
        bevel.width = min(solid["size_m"]) * 0.04
        bevel.segments = 3
        obj.modifiers.new("weighted_normals", "WEIGHTED_NORMAL")
    elif shape == "ellipsoid":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=20, radius=1)
        obj = bpy.context.object
        obj.scale = tuple(x * 0.5 for x in solid["size_m"])
    elif shape in {"cylinder", "prism"}:
        bpy.ops.mesh.primitive_cylinder_add(vertices=solid["sides"] if shape == "prism" else 48,
                                           radius=solid["radius_m"], depth=solid["length_m"])
        obj = bpy.context.object
    elif shape == "lathe":
        profile = solid["profile_m"]
        sides = solid["sides"]
        # Shared vertices along one consistent angular phase prevent cracks.
        vertices = [
            (radius * math.cos(2 * math.pi * index / sides),
             radius * math.sin(2 * math.pi * index / sides), z)
            for z, radius in profile for index in range(sides)
        ]
        faces = []
        for ring in range(len(profile) - 1):
            for index in range(sides):
                nxt = (index + 1) % sides
                faces.append((ring * sides + index, ring * sides + nxt,
                              (ring + 1) * sides + nxt, (ring + 1) * sides + index))
        # Bottom outward normal -Z, top +Z. Positive-radius increasing-Z rings
        # make a connected, orientable closed surface without Boolean seams.
        faces.append(tuple(reversed(range(sides))))
        faces.append(tuple((len(profile) - 1) * sides + index for index in range(sides)))
        mesh = bpy.data.meshes.new(name + "_mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
    elif shape == "frustum":
        bpy.ops.mesh.primitive_cone_add(vertices=solid["sides"], radius1=solid["radius_m"],
                                      radius2=solid["radius_top_m"], depth=solid["length_m"])
        obj = bpy.context.object
    elif shape == "torus":
        bpy.ops.mesh.primitive_torus_add(major_segments=64, minor_segments=12,
                                         location=(0, 0, 0),
                                         major_radius=solid["radius_m"],
                                         minor_radius=solid["length_m"] * 0.5)
        obj = bpy.context.object
    elif shape == "tube":
        start = Vector(solid["center_m"])
        end = Vector(solid["tube_end_m"])
        direction = end - start
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=solid["radius_m"], depth=direction.length)
        obj = bpy.context.object
        obj.rotation_mode = "QUATERNION"
        obj.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(direction.normalized())
        obj.location = (start + end) * 0.5
    else:
        raise ValueError(f"unsupported solid shape: {shape}")
    obj.name = name
    if shape != "tube":
        obj.location = solid["center_m"]
        obj.rotation_mode = "XYZ"
        obj.rotation_euler = solid["rotation_rpy"]
    export_mesh(name, obj, rgba=tuple(solid["rgba"]), part=solid["part"],
                material_class=solid.get("material_class", "matte_plastic"),
                preview_translation=parameters["preview_offsets"][solid["part"]])
