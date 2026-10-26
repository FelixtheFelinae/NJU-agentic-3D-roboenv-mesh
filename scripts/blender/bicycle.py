"""Video-conditioned yellow step-through bicycle; all dimensions are estimates.

The worker supplies export_mesh and parameters. Geometry is exported in each
rigid part's local frame; preview_translation assembles the Blender inspection.
"""

import math

import bpy
from mathutils import Vector

PARAMS = globals()["parameters"]
EMIT = globals()["export_mesh"]
SCALE = float(PARAMS.get("wheel_radius_m", 0.30)) / 0.30
YELLOW = (0.95, 0.64, 0.008, 1)
BLACK = (0.018, 0.022, 0.022, 1)
SILVER = (0.37, 0.41, 0.39, 1)
ORIGINS = {
    "frame": (0, 0, 0),
    "steering": (0.35, 0, 0.82),
    "rear_wheel": (-0.54, 0, 0.30),
    "front_wheel": (0.54, 0, 0.30),
    "crank": (-0.015, 0, 0.285),
}


def emit(name, obj, part="frame", color=YELLOW, material="painted_metal"):
    bores = []
    if part == "frame" and name in {
        "chain_guard_front",
        "seat_tube",
        "chain_guard",
        "chain_stay_left",
        "chain_stay_right",
    }:
        bores.append(((-0.015, 0, 0.285), 0.021))
    if part == "frame" and name in {
        "chain_stay_left",
        "chain_stay_right",
        "seat_stay_left",
        "seat_stay_right",
        "rear_fender_rail_left",
        "rear_fender_rail_right",
        "chain_guard",
    }:
        bores.append(((-0.54, 0, 0.30), 0.0088))
    if part == "steering" and name in {"fork_left", "fork_right"}:
        bores.append(((0.54, 0, 0.30), 0.0088))
    if bores:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.convert(target="MESH")
        obj = bpy.context.object
        for location, radius in bores:
            bpy.ops.mesh.primitive_cylinder_add(
                vertices=32,
                radius=radius,
                depth=0.4,
                location=location,
                rotation=(math.pi / 2, 0, 0),
            )
            cutter = bpy.context.object
            modifier = obj.modifiers.new("AxleClearance", "BOOLEAN")
            modifier.operation, modifier.solver, modifier.object = "DIFFERENCE", "EXACT", cutter
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            bpy.data.objects.remove(cutter, do_unlink=True)
    origin = Vector(ORIGINS[part])
    # All modelling helpers use the assembled bicycle frame. Bake scale and then
    # shift into a joint-local frame before passing to the generic exporter.
    bpy.context.view_layer.update()
    obj.matrix_world.translation -= origin
    obj.scale *= SCALE
    obj.location *= SCALE
    EMIT(
        name,
        obj,
        rgba=color,
        material_class=material,
        part=part,
        preview_translation=tuple(origin * SCALE),
    )


def tube(name, points, radius, part="frame", color=YELLOW, curved=False, closed=False):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.resolution_u = 8
    data.bevel_depth = radius
    data.bevel_resolution = 2
    data.use_fill_caps = True
    spline = data.splines.new("BEZIER" if curved else "POLY")
    if curved:
        spline.bezier_points.add(len(points) - 1)
        for p, co in zip(spline.bezier_points, points, strict=True):
            p.co = co
            p.handle_left_type = p.handle_right_type = "AUTO"
    else:
        spline.points.add(len(points) - 1)
        for p, co in zip(spline.points, points, strict=True):
            p.co = (*co, 1)
    spline.use_cyclic_u = closed
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    emit(name, obj, part, color)


def cylinder(name, start, end, radius, part="frame", color=SILVER, vertices=16):
    a, b = Vector(start), Vector(end)
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices, radius=radius, depth=(b - a).length, location=(a + b) / 2
    )
    obj = bpy.context.object
    obj.rotation_euler = (b - a).to_track_quat("Z", "Y").to_euler()
    for face in obj.data.polygons:
        face.use_smooth = len(face.vertices) == 4
    emit(name, obj, part, color)


def head_bearing():
    a, b = Vector((0.414, 0, 0.63)), Vector((0.30, 0, 0.97))
    direction = (b - a).normalized()
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48, radius=0.032, depth=(b - a).length, location=(a + b) / 2
    )
    shell = bpy.context.object
    shell.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48, radius=0.0198, depth=(b - a).length + 0.04, location=(a + b) / 2
    )
    cutter = bpy.context.object
    cutter.rotation_euler = shell.rotation_euler
    modifier = shell.modifiers.new("SteeringBearingBore", "BOOLEAN")
    modifier.operation, modifier.solver, modifier.object = "DIFFERENCE", "EXACT", cutter
    emit("head_tube", shell)
    bpy.data.objects.remove(cutter, do_unlink=True)


