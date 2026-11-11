"""Resolve registered run outputs, including bounded read-only legacy adapters."""

import json
from functools import lru_cache
from pathlib import Path

from ..artifacts.registry import tree_index
from ..io import sha256
from ..workbench.store import read_json


def execution_record(path):
    """Private execution records are not subject to the browser JSON preview limit."""
    if not path.is_file():
        return {}
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("执行记录过大，请检查依赖快照")
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError("执行记录格式无效")
    return result


def dependency_snapshot(bindings):
    """Flatten the DAG once: shared ancestors retain one exact file snapshot."""
    found = {}

    def visit(binding):
        key = str((binding["root"], binding["ref"]))
        plain = {k: v for k, v in binding.items() if k != "dependencies"}
        if key in found:
            if found[key]["files"] != plain["files"]:
                raise ValueError("同一依赖引用出现不同哈希")
            return
        if len(found) >= 256:
            raise ValueError("依赖链过长")
        found[key] = plain
        for child in binding.get("dependencies", {}).values():
            visit(child)

    for binding in bindings.values():
        visit(binding)
    return {str(i): row for i, row in enumerate(found.values())}


def output_root(run):
    root = Path(run["root"]).resolve()
    return root / "output" if (root / "output").is_dir() else root


def legacy_exports(run, object_id=None):
    base = output_root(run)
    state = read_json(base / "state.json", {}) or {}
    stages = state.get("stages", {})
    found = {}
    entries = {
        "perception": "scene.json",
        "evidence": "objects.json",
        "scope": "scope_plan.json",
        "research": "research.json",
        "assets": "assets.json",
        "scene": "scene_result.json",
        "physics": "physics.json",
        "background": "background.json",
        "cameras": "cameras.json",
        "gaussian": "background_model.json",
        "task_review": "task_scope.json",
        "delivery": "delivery.json",
    }
    for role, filename in entries.items():
        name = "task_scope" if role == "task_review" else role
        candidates = [
            base / row["path"]
            for key, row in reversed(list(stages.items()))
            if (key == name or key.startswith(name + "-repair"))
            and row.get("status") == "completed"
        ]
        candidates += [
            base / name,
            base / "background" / ("prepared" if role == "background" else name),
        ]
        if role == "gaussian":
            candidates += [base / "gaussians", base / "adaptive", base / "smoke-reset"]
        if role == "cameras":
            candidates += [
                base / "cameras_subset",
                base / "cameras-6fps-56",
                base / "cameras_plane_v2",
            ]
        candidates += [base]
        for path in candidates:
            if (path / filename).is_file():
                found[role] = path
                break
    designs = base / "design"
    blueprint_paths = [
        p for p in designs.glob("objects/*/blueprint.json") if object_id in {None, p.parent.name}
    ]
    if len(blueprint_paths) == 1:
        found["blueprint"] = blueprint_paths[0]
    inventory = read_json(found.get("assets", base / "assets") / "assets.json", {}) or {}
    rows = [
        r
        for r in inventory.get("objects", [])
        if object_id in {None, r.get("asset_id"), r.get("object_id")}
    ]
    if len(rows) == 1:
        row = rows[0]
        if row.get("recipe_ref"):
            found["recipe"] = Path(row["recipe_ref"])
        if row.get("model_ref"):
            found["built"] = Path(row["model_ref"]).parent
    # Independent geometry experiments can be viewed/reused without fabricating a Harness history.
    if not found.get("blueprint") and (base / "blueprint.json").is_file():
        found["blueprint"] = base / "blueprint.json"
    if not found.get("recipe") and (base / "recipe.json").is_file():
        found["recipe"] = base / "recipe.json"
    if (base / "candidate.json").is_file():
        found.setdefault("built", base)
    return {
        key: path.resolve()
        for key, path in found.items()
        if path.resolve().is_relative_to(Path(run["root"]).resolve()) and path.exists()
    }


@lru_cache(maxsize=128)
def checkpoint_digest(path, size, modified_ns):
    return sha256(Path(path))


def exports(run, object_id=None):
    base = output_root(run)
    result = execution_record(base / "component.json")
    if result:
        found = {}
        for key, ref in result.get("exports", {}).items():
            path = (base / ref).resolve()
            if not path.is_relative_to(base.resolve()) or not path.exists():
                raise ValueError("组件输出引用已损坏")
            found[key] = path
    else:
        found = legacy_exports(run, object_id)
    # Only complete, hash-bound checkpoints are resumable, including interrupted jobs.
    gaussian = found.get("gaussian", base / "gaussian")
    if gaussian.is_dir():
        checkpoints = list((gaussian / "model/checkpoints").glob("iteration_*/checkpoint.json"))
        checkpoints.sort(key=lambda p: int(p.parent.name.split("_")[-1]), reverse=True)
        for checkpoint in checkpoints:
            receipt = read_json(checkpoint, {})
            state = checkpoint.parent / "training_state.pt"
            if state.is_file() and checkpoint_digest(
                str(state), state.stat().st_size, state.stat().st_mtime_ns
            ) == receipt.get("sha256"):
                found["checkpoint"] = state
                break
    return found


def bind_source(run, role, object_id=None):
    path = exports(run, object_id).get(role)
    if path is None:
        raise ValueError(f"此版本没有 {role} 输出，请先执行对应步骤")
    root = Path(run["root"]).resolve()
    spec = execution_record(root / "spec.json")
    return {
        "run_id": run["id"],
        "role": role,
        "ref": str(path.relative_to(root)),
        "root": str(root),
        "files": tree_index(path) if path.is_dir() else {".": sha256(path)},
        "dependencies": dependency_snapshot(spec.get("sources", {})),
    }


def source_path(binding, _seen=None):
    _seen = set() if _seen is None else _seen
    key = (binding["root"], binding["ref"])
    if key in _seen:
        return (Path(binding["root"]) / binding["ref"]).resolve()
    if len(_seen) >= 256:
        raise ValueError("依赖链过长")
    _seen.add(key)
    root = Path(binding["root"]).resolve()
    path = (root / binding["ref"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("依赖引用越界")
    files = tree_index(path) if path.is_dir() else {".": sha256(path)}
    if files != binding["files"]:
        raise ValueError("依赖产物发生变化，请从新版本重新执行")
    for dependency in binding.get("dependencies", {}).values():
        source_path(dependency, _seen)
    return path
