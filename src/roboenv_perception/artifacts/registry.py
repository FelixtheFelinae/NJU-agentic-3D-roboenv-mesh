"""Publish content identities; distinguish meshes, points and Gaussian PLY files."""

import hashlib
import json

from ..io import sha256, write_json

EXCLUDE = {"component.json", "state.json"}


def artifact_type(path):
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix in {".mp4", ".webm"}:
        return "video"
    if suffix == ".ply":
        with path.open("rb") as stream:
            header = stream.read(32768).split(b"end_header", 1)[0].decode("ascii", errors="ignore")
        if "f_dc_0" in header and "opacity" in header and "scale_0" in header:
            return "gaussian_scene"
        return (
            "mesh"
            if "element face " in header and "element face 0" not in header
            else "point_cloud"
        )
    if suffix in {".glb", ".obj", ".stl"}:
        return "mesh"
    if suffix in {".usd", ".usda", ".usdc", ".urdf", ".xml"}:
        return "simulation_asset"
    kinds = {
        "blueprint.json": "blueprint",
        "recipe.json": "geometry_recipe",
        "cameras.json": "camera_set",
        "scope_plan.json": "object_scope",
        "labels.json": "semantic_set",
        "instances.json": "mask_set",
        "compiled_assembly.json": "compiled_connection",
    }
    return kinds.get(path.name, "report" if suffix == ".json" else "record")


def tree_index(root):
    root = root.resolve()
    rows = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("组件产物不能包含符号链接")
        if path.is_file() and path.name not in EXCLUDE:
            rows[str(path.relative_to(root))] = sha256(path)
    return rows


def content_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def verify_tree(root, expected):
    if tree_index(root) != expected:
        raise ValueError("依赖产物发生变化，请从新版本重新执行")


def publish(
    root, operation, exports, *, sources, object_id=None, validation="unverified", summary=""
):
    files = tree_index(root)
    artifacts = []
    for ref, digest in files.items():
        path = root / ref
        artifacts.append(
            {
                "id": hashlib.sha256(("output/" + ref).encode()).hexdigest()[:24],
                "ref": ref,
                "sha256": digest,
                "size": path.stat().st_size,
                "type": artifact_type(path),
                "object_id": object_id,
            }
        )
    for role, ref in exports.items():
        path = (root / ref).resolve()
        if not path.is_relative_to(root.resolve()) or not path.exists():
            raise ValueError("组件未生成声明的输出：" + role)
    result = {
        "schema": "envcreator.component/1",
        "operation": operation,
        "exports": exports,
        "object_id": object_id,
        "sources": sources,
        "validation_status": validation,
        "summary": summary,
        "files": files,
        "content_sha256": content_digest(files),
        "artifacts": artifacts,
    }
    write_json(root / "component.json", result)
    return result
