"""Portable scene handoff. This module deliberately needs only the standard library."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

SCHEMA = "envcreator.scene-bundle.v1"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _member(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("bundle paths must be relative POSIX paths")
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError("bundle path escapes its root")
    target = root
    for part in name.parts:
        target /= part
        if target.is_symlink():
            raise ValueError(f"symlink in bundle: {relative}")
    if not target.is_file() or not target.resolve().is_relative_to(root):
        raise ValueError(f"missing bundle file: {relative}")
    return target


def inspect_bundle(bundle: Path) -> dict:
    """Verify bytes and provenance; never promote a delivery's verification status."""
    if bundle.is_symlink():
        raise ValueError("bundle root cannot be a symlink")
    root = bundle.resolve(strict=True)
    manifest_path = _member(root, "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported scene bundle schema")
    files = manifest.get("files_sha256")
    if not isinstance(files, dict) or not files or "manifest.json" in files:
        raise ValueError("bundle requires an explicit file inventory")
    for relative, expected in files.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("invalid SHA-256 in bundle")
        if digest(_member(root, relative)) != expected:
            raise ValueError(f"bundle hash mismatch: {relative}")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in bundle")
        if not path.is_dir():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files) | {"manifest.json"}:
        raise ValueError("bundle contains unlisted or missing files")
    scene = manifest.get("scene", {})
    if not isinstance(scene, dict):
        raise ValueError("missing scene description")
    path = scene.get("path", "")
    if not isinstance(path, str) or path not in files or not path.startswith("scene/") or Path(path).suffix not in {".usd", ".usda", ".usdc"}:
        raise ValueError("scene must be a listed USD inside scene/")
    if scene.get("meters_per_unit") != 1 or scene.get("up_axis") != "Z":
        raise ValueError("ClawSpine handoff requires metres and Z-up; no implicit rescaling")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}", str(scene.get("id", ""))):
        raise ValueError("invalid scene ID")
    delivery_path = "metadata/delivery.json"
    if delivery_path not in files:
        raise ValueError("missing original delivery")
    delivery = json.loads(_member(root, delivery_path).read_text())
    if not isinstance(delivery, dict):
        raise ValueError("invalid original delivery")
    status = delivery.get("status")
    if status not in {"verified", "unverified"} or manifest.get("delivery_status") != status:
        raise ValueError("delivery status differs from source evidence")
    if delivery.get("scene_id") != scene["id"]:
        raise ValueError("scene ID differs from delivery")
    if manifest.get("source_delivery_sha256") != files[delivery_path]:
        raise ValueError("delivery provenance hash mismatch")
    return {
        "schema": SCHEMA,
        "bundle": str(root),
        "manifest_sha256": digest(manifest_path),
        "scene_usd": str(root / path),
        "scene_id": scene["id"],
        "delivery_status": status,
        "file_count": len(files),
        "usd_check": manifest.get("usd_check"),
        "robot_execution": "not_run",
    }


def export_bundle(delivery_path: Path, output: Path, usd_python: Path) -> dict:
    """Resolve USD dependencies in a separate worker, then publish a checked bundle."""
    delivery_path = delivery_path.resolve(strict=True)
    source_bytes = delivery_path.read_bytes()
    delivery = json.loads(source_bytes)
    if not isinstance(delivery, dict) or delivery.get("status") not in {"verified", "unverified"}:
        raise ValueError("unsupported delivery status")
    value = delivery.get("scene_usd")
    if not isinstance(value, str) or not value:
        raise ValueError("delivery has no scene_usd; complete scene generation first")
    scene = Path(value)
    if not scene.is_absolute():
        scene = delivery_path.parent / scene
    scene = scene.resolve(strict=True)
    if scene.suffix.lower() not in {".usd", ".usda", ".usdc"} or not scene.is_file():
        raise ValueError("delivery scene must be a local USD file")
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if output.resolve().is_relative_to(scene.parent):
        raise ValueError("bundle output must be outside the source scene directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".envcreator-", dir=output.parent))
    try:
        worker = Path(__file__).with_name("usd_worker.py")
        completed = subprocess.run(
            [str(usd_python), str(worker), "--source", str(scene), "--output", str(staging / "scene")],
            text=True, capture_output=True, timeout=600, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"USD handoff failed: {completed.stderr[-4000:]}")
        check = json.loads(completed.stdout)
        (staging / "metadata").mkdir()
        (staging / "metadata/delivery.json").write_bytes(source_bytes)
        manifest = {
            "schema": SCHEMA,
            "scene": {"path": "scene/" + check["root_layer"], "id": delivery.get("scene_id"),
                      "meters_per_unit": check["meters_per_unit"], "up_axis": check["up_axis"]},
            "delivery_status": delivery["status"],
            "source_delivery_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "usd_check": check,
            "files_sha256": {p.relative_to(staging).as_posix(): digest(p)
                             for p in sorted(staging.rglob("*")) if p.is_file()},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        inspect_bundle(staging)
        if output.exists():
            raise FileExistsError(output)
        os.rename(staging, output)
        return inspect_bundle(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
