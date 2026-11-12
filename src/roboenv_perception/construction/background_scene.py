"""Attach a fixed-view visual background through a separate OpenUSD interpreter."""

import subprocess
from pathlib import Path

from ..io import read_json, sha256, write_json
from .common import new_run


def attach_background(scene: Path, image: Path, output: Path, usd_python: Path,
                      camera: str, distance: float = 4.0, hide_floor_visual: str | None = None) -> dict:
    sources = {"scene": {"path": str(scene.resolve()), "sha256": sha256(scene)},
               "background": {"path": str(image.resolve()), "sha256": sha256(image)}}
    with new_run(output, "background_display", sources):
        worker = Path(__file__).with_name("background_scene_worker.py")
        with (output / "display.log").open("w") as log:
            command = [str(usd_python), str(worker), "--scene", str(scene.resolve()),
                 "--image", str(image.resolve()), "--output", str(output.resolve()),
                 "--camera", camera, "--distance", str(distance)]
            if hide_floor_visual:
                command.extend(["--hide-floor-visual", hide_floor_visual])
            result = subprocess.run(
                command,
                stdout=log, stderr=subprocess.STDOUT, timeout=120, check=False,
            )
        if result.returncode:
            raise RuntimeError(f"background display worker exited {result.returncode}; see display.log")
        report = read_json(output / "display.json")
        if not report["checks"]["original_prims_unchanged"]:
            raise RuntimeError("background display modified original scene properties")
        write_json(output / "display.json", {**report, "sources": sources})
    return {**report, "scene_usd": str((output / "scene.usda").resolve())}