def rounded_box(name, location, size, radius, part="frame", color=YELLOW):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = obj.modifiers.new("RoundedEdges", "BEVEL")
    bevel.width, bevel.segments = radius, 3
    emit(name, obj, part, color)


def torus(name, center, major, minor, part, color):
    bpy.ops.mesh.primitive_torus_add(
        major_segments=80,
        minor_segments=12,
        major_radius=major,
        minor_radius=minor,
        location=center,
        rotation=(math.pi / 2, 0, 0),
    )
    obj = bpy.context.object
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    emit(name, obj, part, color, "rubber" if "tire" in name else "painted_metal")


def fender(name, x, part):
    count = 48
    vertices = []
    for i in range(count):
        angle = -0.23 + (math.pi + 0.46) * i / (count - 1)
        for radius, y in [(0.326, -0.035), (0.326, 0.035), (0.320, 0.035), (0.320, -0.035)]:
            vertices.append((x + radius * math.cos(angle), y, 0.30 + radius * math.sin(angle)))
    faces = []
    for i in range(count - 1):
        for j in range(4):
            faces.append(
                (i * 4 + j, i * 4 + (j + 1) % 4, (i + 1) * 4 + (j + 1) % 4, (i + 1) * 4 + j)
            )
    faces.extend([(3, 2, 1, 0), tuple(range((count - 1) * 4, count * 4))])
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    for face in data.polygons:
        face.use_smooth = len(face.vertices) == 4
    emit(name, obj, part)


def saddle():
    # Wide rear support narrowing to the forward nose, with a rounded crown.
    sections = [
        (-0.385, 0.006, 0.014),
        (-0.365, 0.080, 0.034),
        (-0.320, 0.101, 0.043),
        (-0.260, 0.093, 0.043),
        (-0.210, 0.058, 0.033),
        (-0.145, 0.035, 0.025),
        (-0.115, 0.026, 0.023),
        (-0.100, 0.005, 0.009),
    ]
    n, vertices, faces = 20, [], []
    for x, width, thickness in sections:
        for i in range(n):
            t = i * math.tau / n
            vertices.append((x, width * math.cos(t), 0.925 + thickness * math.sin(t)))
    for r in range(len(sections) - 1):
        for i in range(n):
            faces.append(
                (r * n + i, r * n + (i + 1) % n, (r + 1) * n + (i + 1) % n, (r + 1) * n + i)
            )
    faces.extend(
        [tuple(reversed(range(n))), tuple(range((len(sections) - 1) * n, len(sections) * n))]
    )
    data = bpy.data.meshes.new("Saddle")
    data.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new("Saddle", data)
    bpy.context.collection.objects.link(obj)
    modifier = obj.modifiers.new("SoftSaddle", "SUBSURF")
    modifier.levels = modifier.render_levels = 2
    for face in data.polygons:
        face.use_smooth = True
    emit("saddle", obj, color=BLACK, material="rubber")


def basket_ring(name, z, length, width):
    # Rounded rectangular wire loop, modelled in XY around the basket centre.
    points = []
    radius = 0.026
    for sx, sy, start in [(1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)]:
        for i in range(7):
            a = math.radians(start + i * 15)
            points.append(
                (
                    0.64 + sx * (length / 2 - radius) + radius * math.cos(a),
                    sy * (width / 2 - radius) + radius * math.sin(a),
                    z,
                )
            )
    tube(name, points, 0.0055, "steering", closed=True)


