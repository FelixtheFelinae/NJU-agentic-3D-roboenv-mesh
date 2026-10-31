"""Prepare an unverified robot test scene without changing the source delivery."""

import json
import subprocess
from pathlib import Path

from .bundle import digest, export_bundle, inspect_bundle


def prepare_robot_scene(bundle: Path, output: Path, usd_python: Path, floor_z: float,
                        floor_size: tuple[float, float] = (6, 6)) -> dict:
    source = inspect_bundle(bundle)
    root = Path(source["bundle"])
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    if output.resolve().is_relative_to(root):
        raise ValueError("derived scene must be outside the original bundle")
    output.mkdir(parents=True)
    authoring = output / "authoring"
    authoring.mkdir()
    scene = authoring / "robot_scene.usda"
    worker = Path(__file__).with_name("usd_worker.py")
    completed = subprocess.run([str(usd_python), str(worker), "--source", source["scene_usd"],
                                "--output", str(scene), "--author-floor", str(floor_z),
                                "--floor-size", *map(str, floor_size)],
                               capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode:
        raise RuntimeError(f"Robot scene preparation failed: {completed.stderr[-4000:]}")
    fixture = json.loads(completed.stdout)
    upstream = json.loads((root / "metadata/delivery.json").read_text())
    delivery = {
        "status": "unverified", "scene_id": source["scene_id"], "scene_usd": str(scene),
        "task": upstream.get("task", ""),
        "upstream": {"bundle": str(root), "manifest_sha256": source["manifest_sha256"],
                     "delivery_sha256": digest(root / "metadata/delivery.json"),
                     "delivery_status": source["delivery_status"]},
        "modifications": [fixture],
        "physics": {"status": "not_run"}, "robot_execution": "not_run",
        "known_limits": [*upstream.get("known_limits", []),
                         "Ground dimensions and contact parameters are declared test assumptions.",
                         "Source geometry is referenced unchanged; robot spawn and reachability remain unverified.",
                         "Adding a fixture does not transfer upstream physics verification to the derived scene."],
    }
    delivery_path = output / "delivery.json"
    delivery_path.write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + "\n")
    result = export_bundle(delivery_path, output / "bundle", usd_python)
    if inspect_bundle(root)["manifest_sha256"] != source["manifest_sha256"]:
        raise ValueError("source bundle changed while preparing the robot scene")
    return result
