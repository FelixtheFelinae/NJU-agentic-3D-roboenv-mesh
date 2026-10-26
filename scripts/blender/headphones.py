"""Source-video-inspired rigid headphones; estimated shape, not a product CAD model.

Executed by blender_worker.py, which supplies parameters and export_mesh.
All separate visual pieces belong to the same physical body in Artisan.
"""

import math

import bpy


def mesh_object(name, vertices, faces, subdivisions=1):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    if subdivisions:
        modifier = obj.modifiers.new("RoundedSurface", "SUBSURF")
        modifier.levels = modifier.render_levels = subdivisions
    for face in data.polygons:
        face.use_smooth = True
    return obj


def cup(name, x, y, width_scale, depth_scale):
    # Closely spaced support rings preserve the flattened outer face while rolling
    # the perimeter into a molded side wall. Bottom surface sits above the pad hole.
    profile = [(0.012, 0.001, 0.001), (0.012, 0.026, 0.033),
               (0.013, 0.032, 0.040), (0.016, 0.037, 0.046),
               (0.022, 0.039, 0.048), (0.035, 0.038, 0.047),
               (0.041, 0.035, 0.044), (0.044, 0.030, 0.038),
               (0.045, 0.025, 0.032), (0.0455, 0.001, 0.001)]
    count = 48
    vertices = [(x + rx * width_scale * math.cos(i * math.tau / count),
                 y + ry * depth_scale * math.sin(i * math.tau / count), z)
                for z, rx, ry in profile for i in range(count)]
    faces = []
    for ring in range(len(profile) - 1):
        for i in range(count):
            j = (i + 1) % count
            faces.append((ring * count + i, ring * count + j,
                          (ring + 1) * count + j, (ring + 1) * count + i))
    faces.extend([tuple(reversed(range(count))),
                  tuple((len(profile) - 1) * count + i for i in range(count))])
    return mesh_object(name, vertices, faces)


def cushion(name, x, y, width_scale, depth_scale):
    vertices, faces = [], []
    along, around = 48, 12
    for i in range(along):
        theta = i * math.tau / along
        for j in range(around):
            phi = j * math.tau / around
            vertices.append((x + (0.030 + 0.007 * math.cos(phi)) * width_scale * math.cos(theta),
                             y + (0.038 + 0.007 * math.cos(phi)) * depth_scale * math.sin(theta),
                             0.008 + 0.007 * math.sin(phi)))
    for i in range(along):
        for j in range(around):
            faces.append((i * around + j, ((i + 1) % along) * around + j,
                          ((i + 1) % along) * around + (j + 1) % around,
                          i * around + (j + 1) % around))
    return mesh_object(name, vertices, faces)


def curved_band(name, rx, ry, start, end, radial, vertical, z):
    # A tapered closed oval section swept along an elliptical centreline.
    steps, sides = 40, 12
    vertices, faces = [], []
    for i in range(steps):
        t = i / (steps - 1)
        angle = start + (end - start) * t
        taper = 0.82 + 0.18 * math.sin(math.pi * t)
        for j in range(sides):
            phi = j * math.tau / sides
            dr = radial * taper * math.cos(phi)
            vertices.append(((rx + dr) * math.cos(angle),
                             0.008 + (ry + dr) * math.sin(angle),
                             z + vertical * taper * math.sin(phi)))
    for i in range(steps - 1):
        for j in range(sides):
            k = (j + 1) % sides
            faces.append((i * sides + j, i * sides + k,
                          (i + 1) * sides + k, (i + 1) * sides + j))
    faces.extend([tuple(reversed(range(sides))),
                  tuple((steps - 1) * sides + i for i in range(sides))])
    return mesh_object(name, vertices, faces)


def connector(name, side):
    # Blender's bevelled Bezier curve gives the yoke a continuous molded bend.
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.resolution_u = 12
    data.bevel_depth = 0.0042
    data.bevel_resolution = 3
    data.use_fill_caps = True
    spline = data.splines.new("BEZIER")
    points = [(side * 0.089, 0.010, 0.023), (side * 0.088, -0.009, 0.024),
              (side * 0.080, -0.022, 0.026), (side * 0.075, -0.029, 0.030)]
    spline.bezier_points.add(len(points) - 1)
    for point, co in zip(spline.bezier_points, points, strict=True):
        point.co = co
        point.handle_left_type = point.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    return obj


def build():
    values = globals()["parameters"]
    emit = globals()["export_mesh"]
    allowed = {"cup_width_scale", "cup_depth_scale", "shell_rgba", "pad_rgba"}
    if set(values) - allowed:
        raise ValueError(f"Unknown headphone parameters: {set(values) - allowed}")
    width, depth = values.get("cup_width_scale", 1.0), values.get("cup_depth_scale", 1.0)
    if not all(isinstance(v, (int, float)) and 0.85 <= v <= 1.15 for v in (width, depth)):
        raise ValueError("Cup scale must be in [0.85, 1.15] to retain connected proportions")
    shell = tuple(values.get("shell_rgba", (0.32, 0.34, 0.33, 1)))
    pad = tuple(values.get("pad_rgba", (0.23, 0.25, 0.24, 1)))
    emit("headband", curved_band("Headband", 0.089, 0.099, 0, math.pi,
                                0.006, 0.011, 0.024), rgba=shell)
    emit("headband_pad", curved_band("HeadbandPad", 0.083, 0.093, 0.18, math.pi - 0.18,
                                            0.004, 0.009, 0.023),
         rgba=pad, material_class="rubber")
    for name, side in [("left", -1), ("right", 1)]:
        x, y = side * 0.058, -0.031
        emit(name + "_shell", cup(name + "Shell", x, y, width, depth), rgba=shell)
        emit(name + "_earpad", cushion(name + "Pad", x, y, width, depth),
             rgba=pad, material_class="rubber")
        emit(name + "_yoke", connector(name + "Yoke", side), rgba=(0.39, 0.41, 0.40, 1))


build()