def build():
    if not 0.25 <= float(PARAMS.get("wheel_radius_m", 0.30)) <= 0.36:
        raise ValueError("Wheel radius must be within the explicitly supported bicycle scale")
    # Frame, rear stays and parking stand.
    tube(
        "step_through_frame",
        [
            (-0.10, 0, 0.34),
            (0.04, 0, 0.34),
            (0.14, 0, 0.40),
            (0.20, 0, 0.55),
            (0.27, 0, 0.70),
            (0.29, 0, 0.82),
        ],
        0.030,
        curved=True,
    )
    tube("seat_tube", [(-0.015, 0, 0.285), (-0.14, 0, 0.64)], 0.027)
    cylinder("seat_post", (-0.14, 0, 0.60), (-0.245, 0, 0.915), 0.018, color=BLACK)
    head_bearing()
    saddle()
    for side in [-1, 1]:
        suffix = "left" if side < 0 else "right"
        tube(
            "chain_stay_" + suffix,
            [(-0.015, side * 0.045, 0.285), (-0.54, side * 0.060, 0.30)],
            0.015,
        )
        tube(
            "seat_stay_" + suffix, [(-0.13, side * 0.035, 0.60), (-0.54, side * 0.062, 0.30)], 0.013
        )
        tube(
            "rear_fender_rail_" + suffix,
            [
                (-0.54, side * 0.065, 0.30),
                (-0.80, side * 0.045, 0.36),
                (-0.81, side * 0.035, 0.49),
                (-0.65, side * 0.035, 0.60),
            ],
            0.012,
        )
    tube("parking_stand", [(-0.16, -0.055, 0.30), (-0.25, -0.19, 0.045)], 0.012, color=BLACK)
    rounded_box("stand_foot", (-0.25, -0.19, 0.040), (0.055, 0.035, 0.016), 0.005, color=BLACK)
    fender("rear_fender", -0.54, "frame")
    rounded_box(
        "rear_light", (-0.84, 0, 0.42), (0.012, 0.033, 0.045), 0.006, color=(0.60, 0.006, 0.006, 1)
    )
    rounded_box("chain_guard", (-0.27, -0.078, 0.32), (0.57, 0.038, 0.12), 0.030, color=SILVER)
    cylinder(
        "chain_guard_front",
        (-0.015, -0.060, 0.285),
        (-0.015, -0.101, 0.285),
        0.105,
        color=SILVER,
        vertices=48,
    )
    # Steering subtree: fork, curved handlebars, basket and front mudguard.
    for side in [-1, 1]:
        suffix = "left" if side < 0 else "right"
        tube(
            "fork_" + suffix,
            [(0.424, side * 0.065, 0.60), (0.43, side * 0.067, 0.55), (0.54, side * 0.058, 0.30)],
            0.015,
            "steering",
            SILVER,
            curved=True,
        )
        cylinder(
            "fork_boot_" + suffix,
            (0.45, side * 0.067, 0.51),
            (0.52, side * 0.059, 0.34),
            0.022,
            "steering",
            SILVER,
        )
        cylinder(
            "grip_" + suffix,
            (0.245, side * 0.20, 1.13),
            (0.245, side * 0.30, 1.13),
            0.020,
            "steering",
            BLACK,
        )
        tube(
            "brake_lever_" + suffix,
            [(0.255, side * 0.19, 1.12), (0.30, side * 0.23, 1.10), (0.31, side * 0.28, 1.10)],
            0.005,
            "steering",
            YELLOW,
            curved=True,
        )
    tube(
        "handlebar",
        [
            (0.245, -0.23, 1.13),
            (0.30, -0.14, 1.13),
            (0.30, -0.07, 1.02),
            (0.30, 0.07, 1.02),
            (0.30, 0.14, 1.13),
            (0.245, 0.23, 1.13),
        ],
        0.016,
        "steering",
        SILVER,
        curved=True,
    )
    cylinder("stem", (0.424, 0, 0.60), (0.283, 0, 1.02), 0.019, "steering", SILVER)
    tube(
        "fork_crown",
        [(0.424, -0.065, 0.60), (0.424, 0, 0.60), (0.424, 0.065, 0.60)],
        0.017,
        "steering",
        SILVER,
    )
    tube("fender_mount", [(0.424, 0, 0.60), (0.54, 0, 0.625)], 0.010, "steering", SILVER)
    rounded_box("handle_console", (0.29, 0, 1.025), (0.10, 0.11, 0.045), 0.015, "steering", YELLOW)
    rounded_box("handle_screen", (0.29, 0, 1.050), (0.074, 0.085, 0.006), 0.010, "steering", BLACK)
    fender("front_fender", 0.54, "steering")
    tube(
        "basket_support",
        [(0.424, 0, 0.60), (0.424, -0.055, 0.60), (0.49, -0.055, 0.78), (0.62, 0, 0.78)],
        0.016,
        "steering",
    )
    rounded_box("basket_back_plate", (0.505, 0, 0.89), (0.024, 0.16, 0.20), 0.022, "steering")
    # Open cup holder visible beside the basket; sparse slats preserve its void.
    for z, rad in [(0.74, 0.035), (0.87, 0.045)]:
        points = [
            (0.41 + rad * math.cos(a), -0.12 + rad * math.sin(a), z)
            for a in [i * math.tau / 32 for i in range(32)]
        ]
        tube(
            "cup_holder_ring_" + str(round(z * 100)), points, 0.004, "steering", SILVER, closed=True
        )
    for i in range(6):
        a = i * math.tau / 6
        tube(
            f"cup_holder_slat_{i}",
            [
                (0.41 + r * math.cos(a), -0.12 + r * math.sin(a), z)
                for r, z in [(0.035, 0.74), (0.045, 0.87)]
            ],
            0.004,
            "steering",
            SILVER,
        )
    tube("cup_holder_mount", [(0.45, -0.12, 0.86), (0.505, -0.08, 0.86)], 0.006, "steering", SILVER)
    for index, (z, length, width) in enumerate(
        [(0.78, 0.26, 0.28), (0.90, 0.30, 0.34), (1.025, 0.33, 0.38)]
    ):
        basket_ring(f"basket_ring_{index}", z, length, width)
    for side in [-1, 1]:
        suffix = "left" if side < 0 else "right"
        for index, t in enumerate([-0.8, -0.4, 0, 0.4, 0.8]):
            tube(
                f"basket_side_{suffix}_{index}",
                [(0.64 + 0.13 * t, side * 0.14, 0.78), (0.64 + 0.165 * t, side * 0.19, 1.025)],
                0.004,
                "steering",
            )
        for index, t in enumerate([-0.7, 0, 0.7]):
            tube(
                f"basket_end_{suffix}_{index}",
                [(0.64 + side * 0.13, 0.14 * t, 0.78), (0.64 + side * 0.165, 0.19 * t, 1.025)],
                0.004,
                "steering",
            )
    for index in range(5):
        y = -0.12 + index * 0.06
        tube(f"basket_bottom_{index}", [(0.51, y, 0.78), (0.77, y, 0.78)], 0.004, "steering")
    # Rotating wheels keep their actual hollow centre; the rings are not disks.
    for part, x in [("rear_wheel", -0.54), ("front_wheel", 0.54)]:
        torus(part + "_tire", (x, 0, 0.30), 0.280, 0.020, part, BLACK)
        torus(part + "_rim", (x, 0, 0.30), 0.260, 0.013, part, YELLOW)
        cylinder(part + "_hub", (x, -0.036, 0.30), (x, 0.036, 0.30), 0.032, part, SILVER)
        cylinder(part + "_axle", (x, -0.070, 0.30), (x, 0.070, 0.30), 0.008, part, SILVER)
        for i in range(24):
            a = i * math.tau / 24
            side = -1 if i % 2 else 1
            cylinder(
                part + f"_spoke_{i:02}",
                (x, side * 0.025, 0.30),
                (x + 0.26 * math.cos(a), 0, 0.30 + 0.26 * math.sin(a)),
                0.0017,
                part,
                SILVER,
                vertices=8,
            )
        rounded_box(
            part + "_reflector",
            (x + 0.17, 0, 0.30),
            (0.080, 0.012, 0.025),
            0.004,
            part,
            (0.72, 0.72, 0.68, 1),
        )
    # Independent crank: no claim of reconstructed chain or drivetrain coupling.
    cylinder("crank_axle", (-0.015, -0.11, 0.285), (-0.015, 0.11, 0.285), 0.019, "crank", BLACK)
    for side in [-1, 1]:
        suffix = "left" if side < 0 else "right"
        end = (-0.015 + side * 0.15, side * 0.11, 0.285)
        tube("crank_arm_" + suffix, [(-0.015, side * 0.11, 0.285), end], 0.012, "crank", BLACK)
        rounded_box(
            "pedal_" + suffix,
            (end[0], side * 0.15, end[2]),
            (0.075, 0.085, 0.020),
            0.005,
            "crank",
            BLACK,
        )
    if PARAMS.get("drivetrain", False):
        # Two pitch radii give the explicitly assumed 2.5:1 transmission ratio.
        # This continuous envelope is a visual solid; the simulator uses a
        # native bilateral gear constraint instead of per-link collisions.
        rear = Vector((-0.54, 0.30))
        front = Vector((-0.015, 0.285))
        delta = front - rear
        theta = math.atan2(delta.y, delta.x)
        tangent = math.acos((0.036 - 0.090) / delta.length)
        points = []
        for centre, radius, start, stop in [
            (rear, 0.036, theta + tangent, theta + math.tau - tangent),
            (front, 0.090, theta - tangent, theta + tangent),
        ]:
            for i in range(49):
                a = start + (stop - start) * i / 48
                points.append(
                    (centre.x + radius * math.cos(a), -0.052, centre.y + radius * math.sin(a))
                )
        tube("drive_chain_envelope", points, 0.003, color=BLACK, closed=True)
        cylinder(
            "drive_chainring",
            (-0.015, -0.055, 0.285),
            (-0.015, -0.049, 0.285),
            0.087,
            "crank",
            SILVER,
            vertices=64,
        )
        cylinder(
            "drive_rear_sprocket",
            (-0.54, -0.055, 0.30),
            (-0.54, -0.049, 0.30),
            0.033,
            "rear_wheel",
            SILVER,
            vertices=48,
        )


build()
