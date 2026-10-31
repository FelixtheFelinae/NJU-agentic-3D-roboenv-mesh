"""Attach a fixed-view visual background through a separate OpenUSD interpreter."""

import os
import subprocess
import time
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


def validate_laptop_display(scene: Path, output: Path, isaac_python: Path,
                            camera: str = "/World/Overview", size: tuple[int, int] = (768, 576),
                            timeout_seconds: int = 240) -> dict:
    """Verify the existing laptop fixture; reuse Harness process cleanup after saved reports."""
    from ..harness.physics import _stop_process_group

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    worker = Path(__file__).with_name("background_render_worker.py")
    expected = sha256(scene)
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(OMP_NUM_THREADS="4", MKL_NUM_THREADS="4")
    command = [str(isaac_python), "-u", str(worker), "--scene", str(scene.resolve()),
               "--output", str(output.resolve()), "--camera", camera,
               "--width", str(size[0]), "--height", str(size[1])]
    shutdown = "normal"
    with output.with_suffix(".log").open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env,
                                   start_new_session=True)
        deadline = time.monotonic() + timeout_seconds
        reported = None
        try:
            while process.poll() is None:
                now = time.monotonic()
                if now >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
                if (output / "validation.json").is_file() or (output / "failure.json").is_file():
                    if reported is None:
                        reported = now
                    elif now - reported > 3:
                        _stop_process_group(process)
                        shutdown = "terminated_after_report"
                        break
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            _stop_process_group(process)
    write_json(output / "launcher.json", {"worker_exit_code": process.returncode, "shutdown": shutdown})
    if not (output / "validation.json").is_file():
        raise RuntimeError(f"Isaac background check failed; see {output.with_suffix('.log')}")
    report = read_json(output / "validation.json")
    if sha256(scene) != expected:
        raise RuntimeError("background scene layer changed during validation")
    return report
