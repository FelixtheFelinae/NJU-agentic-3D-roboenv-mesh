"""Independent, evidence-tagged bicycle assembly study. Run with Blender 4.5.

No imports from the existing bicycle generators. Hidden internals are hypotheses.
Geometry is for assembly illustration, not manufacturing or physics validation.
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

PI = math.pi
PARTS = []
GROUPS = {
    "frame": (0, 0, 0),
    "rear_wheel": (-0.48, 0.08, -0.03),
    "front_wheel": (0.58, 0.08, -0.03),
    "fork": (0.31, 0, 0.30),
    "headset": (0.18, 0, 0.63),
    "cockpit": (0.22, 0, 0.96),
    "saddle": (-0.18, 0, 0.74),
    "drivetrain": (0, -0.49, -0.10),
    "chainguard": (0, -0.88, 0.09),
    "rear_fender": (-0.48, 0.02, 0.49),
    "front_fender": (0.68, 0.02, 0.50),
    "basket": (0.78, 0, 0.73),
    "accessories": (-0.22, 0.42, 0.30),
}
COLORS = {
    "yellow": (0.98, 0.66, 0.025, 1),
    "steel": (0.50, 0.57, 0.62, 1),
    "black": (0.065, 0.078, 0.09, 1),
    "rubber": (0.045, 0.052, 0.058, 1),
    "red": (0.7, 0.035, 0.035, 1),
    "white": (0.85, 0.9, 0.92, 1),
    "candidate": (0.20, 0.52, 0.78, 1),
}
MATS = {}
OUT = None


def material(name):
    if name not in MATS:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = COLORS[name]
        mat.use_nodes = True
        shader = mat.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = COLORS[name]
        shader.inputs["Metallic"].default_value = 0.65 if name == "steel" else 0.1
        shader.inputs["Roughness"].default_value = 0.33
        MATS[name] = mat
    return MATS[name]


def mesh(name, verts, faces):
    data = bpy.data.meshes.new(name)
    data.from_pydata(verts, [], faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    return obj


def orient(obj, center, axis):
    obj.location = center
    obj.rotation_euler = Vector(axis).to_track_quat("Z", "Y").to_euler()
    return obj


def ring(center, outer, inner, depth, axis=(0, 1, 0), segments=48):
    verts = []
    for z, r in [(-depth / 2, outer), (depth / 2, outer), (-depth / 2, inner), (depth / 2, inner)]:
        verts.extend(
            [
                (r * math.cos(2 * PI * i / segments), r * math.sin(2 * PI * i / segments), z)
                for i in range(segments)
            ]
        )
    faces = []
    for i in range(segments):
        j = (i + 1) % segments
        faces += [
            (i, j, segments + j, segments + i),
            (2 * segments + j, 2 * segments + i, 3 * segments + i, 3 * segments + j),
            (j, i, 2 * segments + i, 2 * segments + j),
            (segments + i, segments + j, 3 * segments + j, 3 * segments + i),
        ]
    return orient(mesh("ring", verts, faces), center, axis)


def cylinder(center, radius, length, axis=(0, 1, 0), vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=length)
    return orient(bpy.context.object, center, axis)


def box(center, size, bevel=0.004):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("rounded edges", "BEVEL")
        mod.width = bevel
        mod.segments = 3
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return obj


def path(points, radius, smooth=False):
    points = list(points)
    cyclic = (Vector(points[0]) - Vector(points[-1])).length < 1e-8
    if cyclic:
        points = points[:-1]
    data = bpy.data.curves.new("formed section", "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = radius
    data.bevel_resolution = 3
    data.resolution_u = 16
    data.use_fill_caps = True
    spline = data.splines.new("BEZIER" if smooth else "POLY")
    spline.use_cyclic_u = cyclic
    if smooth:
        spline.bezier_points.add(len(points) - 1)
        for p, co in zip(spline.bezier_points, points):
            p.co = co
            p.handle_left_type = "AUTO"
            p.handle_right_type = "AUTO"
    else:
        spline.points.add(len(points) - 1)
        for p, co in zip(spline.points, points):
            p.co = (*co, 1)
    obj = bpy.data.objects.new("tube", data)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target="MESH")
    obj = bpy.context.object
    # Curve conversion duplicates cap boundaries; weld each formed section before
    # joining it into its logical assembly (not a boolean union of welded tubes).
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-7)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    return obj


def bar(a, b, radius):
    a, b = Vector(a), Vector(b)
    return cylinder((a + b) / 2, radius, (b - a).length, b - a)


def merge(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    return objects[0]


def part(
    key,
    label,
    group,
    obj,
    evidence,
    status="observed_feature",
    color="steel",
    local=(0, 0, 0),
    note="",
):
    if any(p["id"] == key for p in PARTS):
        raise ValueError(f"Duplicate part: {key}")
    obj.name = key
    obj.data.name = key
    if status == "inferred_hidden":
        color = "candidate"
    obj.data.materials.clear()
    obj.data.materials.append(material(color))
    obj.color = COLORS[color]
    for poly in obj.data.polygons:
        poly.use_smooth = len(poly.vertices) == 4
    coll = bpy.data.collections.get(group)
    if coll is None:
        coll = bpy.data.collections.new(group)
        bpy.context.scene.collection.children.link(coll)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)
    bpy.context.view_layer.update()
    center = sum((obj.matrix_world @ Vector(c) for c in obj.bound_box), Vector()) / 8
    row = {
        "id": key,
        "label_zh": label,
        "assembly": group,
        "status": status,
        "evidence": evidence.split(","),
        "note": note,
        "geometry_basis": "approximate, unmeasured; thread profiles omitted",
        "assembled_origin_m": list(obj.location),
        "center_m": list(center),
        "explosion_local_m": list(local),
        "explosion_group_m": list(GROUPS[group]),
    }
    for k in ["status", "label_zh", "note", "geometry_basis"]:
        obj[k] = row[k]
    obj["evidence"] = evidence
    obj["assembly"] = group
    obj["explosion_offset_m"] = list(Vector(local) + Vector(GROUPS[group]))
    PARTS.append(row)
    return obj


def bolt(
    key,
    group,
    center,
    evidence,
    axis=(0, 1, 0),
    radius=0.003,
    length=0.018,
    local=(0, 0, 0),
    status="observed_feature",
    label="紧固螺钉（无螺纹）",
):
    a, c = Vector(axis).normalized(), Vector(center)
    # Smooth shank plus annular socket head, joined as one logical fastener.
    obj = merge(
        [
            cylinder(c - a * length / 2, radius, length, a, 24),
            ring(c + a * 0.002, radius * 1.8, radius * 0.65, 0.004, a, 24),
        ]
    )
    return part(
        key,
        label,
        group,
        obj,
        evidence,
        status,
        local=local,
        note="仅外侧紧固特征有图像依据；杆长、规格、孔型及配合件为近似。",
    )


def washer(
    key, group, center, evidence, outer=0.011, inner=0.0055, axis=(0, 1, 0), local=(0, 0, 0)
):
    return part(
        key,
        "候选垫圈",
        group,
        ring(center, outer, inner, 0.002, axis),
        evidence,
        "inferred_hidden",
        local=local,
        note="候选轴向堆叠，视频不能确认独立垫圈数量。",
    )


def bearing(key, group, center, evidence, outer, inner, axis=(0, 1, 0), local=(0, 0, 0)):
    return part(
        key,
        "候选轴承包络",
        group,
        ring(center, outer, inner, 0.009, axis),
        evidence,
        "inferred_hidden",
        local=local,
        note="仅表示滚动支撑候选；散珠/轴套/密封轴承型式均未确认，不展开滚珠。",
    )


def annular_arc(center, radius, width, start, end):
    verts, faces, n = [], [], 80
    for i in range(n + 1):
        t = start + (end - start) * i / n
        for r, y in [
            (radius, -width / 2),
            (radius, width / 2),
            (radius + 0.008, -width / 2),
            (radius + 0.008, width / 2),
        ]:
            verts.append((center[0] + r * math.cos(t), center[1] + y, center[2] + r * math.sin(t)))
    for i in range(n):
        a, b = 4 * i, 4 * (i + 1)
        faces += [
            (a, b, b + 1, a + 1),
            (a + 2, a + 3, b + 3, b + 2),
            (a, a + 2, b + 2, b),
            (a + 1, b + 1, b + 3, a + 3),
        ]
    faces += [(0, 1, 3, 2), (4 * n + 2, 4 * n + 3, 4 * n + 1, 4 * n)]
    return mesh("fender", verts, faces)


def build_frame():
    sections = [
        path(
            [
                (-0.25, 0, 0.58),
                (-0.18, 0, 0.39),
                (-0.04, 0, 0.34),
                (0.15, 0, 0.43),
                (0.44, 0, 0.81),
            ],
            0.032,
            True,
        ),
        ring((0.443333, 0, 0.80), 0.038, 0.025, 0.17, (-0.28, 0, 0.96)),
        ring((-0.12, 0, 0.335), 0.032, 0.022, 0.08),
        ring((-0.23, 0, 0.55), 0.023, 0.017, 0.18, (-0.28, 0, 0.96)),
    ]
    for side in [-1, 1]:
        sections += [
            path(
                [
                    (-0.13, 0.045 * side, 0.34),
                    (-0.40, 0.07 * side, 0.32),
                    (-0.56, 0.065 * side, 0.305),
                ],
                0.018,
            ),
            path(
                [
                    (-0.23, 0.022 * side, 0.57),
                    (-0.41, 0.06 * side, 0.49),
                    (-0.56, 0.065 * side, 0.305),
                ],
                0.012,
            ),
        ]
    part(
        "FR01",
        "焊接车架总成（不把焊管伪装成可拆件）",
        "frame",
        merge(sections),
        "E12,E01",
        color="yellow",
    )


def build_wheel(prefix, x, evidence):
    group = "front_wheel" if prefix == "FW" else "rear_wheel"
    c = (x, 0, 0.305)
    bpy.ops.mesh.primitive_torus_add(
        major_segments=96, minor_segments=12, major_radius=0.286, minor_radius=0.019
    )
    tyre = orient(bpy.context.object, c, (0, 1, 0))
    part(prefix + "01", "轮胎包络（充气/实心未确认）", group, tyre, evidence, color="rubber")
    part(prefix + "02", "轮圈", group, ring(c, 0.279, 0.262, 0.027), evidence, color="yellow")
    # 36 holes is explicitly a repeat-count prior, not a claimed image count.
    for i in range(36):
        t = 2 * PI * i / 36
        side = -1 if i % 2 else 1
        th = t + side * 0.24
        a = (x + 0.033 * math.cos(th), side * 0.027, 0.305 + 0.033 * math.sin(th))
        b = (x + 0.267 * math.cos(t), side * 0.007, 0.305 + 0.267 * math.sin(t))
        part(
            f"{prefix}S{i + 1:02}",
            "辐条",
            group,
            bar(a, b, 0.001),
            evidence,
            "repeat_count_prior",
            note="交叉辐条可见；每轮36根为暂定数量，未完成遮挡消歧计数。",
        )
        end = Vector(b)
        direction = (end - Vector(a)).normalized()
        part(
            f"{prefix}N{i + 1:02}",
            "辐条帽",
            group,
            cylinder(end - direction * 0.004, 0.002, 0.008, direction, 12),
            evidence,
            "repeat_count_prior",
            note="辐条帽独立建模；数量随暂定辐条数，规格未知。",
        )
    shells = [ring(c, 0.025, 0.016, 0.055)]
    for y in [-0.029, 0.029]:
        shells.append(ring((x, y, 0.305), 0.038, 0.016, 0.005))
    part(
        prefix + "03",
        "花鼓壳与法兰",
        group,
        merge(shells),
        evidence,
        local=(0, 0.02, 0),
        note="外部花鼓轮廓有依据；轴承孔和内部台阶为候选配合。",
    )
    part(
        prefix + "04",
        "候选通轴",
        group,
        cylinder(c, 0.005, 0.155),
        evidence,
        "inferred_hidden",
        local=(0, -0.12, 0),
        note="轴端可见，但贯穿轴形式、直径和轴肩未被直接观察。",
    )
    for side in [-1, 1]:
        suffix = "L" if side < 0 else "R"
        bearing(
            prefix + "05" + suffix,
            group,
            (x, side * 0.021, 0.305),
            evidence,
            0.015,
            0.0055,
            local=(0, side * 0.14, 0),
        )
        washer(
            prefix + "06" + suffix,
            group,
            (x, side * 0.069, 0.305),
            evidence,
            local=(0, side * 0.22, 0),
        )
        part(
            prefix + "07" + suffix,
            "轴端紧固件（六角外形近似）",
            group,
            ring((x, side * 0.077, 0.305), 0.009, 0.0052, 0.008, segments=6),
            evidence,
            local=(0, side * 0.30, 0),
            note="外端紧固件可见；螺母/防盗端帽具体制式未确认。",
        )
    part(
        prefix + "08",
        "轮毂侧盖 / 制动器外壳",
        group,
        ring((x, 0.047, 0.305), 0.054, 0.006, 0.019),
        evidence,
        color="black",
        local=(0, 0.36, 0),
        note="仅恢复可见外壳；不声称已经识别鼓刹内部制动蹄。",
    )
    part(
        prefix + "09",
        "辐条反光片",
        group,
        box((x - 0.09, -0.008, 0.43), (0.09, 0.012, 0.018)),
        evidence,
        color="white",
    )


def build_fork_and_headset():
    a = Vector((-0.28, 0, 0.96))
    base = Vector((0.49, 0, 0.64))
    part("FK01", "叉冠", "fork", box(base, (0.09, 0.155, 0.038)), "E06")
    part(
        "FK02",
        "候选舵管",
        "fork",
        cylinder(base + a * 0.17, 0.014, 0.34, a),
        "E06",
        "inferred_hidden",
        local=tuple(a * 0.08),
    )
    for side in [-1, 1]:
        s = "L" if side < 0 else "R"
        lower = Vector((0.545, side * 0.061, 0.43))
        upper = Vector((0.514, side * 0.061, 0.54))
        part(
            "FK03" + s,
            "前叉外管",
            "fork",
            ring(lower, 0.021, 0.015, 0.25, a),
            "E06",
            local=(0, side * 0.045, -0.06),
        )
        part(
            "FK04" + s,
            "前叉内管",
            "fork",
            ring(upper, 0.0145, 0.010, 0.20, a),
            "E06",
            local=(0, side * 0.045, 0.08),
        )
        part(
            "FK05" + s,
            "管口防尘封",
            "fork",
            ring(lower + a * 0.126, 0.022, 0.0145, 0.012, a),
            "E06",
            color="black",
            local=(0, side * 0.045, 0.13),
        )
        part(
            "FK06" + s,
            "候选导向衬套",
            "fork",
            ring(lower + a * 0.08, 0.017, 0.0146, 0.025, a),
            "E06",
            "inferred_hidden",
            local=(0, side * 0.045, 0.22),
        )
        part(
            "FK07" + s,
            "未知弹性元件的占位包络",
            "fork",
            cylinder(lower, 0.009, 0.09, a),
            "E06",
            "inferred_hidden",
            local=(0, side * 0.15, 0.10),
            note="不能确定弹簧、弹性体或阻尼结构；用光滑蓝色包络表示，不伪造弹簧细节。",
        )
        bolt(
            "FK08" + s,
            "fork",
            (0.576, side * 0.061, 0.308),
            "E06",
            axis=-a,
            local=(0, side * 0.045, -0.12),
            status="inferred_hidden",
        )
    # Independent headset parts, all hidden configurations explicitly provisional.
    for i, (z, r, inner, label) in enumerate(
        [
            (0.70, 0.031, 0.015, "下碗"),
            (0.717, 0.025, 0.0145, "下轴承"),
            (0.885, 0.025, 0.0145, "上轴承"),
            (0.90, 0.031, 0.015, "上碗"),
            (0.918, 0.027, 0.0145, "防尘盖"),
            (0.94, 0.023, 0.0145, "锁紧环"),
        ]
    ):
        c = base + a * ((z - base.z) / a.z)
        part(
            f"HS{i + 1:02}",
            "候选头碗" + label,
            "headset",
            ring(c, r, inner, 0.009, a),
            "E06,E04",
            "inferred_hidden",
            local=tuple(a * (i - 2) * 0.04),
            note="头碗形式及零件数未从外观确定。",
        )


def build_cockpit():
    part(
        "CK01",
        "把立外套",
        "cockpit",
        cylinder((0.395208, 0, 0.965), 0.032, 0.19, (-0.28, 0, 0.96)),
        "E04",
    )
    part(
        "CK02",
        "一体弯把",
        "cockpit",
        path(
            [
                (0.30, -0.31, 1.13),
                (0.34, -0.17, 1.15),
                (0.37, -0.10, 1.045),
                (0.37, 0.10, 1.045),
                (0.34, 0.17, 1.15),
                (0.30, 0.31, 1.13),
            ],
            0.012,
            True,
        ),
        "E04",
    )
    for side in [-1, 1]:
        s = "L" if side < 0 else "R"
        part(
            "CK03" + s,
            "握把",
            "cockpit",
            cylinder((0.30, side * 0.265, 1.13), 0.021, 0.125),
            "E04",
            color="black",
            local=(0, side * 0.12, 0),
        )
        part(
            "CK04" + s,
            "刹把夹座",
            "cockpit",
            ring((0.31, side * 0.194, 1.13), 0.023, 0.0125, 0.02),
            "E04",
            color="yellow",
            local=(0, side * 0.05, 0),
        )
        part(
            "CK05" + s,
            "刹把手柄",
            "cockpit",
            path(
                [
                    (0.335, side * 0.19, 1.11),
                    (0.365, side * 0.25, 1.095),
                    (0.35, side * 0.31, 1.095),
                ],
                0.007,
                True,
            ),
            "E04",
            color="yellow",
            local=(0.10, side * 0.05, -0.02),
        )
        bolt(
            "CK06" + s,
            "cockpit",
            (0.335, side * 0.19, 1.13),
            "E04",
            axis=(0, 0, 1),
            local=(0.10, side * 0.05, 0.07),
            label="刹把转轴紧固件",
        )
        bolt(
            "CK07" + s,
            "cockpit",
            (0.315, side * 0.193, 1.15),
            "E04",
            axis=(0, 0, 1),
            local=(0, side * 0.05, 0.07),
            label="刹把夹座紧固件",
        )
        part(
            "CK08" + s,
            "刹车线管",
            "cockpit",
            path(
                [
                    (0.36, side * 0.18, 1.10),
                    (0.49, side * 0.16, 0.96),
                    (0.45, side * 0.10, 0.79),
                ]
                + (
                    [(0.55, side * 0.07, 0.37)]
                    if side < 0
                    else [(0.15, 0.045, 0.45), (-0.12, 0.045, 0.37), (-0.56, 0.06, 0.34)]
                ),
                0.003,
                True,
            ),
            "E04,E08",
            color="black",
            note="走线是外观近似；后刹完整路由受遮挡，需进一步核对。",
        )
    part(
        "CK09", "中央终端壳体", "cockpit", box((0.33, 0, 1.09), (0.12, 0.115, 0.105), 0.022), "E04"
    )
    part(
        "CK10",
        "终端面板",
        "cockpit",
        box((0.28, 0, 1.115), (0.018, 0.084, 0.08), 0.009),
        "E04",
        color="black",
        local=(-0.08, 0, 0.04),
    )
    bolt(
        "CK11",
        "cockpit",
        (0.365, 0, 1.052),
        "E04",
        axis=(0, 0, 1),
        status="inferred_hidden",
        local=(0, 0, 0.13),
        label="候选把立固定螺栓",
    )


def build_saddle():
    axis = Vector((-0.28, 0, 0.96))
    c = Vector((-0.25, 0, 0.62))
    part(
        "SD01",
        "座管",
        "saddle",
        ring(c + axis * 0.11, 0.016, 0.012, 0.31, axis),
        "E03,E09",
        color="black",
        local=(0, 0, 0.06),
    )
    part("SD02", "座管夹环", "saddle", ring(c, 0.026, 0.018, 0.022, axis), "E03", color="black")
    part(
        "SD03",
        "快拆手柄",
        "saddle",
        path([(-0.25, -0.032, 0.62), (-0.21, -0.037, 0.60), (-0.20, -0.034, 0.57)], 0.008, True),
        "E03",
        local=(0, -0.10, 0),
    )
    bolt(
        "SD04",
        "saddle",
        (-0.25, -0.035, 0.62),
        "E03",
        length=0.055,
        local=(0, -0.17, 0),
        label="座管夹横向螺杆",
    )
    washer("SD05", "saddle", (-0.25, 0.034, 0.62), "E03", local=(0, 0.09, 0))
    for side in [-1, 1]:
        part(
            "SD06" + str(side),
            "座弓",
            "saddle",
            path(
                [
                    (-0.43, side * 0.035, 0.91),
                    (-0.38, side * 0.035, 0.875),
                    (-0.25, side * 0.035, 0.89),
                    (-0.19, side * 0.025, 0.925),
                ],
                0.004,
                True,
            ),
            "E09",
            local=(0, side * 0.04, 0.18),
        )
    part(
        "SD07",
        "候选座弓夹块",
        "saddle",
        box((-0.33, 0, 0.88), (0.06, 0.07, 0.018)),
        "E09",
        "inferred_hidden",
        local=(0, 0, 0.12),
    )
    bolt(
        "SD08",
        "saddle",
        (-0.33, 0, 0.874),
        "E09",
        axis=(0, 0, -1),
        status="inferred_hidden",
        local=(0, 0, 0.05),
        label="候选座弓夹紧螺栓",
    )
    # Rounded saddle silhouette; smooth filled proxy, not a traced exact surface.
    verts = []
    faces = []
    n = 48
    for z, scale in [(0.91, 0.88), (0.927, 1), (0.963, 0.94)]:
        for i in range(n):
            t = 2 * PI * i / n
            x = -0.33 + 0.16 * math.cos(t) * scale
            w = 0.09 * (1 - 0.52 * math.cos(t))
            verts.append((x, w * math.sin(t) * scale, z))
    for j in range(2):
        for i in range(n):
            k = (i + 1) % n
            faces.append((j * n + i, j * n + k, (j + 1) * n + k, (j + 1) * n + i))
    faces += [tuple(reversed(range(n))), tuple(range(2 * n, 3 * n))]
    part(
        "SD09",
        "鞍座表面包络",
        "saddle",
        mesh("saddle", verts, faces),
        "E09",
        color="black",
        local=(0, 0, 0.29),
        note="未把未知的填充/包覆层当作识别出的独立制造件。",
    )
    part(
        "SD10",
        "候选鞍座底壳",
        "saddle",
        box((-0.35, 0, 0.916), (0.22, 0.14, 0.012), 0.015),
        "E09",
        "inferred_hidden",
        local=(0, 0, 0.23),
    )


def build_drivetrain():
    cc = Vector((-0.12, 0, 0.335))
    group = "drivetrain"
    part(
        "DT01",
        "候选中轴轴芯",
        group,
        cylinder(cc, 0.008, 0.18),
        "E01",
        "inferred_hidden",
        local=(0, -0.13, 0),
    )
    for side in [-1, 1]:
        s = "L" if side < 0 else "R"
        bearing(
            "DT02" + s,
            group,
            cc + Vector((0, side * 0.027, 0)),
            "E01",
            0.021,
            0.0085,
            local=(0, side * 0.12, 0),
        )
        part(
            "DT03" + s,
            "候选中轴碗",
            group,
            ring(cc + Vector((0, side * 0.041, 0)), 0.026, 0.021, 0.013),
            "E01",
            "inferred_hidden",
            local=(0, side * 0.18, 0),
        )
        end = cc + Vector((side * 0.125, side * 0.094, side * 0.09))
        part(
            "DT04" + s,
            "曲柄",
            group,
            bar(cc + Vector((0, side * 0.094, 0)), end, 0.016),
            "E01",
            color="black",
            local=(0, side * 0.27, 0),
        )
        bolt(
            "DT05" + s,
            group,
            cc + Vector((0, side * 0.111, 0)),
            "E01",
            axis=(0, side, 0),
            local=(0, side * 0.34, 0),
            label="曲柄中心紧固件",
        )
        pedal_c = end + Vector((0, side * 0.055, 0))
        part(
            "DT06" + s,
            "脚踏轴",
            group,
            cylinder(pedal_c, 0.005, 0.10),
            "E11",
            "inferred_hidden",
            local=(0, side * 0.40, 0),
        )
        pts = [
            pedal_c + Vector(v)
            for v in [
                (-0.045, -0.045, 0),
                (0.045, -0.045, 0),
                (0.045, 0.045, 0),
                (-0.045, 0.045, 0),
                (-0.045, -0.045, 0),
            ]
        ]
        cage = merge([path(pts, 0.009), cylinder(pedal_c, 0.011, 0.085)])
        part(
            "DT07" + s, "框式踏板本体", group, cage, "E11", color="black", local=(0, side * 0.47, 0)
        )
        for j in [-1, 1]:
            part(
                f"DT08{s}{j}",
                "踏板反光片",
                group,
                box(pedal_c + Vector((j * 0.047, 0, 0)), (0.006, 0.055, 0.012), 0.001),
                "E11",
                color="yellow",
                local=(j * 0.025, side * 0.47, 0),
            )
            bearing(
                f"DT09{s}{j}",
                group,
                pedal_c + Vector((0, j * 0.026, 0)),
                "E11",
                0.009,
                0.0055,
                local=(0, side * (0.55 + j * 0.045), 0),
            )
    part(
        "DT10",
        "候选前链轮包络",
        group,
        ring((-0.12, -0.065, 0.335), 0.089, 0.018, 0.004),
        "E01",
        "inferred_hidden",
        local=(0, -0.21, 0),
        note="链罩遮挡齿形；不声称识别到实际齿数，未绘制虚构精确齿形。",
    )
    part(
        "DT11",
        "候选后链轮包络",
        group,
        ring((-0.56, -0.065, 0.305), 0.036, 0.018, 0.005),
        "E01",
        "inferred_hidden",
        local=(0, -0.21, 0),
    )
    part(
        "DT12",
        "候选后轮单向机构包络",
        group,
        ring((-0.56, -0.047, 0.305), 0.03, 0.018, 0.018),
        "E01",
        "inferred_hidden",
        local=(0, 0.09, 0),
        note="有无独立飞轮及棘爪数量不可见；不展开假定棘爪。",
    )
    # Closed pitch path built independently from a pair of assumed circles.
    c1 = Vector((-0.56, 0.305))
    c2 = Vector((-0.12, 0.335))
    r1, r2 = 0.036, 0.089
    delta = c2 - c1
    phi = math.atan2(delta.y, delta.x)
    theta = math.acos((r1 - r2) / delta.length)
    top, bottom = phi + theta, phi - theta
    rear = [
        c1 + Vector((math.cos(t), math.sin(t))) * r1
        for t in [top + (2 * PI - 2 * theta) * i / 70 for i in range(71)]
    ]
    front = [
        c2 + Vector((math.cos(t), math.sin(t))) * r2
        for t in [bottom + 2 * theta * i / 140 for i in range(141)]
    ]
    poly = rear + front + [rear[0]]
    lengths = [0.0]
    for a, b in zip(poly, poly[1:]):
        lengths.append(lengths[-1] + (b - a).length)
    count = round(lengths[-1] / 0.0127 / 2) * 2
    points = []
    idx = 0
    for i in range(count):
        distance = lengths[-1] * i / count
        while lengths[idx + 1] < distance:
            idx += 1
        p = poly[idx].lerp(
            poly[idx + 1], (distance - lengths[idx]) / (lengths[idx + 1] - lengths[idx])
        )
        points.append(Vector((p.x, -0.065, p.y)))
    for i, p in enumerate(points):
        q = points[(i + 1) % count]
        for side in [-1, 1]:
            y = side * (0.0046 if i % 2 else 0.0032)
            part(
                f"CH{i:03}P{side}",
                "链节侧板",
                group,
                bar(p + Vector((0, y, 0)), q + Vector((0, y, 0)), 0.0022),
                "E01",
                "repeat_count_prior",
                local=(0, -0.21, 0),
                note="链条局部可见；节数与板形是闭环建模先验，板为圆边杆状近似。",
            )
        part(
            f"CH{i:03}R",
            "链条滚子",
            group,
            ring(p, 0.0036, 0.0018, 0.006),
            "E01",
            "repeat_count_prior",
            local=(0, -0.21, 0),
        )
        part(
            f"CH{i:03}A",
            "链条销轴",
            group,
            cylinder(p, 0.0017, 0.012, vertices=16),
            "E01",
            "repeat_count_prior",
            local=(0, -0.21, 0),
            note="衬套与板的制造形式未知，未额外假设独立衬套。",
        )
    # Visible cover split is not proven: explicit candidate inner/outer separation.
    for side in [-1, 1]:
        y = -0.077 + side * 0.015
        shell = merge(
            [
                box((-0.35, y, 0.36), (0.45, 0.008, 0.11), 0.026),
                cylinder((-0.12, y, 0.335), 0.108, 0.008),
            ]
        )
        part(
            "CG01" + str(side),
            "链罩外侧可见壳" if side < 0 else "候选链罩内侧壳",
            "chainguard",
            shell,
            "E01",
            "observed_feature" if side < 0 else "inferred_hidden",
            local=(0, side * 0.05, 0),
            note="外轮廓重新近似；内外分壳及内部卡扣未知。",
        )
    for i, (x, z) in enumerate([(-0.56, 0.333), (-0.48, 0.403), (-0.17, 0.433)]):
        bolt(
            f"CGS{i + 1}", "chainguard", (x, -0.10, z), "E01", axis=(0, -1, 0), local=(0, -0.12, 0)
        )


def build_accessories():
    for group, x, prefix in [("rear_fender", -0.56, "RF"), ("front_fender", 0.56, "FF")]:
        part(
            prefix + "01",
            "挡泥板主体",
            group,
            annular_arc((x, 0, 0.305), 0.324, 0.065, 0.0, PI * 1.05),
            "E05,E06,E10",
            color="yellow",
        )
        for side in [-1, 1]:
            p = [
                (x, side * 0.067, 0.31),
                (x - 0.22, side * 0.06, 0.51),
                (x - 0.25, side * 0.027, 0.54),
            ]
            part(
                prefix + "02" + str(side),
                "挡泥板支撑",
                group,
                path(p, 0.008),
                "E05,E06",
                color="yellow",
                local=(0, side * 0.07, 0),
            )
            for j, co in enumerate([p[0], p[1]]):
                bolt(
                    prefix + "S" + str(side) + str(j),
                    group,
                    co,
                    "E05,E06",
                    axis=(0, side, 0),
                    local=(0, side * 0.14, 0),
                )
    part(
        "RF03",
        "后反光片",
        "rear_fender",
        box((-0.88, 0, 0.40), (0.014, 0.043, 0.037)),
        "E10",
        color="red",
        local=(-0.08, 0, 0),
    )
    part(
        "AC01",
        "后轮锁外壳包络",
        "accessories",
        annular_arc((-0.56, 0, 0.305), 0.245, 0.085, 0.15 * PI, 0.85 * PI),
        "E10",
        color="yellow",
        note="从跨轮壳体推定轮锁区域；内部锁舌、电机与电路未建模。",
    )
    part(
        "AC02",
        "脚撑安装座",
        "accessories",
        box((-0.51, 0.077, 0.315), (0.08, 0.014, 0.055)),
        "E05",
        color="black",
    )
    part(
        "AC03",
        "侧脚撑",
        "accessories",
        path([(-0.51, 0.085, 0.30), (-0.55, 0.17, 0.16), (-0.62, 0.22, 0.02)], 0.011),
        "E05",
        color="black",
        local=(0, 0.08, -0.05),
    )
    for i, x in enumerate([-0.535, -0.485]):
        bolt(f"AC04{i}", "accessories", (x, 0.09, 0.33), "E05", local=(0, 0.10, 0))
    bolt("AC05", "accessories", (-0.51, 0.1, 0.30), "E05", local=(0, 0.15, 0), label="脚撑转轴")
    # Basket is a welded/molded assembly, not dozens of invented detachable bars.
    basket = []
    for z in [0.77, 0.84, 0.92]:
        basket.append(
            path(
                [
                    (0.51, -0.18, z),
                    (0.80, -0.18, z),
                    (0.84, -0.14, z),
                    (0.84, 0.14, z),
                    (0.80, 0.18, z),
                    (0.51, 0.18, z),
                    (0.51, -0.18, z),
                ],
                0.007,
                True,
            )
        )
    for x in [0.52, 0.60, 0.68, 0.76, 0.83]:
        for side in [-1, 1]:
            basket.append(bar((x, side * 0.18, 0.77), (x, side * 0.18, 0.92), 0.006))
        basket.append(bar((x, -0.18, 0.77), (x, 0.18, 0.77), 0.005))
    for y in [-0.12, -0.06, 0, 0.06, 0.12]:
        basket.append(
            path([(0.51, y, 0.92), (0.51, y, 0.77), (0.84, y, 0.77), (0.84, y, 0.92)], 0.005)
        )
    part("BK01", "整体网格车篮", "basket", merge(basket), "E07", color="yellow")
    part(
        "BK02",
        "车篮后壁壳体",
        "basket",
        box((0.52, 0, 0.85), (0.06, 0.10, 0.15), 0.012),
        "E07",
        color="yellow",
        local=(-0.12, 0, 0),
    )
    part(
        "BK03",
        "车篮前反光片",
        "basket",
        box((0.85, 0, 0.79), (0.012, 0.05, 0.03)),
        "E07",
        color="white",
        local=(0.12, 0, 0),
    )
    part(
        "BK04",
        "车篮安装支架",
        "basket",
        path(
            [(0.41, -0.07, 0.77), (0.65, -0.07, 0.75), (0.65, 0.07, 0.75), (0.41, 0.07, 0.77)],
            0.009,
        ),
        "E07",
        local=(0, 0, -0.08),
    )
    for i, y in enumerate([-0.065, 0.065]):
        bolt(
            f"BK05{i}",
            "basket",
            (0.52, y, 0.80),
            "E07",
            axis=(1, 0, 0),
            local=(-0.19, 0, 0),
            status="inferred_hidden",
        )
    cup = [
        ring((0.42, -0.115, 0.73), 0.039, 0.034, 0.012, (0, 0, 1)),
        ring((0.42, -0.115, 0.85), 0.044, 0.039, 0.013, (0, 0, 1)),
    ]
    for i in range(6):
        t = i * PI / 3
        cup.append(
            bar(
                (0.42 + 0.037 * math.cos(t), -0.115 + 0.037 * math.sin(t), 0.73),
                (0.42 + 0.041 * math.cos(t), -0.115 + 0.041 * math.sin(t), 0.85),
                0.007,
            )
        )
    part("BK06", "开槽杯架", "basket", merge(cup), "E08", local=(0, -0.19, 0))
    bolt(
        "BK07", "basket", (0.41, -0.075, 0.79), "E08", status="inferred_hidden", local=(0, -0.27, 0)
    )


def set_pose(factor, visible_groups=None, detail=False):
    for row in PARTS:
        obj = bpy.data.objects[row["id"]]
        obj.hide_render = visible_groups is not None and row["assembly"] not in visible_groups
        obj.hide_set(obj.hide_render)
        offset = Vector(row["explosion_local_m"])
        if not detail:
            offset += Vector(row["explosion_group_m"])
        obj.location = Vector(row["assembled_origin_m"]) + offset * factor
    bpy.context.view_layer.update()


def camera_fit(direction, pad=1.16):
    scene = bpy.context.scene
    cam = scene.camera
    coords = []
    for row in PARTS:
        obj = bpy.data.objects[row["id"]]
        if not obj.hide_render:
            coords.extend(obj.matrix_world @ Vector(v) for v in obj.bound_box)
    center = sum(coords, Vector()) / len(coords)
    cam.rotation_euler = Vector(direction).to_track_quat("Z", "Y").to_euler()
    inv = cam.rotation_euler.to_matrix().transposed()
    local = [inv @ (v - center) for v in coords]
    lo = Vector(tuple(min(v[i] for v in local) for i in range(3)))
    hi = Vector(tuple(max(v[i] for v in local) for i in range(3)))
    center += cam.rotation_euler.to_matrix() @ ((hi + lo) / 2)
    cam.location = center + Vector(direction).normalized() * 8
    aspect = scene.render.resolution_x / scene.render.resolution_y
    cam.data.ortho_scale = max(hi.x - lo.x, (hi.y - lo.y) * aspect) * pad
    bpy.context.view_layer.update()


def render(name, factor, groups=None, direction=(2.3, -5, 2.3), detail=False, only=None):
    set_pose(factor, groups, detail)
    if only:
        for row in PARTS:
            obj = bpy.data.objects[row["id"]]
            obj.hide_render = row["id"] not in only
            obj.hide_set(obj.hide_render)
    camera_fit(direction)
    scene = bpy.context.scene
    scene.render.filepath = str(OUT / (name + ".png"))
    bpy.ops.render.render(write_still=True)
    anchors = {}
    for group in groups or GROUPS:
        members = [p for p in PARTS if p["assembly"] == group]
        center = sum(
            (
                Vector(p["center_m"])
                + Vector(p["explosion_local_m"]) * factor
                + (Vector() if detail else Vector(p["explosion_group_m"]) * factor)
                for p in members
            ),
            Vector(),
        ) / len(members)
        uv = world_to_camera_view(scene, scene.camera, center)
        anchors[group] = [uv.x, 1 - uv.y]
    (OUT / (name + "-projection.json")).write_text(json.dumps(anchors, indent=2))
    projected = {}
    for row in PARTS:
        if bpy.data.objects[row["id"]].hide_render:
            continue
        center = (
            Vector(row["center_m"])
            + Vector(row["explosion_local_m"]) * factor
            + (Vector() if detail else Vector(row["explosion_group_m"]) * factor)
        )
        uv = world_to_camera_view(scene, scene.camera, center)
        projected[row["id"]] = [uv.x, 1 - uv.y]
    (OUT / (name + "-parts-projection.json")).write_text(json.dumps(projected, indent=2))


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    OUT = args.output.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "assembly.blend").exists():
        raise FileExistsError("Use a new output directory to preserve previous results")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    build_frame()
    build_wheel("FW", 0.56, "E02,E06,E12")
    build_wheel("RW", -0.56, "E05,E10,E12")
    build_fork_and_headset()
    build_cockpit()
    build_saddle()
    build_drivetrain()
    build_accessories()
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.studio_light = "paint.sl"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "BOTH"
    scene.display.shading.curvature_ridge_factor = 1.3
    scene.display.shading.show_object_outline = True
    scene.display.shading.background_type = "WORLD"
    scene.world.color = (0.92, 0.94, 0.96)
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x = 2000
    scene.render.resolution_y = 1400
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    bpy.ops.object.camera_add()
    scene.camera = bpy.context.object
    scene.camera.data.type = "ORTHO"
    # Render assembled and exploded totals, plus dedicated assembly breakdowns.
    render("assembled", 0)
    render("exploded", 1)
    render("front-hub", 1, ["front_wheel"], (1, -4, 1.6), True)
    render(
        "hub-internals",
        1,
        ["front_wheel"],
        (2, -3, 1.5),
        True,
        only=[
            p["id"]
            for p in PARTS
            if p["id"].startswith(("FW03", "FW04", "FW05", "FW06", "FW07", "FW08"))
        ],
    )
    render("drivetrain", 1, ["drivetrain"], (1.8, -5, 2.5), True)
    render("fork-headset", 1, ["fork", "headset"], (1.7, -5, 1.6), True)
    render("cockpit", 1, ["cockpit"], (2, -4, 2.6), True)
    render("saddle", 1, ["saddle"], (2, -5, 2), True)
    # Open the blend with the entire exploded assembly and two inspectable key poses.
    set_pose(0)
    scene.frame_start = 1
    scene.frame_end = 90
    for row in PARTS:
        obj = bpy.data.objects[row["id"]]
        obj.keyframe_insert(data_path="location", frame=1)
        obj.location = (
            Vector(row["assembled_origin_m"])
            + Vector(row["explosion_group_m"])
            + Vector(row["explosion_local_m"])
        )
        obj.keyframe_insert(data_path="location", frame=90)
    scene.frame_set(90)
    camera_fit((2.3, -5, 2.3))
    scene["purpose"] = "Exploratory exploded assembly, not precision CAD or validated physics"
    scene["legend"] = (
        "Blue: inferred hidden components. All dimensions approximate. Repeated counts are priors."
    )
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "assembly.blend"))
    for frame, name in [(1, "assembled"), (90, "exploded")]:
        scene.frame_set(frame)
        bpy.ops.object.select_all(action="DESELECT")
        for row in PARTS:
            bpy.data.objects[row["id"]].select_set(True)
        bpy.ops.export_scene.gltf(
            filepath=str(OUT / (name + ".glb")),
            export_format="GLB",
            use_selection=True,
            export_animations=False,
            export_current_frame=True,
            export_extras=True,
        )
    with (OUT / "parts.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["id", "label_zh", "assembly", "status", "evidence", "note", "geometry_basis"]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(PARTS)
    (OUT / "parts.json").write_text(json.dumps(PARTS, ensure_ascii=False, indent=2) + "\n")
    print(f"EXPLODED_STUDY_DONE parts={len(PARTS)}")


if __name__ == "__main__":
    main()
