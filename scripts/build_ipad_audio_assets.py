"""Build the tablet, Sony headphones, and articulated FreeClip case assets."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image


def save_mesh(path, mesh):
    mesh = mesh.copy()
    mesh.remove_unreferenced_vertices()
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path)
    if not mesh.is_watertight or mesh.volume <= 0:
        raise ValueError(f"mesh is not a closed positive-volume solid: {path}")
    return mesh


def tube(points, radius, segments=16):
    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        raise ValueError("tube path needs at least two points")
    rings = []
    for i, point in enumerate(points):
        tangent = points[min(i + 1, len(points) - 1)] - points[max(i - 1, 0)]
        tangent /= np.linalg.norm(tangent)
        reference = np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(tangent, reference))) > 0.95:
            reference = np.array([0.0, 1.0, 0.0])
        normal = np.cross(tangent, reference)
        normal /= np.linalg.norm(normal)
        binormal = np.cross(tangent, normal)
        theta = np.arange(segments) * 2 * np.pi / segments
        rings.append(
            point
            + radius * np.cos(theta)[:, None] * normal
            + radius * np.sin(theta)[:, None] * binormal
        )
    vertices = np.concatenate(rings)
    faces = []
    for ring in range(len(points) - 1):
        for i in range(segments):
            a = ring * segments + i
            b = ring * segments + (i + 1) % segments
            faces.extend([(a, b, b + segments), (a, b + segments, a + segments)])
    start = len(vertices)
    vertices = np.vstack([vertices, points[0], points[-1]])
    for i in range(segments):
        j = (i + 1) % segments
        faces.append((start, j, i))
        base = (len(points) - 1) * segments
        faces.append((start + 1, base + i, base + j))
    return trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=True)


def ellipsoid(center, radii, subdivisions=3):
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=1.0)
    mesh.apply_scale(radii)
    mesh.apply_translation(center)
    return mesh


def box(center, extents):
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    return mesh


def finish_to_floor(mesh):
    mesh = mesh.copy()
    mesh.apply_translation((0, 0, -mesh.bounds[0, 2]))
    return mesh


def tablet_meshes():
    body = box((0, 0, 0.008), (0.300, 0.230, 0.016))
    # The raised folio hinge runs along the near, long edge in the source video.
    ridge = box((0, -0.102, 0.019), (0.290, 0.030, 0.006))
    case = trimesh.boolean.union([body, ridge], engine="manifold")
    return finish_to_floor(case)


def oval_cup(center_x, segments=48):
    profile = [
        (0.000, 0.044, 0.054),
        (0.006, 0.053, 0.064),
        (0.032, 0.053, 0.064),
        (0.043, 0.047, 0.057),
        (0.046, 0.039, 0.049),
    ]
    angles = np.arange(segments) * 2 * np.pi / segments
    rings = [
        np.column_stack((center_x + rx * np.cos(angles), ry * np.sin(angles),
                         np.full(segments, z)))
        for z, rx, ry in profile
    ]
    vertices = np.vstack([*rings, [center_x, 0, 0], [center_x, 0, profile[-1][0]]])
    bottom, top = segments * len(rings), segments * len(rings) + 1
    faces = []
    for ring in range(len(rings) - 1):
        for i in range(segments):
            a = ring * segments + i
            b = ring * segments + (i + 1) % segments
            faces.extend([(a, b, b + segments), (a, b + segments, a + segments)])
    for i in range(segments):
        j = (i + 1) % segments
        faces.extend([(bottom, j, i), (top, (len(rings) - 1) * segments + i,
                       (len(rings) - 1) * segments + j)])
    return trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=True)


def headphone_meshes():
    radius = 0.084
    theta = np.linspace(0, np.pi, 64)
    path = np.c_[radius * np.cos(theta), radius * np.sin(theta),
                 np.full_like(theta, 0.027)]
    headband = tube(path, 0.013)
    left = oval_cup(-radius)
    right = oval_cup(radius)
    mesh = trimesh.boolean.union([headband, left, right], engine="manifold")
    return finish_to_floor(mesh)


def earbud(center, mirror):
    cx, cy, cz = center
    direction = -1 if mirror else 1
    arc = []
    for angle in np.linspace(-1.0, 1.0, 18):
        arc.append(
            [
                cx + direction * 0.006 * np.sin(angle),
                cy + 0.006 * np.cos(angle),
                cz,
            ]
        )
    bridge = tube(arc, 0.0018, segments=8)
    ball = ellipsoid((cx + direction * 0.007, cy + 0.001, cz), (0.008, 0.006, 0.004))
    stem = tube(
        [[cx, cy, cz], [cx, cy, cz - 0.008]], 0.0018, segments=8
    )
    return trimesh.boolean.union([bridge, ball, stem], engine="manifold")


def freeclip_meshes():
    body = box((0, 0, 0.007), (0.060, 0.060, 0.014))
    left = earbud((-0.014, 0.010, 0.010), mirror=False)
    right = earbud((0.014, 0.010, 0.010), mirror=True)
    base = trimesh.boolean.union([body, left, right], engine="manifold")
    base = finish_to_floor(base)
    lid = box((0, 0.030, 0.005), (0.060, 0.060, 0.010))
    return base, lid


def write_rigid(workspace, mesh, blueprint):
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    save_mesh(inputs / "body.obj", mesh)
    (workspace / "build-spec.json").write_text(json.dumps(blueprint, indent=2) + "\n")
    source = '''from sdk import ArticulatedObject, mesh_from_input, TestContext, TestReport


def build_object_model():
    m = ArticulatedObject(name=%(name)r)
    finish = m.material("finish", rgba=%(rgba)r, material_class=%(material_class)r)
    body = m.part("body", est_mass_kg=%(mass)r)
    body.visual(mesh_from_input("body"), material=finish, name="body_surface")
    return m


object_model = build_object_model()


def run_tests() -> TestReport:
    return TestContext(object_model).report()
''' % {
        "name": blueprint["asset_id"],
        "rgba": tuple(blueprint["material"]["rgba"]),
        "material_class": blueprint["material"]["material_class"],
        "mass": blueprint["parts"][0]["est_mass_kg"],
    }
    (workspace / "model.py").write_text(source)


def write_blender_rigid(workspace, blueprint, blender):
    from roboenv_perception.construction.blender_backend import build_blender_meshes, file_hash

    generator = Path(__file__).with_name("blender") / "headphones.py"
    result = build_blender_meshes(generator, workspace / "blender", blender=blender)
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    source = ["from sdk import ArticulatedObject, mesh_from_input, TestContext, TestReport", "",
              "def build_object_model():",
              f"    m = ArticulatedObject(name={blueprint['asset_id']!r})",
              f"    body = m.part('body', est_mass_kg={blueprint['parts'][0]['est_mass_kg']!r})"]
    for mesh in result["meshes"]:
        name = mesh["name"]
        shutil.copyfile(workspace / "blender" / mesh["file"], inputs / mesh["file"])
        source.extend([
            f"    finish = m.material({name!r}, rgba={tuple(mesh['rgba'])!r}, material_class={mesh['material_class']!r})",
            f"    body.visual(mesh_from_input({name!r}), material=finish, name={name!r})",
        ])
    source.extend(["    return m", "", "object_model = build_object_model()", "",
                   "def run_tests() -> TestReport:", "    return TestContext(object_model).report()", ""])
    (workspace / "model.py").write_text("\n".join(source))
    blueprint["mesh_backend"] = "blender"
    blueprint["mesh_backend_result"] = "blender/result.json"
    blueprint["mesh_backend_result_sha256"] = file_hash(workspace / "blender/result.json")
    blueprint["dimensions_m"] = [result["bounds_m"][1][i] - result["bounds_m"][0][i]
                                 for i in range(3)]
    blueprint["assumptions"].append(
        "Blender curved headband, molded shells, hollow earpads and yokes estimated from video; "
        "all visual components belong to one rigid body; not a measured product CAD model.")
    (workspace / "build-spec.json").write_text(json.dumps(blueprint, indent=2) + "\n")


def write_freeclip(workspace, base, lid, blueprint):
    inputs = workspace / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    save_mesh(inputs / "base.obj", base)
    save_mesh(inputs / "lid.obj", lid)
    (workspace / "build-spec.json").write_text(json.dumps(blueprint, indent=2) + "\n")
    joint = blueprint["joint"]
    source = '''from sdk import ArticulatedObject, Box, MotionFeel, MotionLimits, Origin, TestContext, TestReport, mesh_from_input


def build_object_model():
    m = ArticulatedObject(name=%(name)r)
    finish = m.material("finish", rgba=%(rgba)r, material_class=%(material_class)r)
    base = m.part("base", est_mass_kg=%(base_mass)r)
    base.visual(mesh_from_input("base"), material=finish, name="base_surface")
    lid = m.part("lid", est_mass_kg=%(lid_mass)r)
    lid.visual(mesh_from_input("lid"), material=finish, name="lid_surface")
    m.articulation(
        %(joint_name)r,
        "revolute",
        parent="base",
        child="lid",
        origin=Origin(xyz=%(origin)r),
        axis=%(axis)r,
        motion_limits=MotionLimits(lower=%(lower)r, upper=%(upper)r),
        motion_feel=MotionFeel.from_intent(effort="firm", motion="damped", hold="holds_anywhere"),
    )
    return m


object_model = build_object_model()


def run_tests() -> TestReport:
    return TestContext(object_model).report()
''' % {
        "name": blueprint["asset_id"],
        "rgba": tuple(blueprint["material"]["rgba"]),
        "material_class": blueprint["material"]["material_class"],
        "base_mass": blueprint["parts"][0]["est_mass_kg"],
        "lid_mass": blueprint["parts"][1]["est_mass_kg"],
        "joint_name": joint["name"],
        "origin": tuple(joint["origin"]),
        "axis": tuple(joint["axis"]),
        "lower": joint["lower"],
        "upper": joint["upper"],
    }
    (workspace / "model.py").write_text(source)


def build(workspace, artisan_python):
    worker = Path(__file__).resolve().parents[1] / "src/roboenv_perception/construction/artisan_asset_worker.py"
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    with (workspace / "build.log").open("w") as stream:
        result = subprocess.run(
            [str(artisan_python), str(worker), "build", str(workspace.resolve())],
            cwd=workspace,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
    report = json.loads((workspace / "build.json").read_text()) if (workspace / "build.json").exists() else {}
    if result.returncode or not report.get("passed"):
        raise RuntimeError(f"Artisan build failed for {workspace}: {report}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--artisan-python",
        type=Path,
        default=Path("/home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python"),
    )
    parser.add_argument("--tablet-texture", type=Path)
    parser.add_argument("--mesh-backend", choices=["legacy", "blender"], default="legacy",
                        help="Headphone geometry backend; tablet and case keep their existing builders")
    parser.add_argument("--blender", type=Path, help="Blender executable (or use BLENDER_BIN/PATH)")
    parser.add_argument("--scene-layout", type=Path,
                        help="Reuse an existing scene-build-spec.json table and placements")
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)

    table_root = root / "table"
    table_root.mkdir(exist_ok=True)
    image = np.full((1024, 1024, 3), 242, dtype=np.uint8)
    rng = np.random.default_rng(7)
    image = np.clip(image.astype(np.int16) + rng.integers(-3, 4, image.shape), 0, 255).astype(np.uint8)
    Image.fromarray(image).save(table_root / "table.png")

    tablet = {
        "asset_id": "asset-0004",
        "category": "tablet_case",
        "kind": "rigid",
        "parts": [{"name": "body", "est_mass_kg": 0.85}],
        "dimensions_m": [0.300, 0.230, 0.022],
        "appearance_top_z_m": 0.016,
        "scale_source": "estimated",
        "scale_relative_uncertainty": 0.08,
        "material": {"rgba": [0.72, 0.69, 0.60, 1], "material_class": "silicone"},
        "assumptions": [
            "iPad Pro 12.9-inch 2021 confirmed by user; protective case adds approximate 1 cm margin.",
            "Closed folio case modeled as one rigid body; fold-out stand represented by a fixed ridge.",
        ],
        "acceptance": [
            "Metric closed mesh with positive volume and inertia.",
            "Exactly one rigid body and no moving joints.",
            "Actual exported USD settles on the tabletop.",
        ],
    }
    headphones = {
        "asset_id": "asset-0002",
        "category": "headphones",
        "kind": "rigid",
        "parts": [{"name": "body", "est_mass_kg": 0.254}],
        "dimensions_m": [0.276, 0.160, 0.046],
        "scale_source": "estimated",
        "scale_relative_uncertainty": 0.10,
        "material": {"rgba": [0.46, 0.48, 0.47, 1], "material_class": "matte_plastic"},
        "assumptions": [
            "Sony WH-1000XM6 confirmed by user; mass uses the product-family prior.",
            "Headband, cups and yokes are one rigid body as requested.",
        ],
        "acceptance": [
            "Metric closed mesh with positive volume and inertia.",
            "Exactly one rigid body and no moving joints.",
            "Actual exported USD settles on the tabletop.",
        ],
    }
    freeclip = {
        "asset_id": "asset-0005",
        "category": "earbud_case",
        "kind": "articulated",
        "parts": [
            {"name": "base", "est_mass_kg": 0.040},
            {"name": "lid", "est_mass_kg": 0.010},
        ],
        "dimensions_m": [0.060, 0.060, 0.026],
        "joint": {
            "name": "lid_hinge",
            "type": "revolute",
            "axis": [1, 0, 0],
            "origin": [0, -0.030, 0.014],
            "lower": 0.0,
            "upper": 2.2,
            "range_source": "assumed hinge range; case observed open in video",
        },
        "scene_opening_rad": 1.75,
        "scale_source": "estimated",
        "scale_relative_uncertainty": 0.10,
        "material": {"rgba": [0.74, 0.72, 0.67, 1], "material_class": "matte_plastic"},
        "assumptions": [
            "Huawei FreeClip confirmed by user; charging case and earbuds modeled as one assembly.",
            "One revolute hinge is the case lid; earbuds are fixed to the base.",
        ],
        "acceptance": [
            "Exactly two articulated parts and one passive revolute hinge.",
            "Closed pose has no part interpenetration.",
            "Actual exported USD opens from the scene angle without exploding.",
        ],
    }

    tablet_ws = root / "objects/asset-0004"
    write_rigid(tablet_ws, tablet_meshes(), tablet)
    if args.tablet_texture:
        appearance = tablet_ws / "appearance"
        appearance.mkdir(exist_ok=True)
        shutil.copyfile(args.tablet_texture, appearance / "tablet_top.png")
    build(tablet_ws, args.artisan_python)
    headphones_ws = root / "objects/asset-0002"
    if args.mesh_backend == "blender":
        write_blender_rigid(headphones_ws, headphones, args.blender)
    else:
        write_rigid(headphones_ws, headphone_meshes(), headphones)
    (headphones_ws / "appearance").mkdir(exist_ok=True)
    build(headphones_ws, args.artisan_python)
    base, lid = freeclip_meshes()
    freeclip_ws = root / "objects/asset-0005"
    write_freeclip(freeclip_ws, base, lid, freeclip)
    build(freeclip_ws, args.artisan_python)

    scene = {
        "table": {"width_m": 0.80, "depth_m": 0.70, "top_height_m": 0.75, "thickness_m": 0.03},
        "table_texture": str((table_root / "table.png").resolve()),
        "objects": [
            {
                "asset_id": "asset-0004",
                "category": "tablet_case",
                "position": [-0.10, 0.02, 0.752],
                "yaw": -0.12,
                "kind": "rigid",
            },
            {
                "asset_id": "asset-0002",
                "category": "headphones",
                "position": [0.15, 0.18, 0.752],
                "yaw": 0.25,
                "kind": "rigid",
            },
            {
                "asset_id": "asset-0005",
                "category": "earbud_case",
                "position": [0.25, 0.27, 0.752],
                "yaw": -0.30,
                "kind": "articulated",
                "opening_rad": 1.75,
            },
        ],
        "placement_source": "estimated from video views; not a metric pose calibration",
        "scale_source": "estimated from user-confirmed iPad Pro 12.9-inch 2021 and tabletop proportions",
    }
    if args.scene_layout:
        scene = json.loads(args.scene_layout.read_text())
        scene["table_texture"] = str((table_root / "table.png").resolve())
    (root / "scene-build-spec.json").write_text(json.dumps(scene, indent=2) + "\n")
    print(json.dumps({"root": str(root), "objects": [o["asset_id"] for o in scene["objects"]]}))


if __name__ == "__main__":
    main()
