"""Run local background completion on existing evidence, without rebuilding task assets."""

import argparse
import json
import subprocess
from pathlib import Path

from ..io import sha256, write_json
from .background_generation import (
    complete_prepared_background,
    generate_background,
    load_background_generation_config,
)
from .background_scene import attach_background
from .common import new_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prepared-background", type=Path)
    source.add_argument("--image", type=Path)
    parser.add_argument("--observed-mask", type=Path, help="Binary 255=observed background, 0=generate")
    parser.add_argument("--frame-id", help="Frame in an existing prepared background; default=most observed pixels")
    parser.add_argument("--scene", type=Path)
    parser.add_argument("--camera", help="Existing perspective camera, e.g. /World/Overview")
    parser.add_argument("--usd-python", type=Path)
    parser.add_argument("--display-distance", type=float, default=4.0)
    parser.add_argument("--hide-floor-visual", help="Existing floor prim; hide appearance while preserving collision")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.image) != bool(args.observed_mask):
        parser.error("--image and --observed-mask must be supplied together")
    if args.frame_id and not args.prepared_background:
        parser.error("--frame-id requires --prepared-background")
    if any((args.scene, args.camera, args.usd_python)) and not all((args.scene, args.camera, args.usd_python)):
        parser.error("display requires --scene, --camera and --usd-python together")
    if args.hide_floor_visual and not args.scene:
        parser.error("--hide-floor-visual requires --scene")
    try:
        config = load_background_generation_config(args.config)
        with new_run(args.output, "background_completion", {"config_sha256": sha256(args.config)}):
            if args.prepared_background:
                report = complete_prepared_background(args.prepared_background, args.output / "generation",
                                                       config, args.frame_id)
            else:
                report = generate_background(args.image, args.observed_mask, args.output / "generation", config)
            if args.scene:
                report["display"] = attach_background(args.scene, args.output / "generation/background.png",
                                                       args.output / "display", args.usd_python, args.camera,
                                                       args.display_distance, args.hide_floor_visual)
            write_json(args.output / "result.json", report)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(2, f"EnvCreator background: {error}\n")
    print(json.dumps({"status": report["status"], "result": str((args.output / "result.json").resolve()),
                      "image": str((args.output / "generation/background.png").resolve()),
                      "scene": report.get("display", {}).get("scene_usd")}, ensure_ascii=False, indent=2))
    return 0
