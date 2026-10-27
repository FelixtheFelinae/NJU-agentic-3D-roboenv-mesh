"""Reload and inspect actual exploded-study deliverables; optionally render motion."""

import argparse
import hashlib
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--animation", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    root = args.model.resolve()
    rows = json.loads((root / "parts.json").read_text())
    observations = json.loads((root.parent / "observations.json").read_text())
    evidence_ids = {r["id"] for r in observations["observations"]}
    bpy.ops.wm.open_mainfile(filepath=str(root / "assembly.blend"))
    checks = {}
    checks["unique_part_ids"] = len({r["id"] for r in rows}) == len(rows)
    checks["mesh_ids_equal_bom"] = {o.name for o in bpy.data.objects if o.type == "MESH"} == {
        r["id"] for r in rows
    }
    checks["evidence_references_resolve"] = all(set(r["evidence"]) <= evidence_ids for r in rows)
    checks["source_images_unchanged"] = all(
        hashlib.sha256((root.parent / r["source_image"]).read_bytes()).hexdigest() == r["sha256"]
        for r in observations["observations"]
    )
    nonmanifold = []
    invalid_coordinates = []
    for row in rows:
        obj = bpy.data.objects[row["id"]]
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        if any(not edge.is_manifold for edge in bm.edges):
            nonmanifold.append(row["id"])
        if any(not math.isfinite(c) for v in bm.verts for c in v.co):
            invalid_coordinates.append(row["id"])
        bm.free()
    checks["all_mesh_surfaces_closed"] = not nonmanifold
    checks["finite_coordinates"] = not invalid_coordinates
    pose_errors = {}
    for frame, factor in [(1, 0), (90, 1), (1, 0)]:
        bpy.context.scene.frame_set(frame)
        err = max(
            (
                bpy.data.objects[r["id"]].location
                - Vector(r["assembled_origin_m"])
                - (Vector(r["explosion_group_m"]) + Vector(r["explosion_local_m"])) * factor
            ).length
            for r in rows
        )
        pose_errors[f"frame_{frame}"] = err
    checks["explosion_and_return_match_bom"] = max(pose_errors.values()) < 1e-6
    export_errors = {}
    for name, factor in [("assembled", 0), ("exploded", 1)]:
        data = (root / f"{name}.glb").read_bytes()
        length = struct.unpack_from("<I", data, 12)[0]
        gltf = json.loads(data[20 : 20 + length])
        nodes = {n["name"]: n for n in gltf["nodes"] if "mesh" in n}
        checks[name + "_glb_ids_and_metadata"] = set(nodes) == {r["id"] for r in rows} and all(
            nodes[r["id"]]["extras"]["status"] == r["status"] for r in rows
        )
        errors = []
        for row in rows:
            # Export uses glTF Y-up; the Blender assembly is Z-up.
            t = nodes[row["id"]].get("translation", [0, 0, 0])
            actual = Vector((t[0], -t[2], t[1]))
            expected = (
                Vector(row["assembled_origin_m"])
                + (Vector(row["explosion_group_m"]) + Vector(row["explosion_local_m"])) * factor
            )
            errors.append((actual - expected).length)
        export_errors[name] = max(errors)
        checks[name + "_glb_transforms"] = max(errors) < 1e-6
    report = {
        "checks": checks,
        "all_passed": all(checks.values()),
        "part_count": len(rows),
        "counts_by_status": dict(Counter(r["status"] for r in rows)),
        "nonmanifold_meshes": nonmanifold,
        "pose_max_error_m": pose_errors,
        "export_max_error_m": export_errors,
        "scope": "File/mesh/metadata/pose checks only; not real-world completeness, dimensional accuracy, interference, assembly feasibility or dynamics validation.",
    }
    (root / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    if not report["all_passed"]:
        raise RuntimeError("Validation failed; inspect validation.json")
    if args.animation:
        frames = root / "animation-frames"
        frames.mkdir(exist_ok=True)
        scene = bpy.context.scene
        scene.render.resolution_x = 1280
        scene.render.resolution_y = 896
        for i in range(45):
            scene.frame_set(round(1 + 89 * i / 44))
            scene.render.filepath = str(frames / f"{i:04}.png")
            bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
