"""EnvCreator entry point: generate environments and reuse ClawSpine capabilities."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .bundle import export_bundle, inspect_bundle


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Keep the existing harness implementation and its entire argument contract.
    if argv and argv[0] == "generate":
        from ..harness.cli import main as generate

        return generate(argv[1:])
    if argv and argv[0] == "background":
        from ..construction.background_cli import main as background

        return background(argv[1:])
    if argv and argv[0] == "dsh-model":
        from .dsh_model import main as check_dsh_model

        return check_dsh_model(argv[1:])
    if argv and argv[0] in {"clawspine", "smoke"}:
        # Give the native launcher direct signal/lifecycle ownership, including
        # Ctrl-C cleanup, instead of leaving an extra Python parent waiting.
        try:
            script = "smoke.mjs" if argv[0] == "smoke" else "clawspine.mjs"
            os.execvpe("node", ["node", str(Path(__file__).with_name(script)), *argv[1:]],
                       {**os.environ, "ENVCREATOR_PYTHON": sys.executable})
        except OSError as exc:
            print(f"EnvCreator: cannot start the ClawSpine adapter: {exc}", file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate", help="Run the existing video/image construction harness")
    commands.add_parser("background", help="Run optional local background outpainting on existing evidence")
    commands.add_parser("dsh-model", help="Check independent local DSH gateway settings without printing secrets")
    commands.add_parser("clawspine", help="Load a bundle using ClawSpine's native G1 entry")
    commands.add_parser("smoke", help="Submit one public goal to an existing native robot session")
    tool = commands.add_parser("tool", help="Build a reference-informed robot tool using EnvCreator geometry")
    tool.add_argument("kind", choices=["electric-screwdriver"])
    tool.add_argument("--output", type=Path, required=True)
    tool.add_argument("--artisan-python", type=Path, required=True)
    tool.add_argument("--blender", type=Path)
    tool.add_argument("--isaac-python", type=Path, help="Run tool feasibility; omitted keeps it unverified")
    tool_test = commands.add_parser("tool-test", help="Collect references and test an existing electric screwdriver")
    tool_test.add_argument("--asset", type=Path, required=True)
    tool_test.add_argument("--output", type=Path, required=True)
    tool_test.add_argument("--isaac-python", type=Path, required=True)
    profile = commands.add_parser("dsh-tool-profile", help="Prepare the EnvCreator native DSH plugin binding")
    profile.add_argument("--asset", type=Path, required=True)
    profile.add_argument("--validation", type=Path)
    profile.add_argument("--output", type=Path, required=True)
    profile.add_argument("--usd-python", type=Path, required=True)
    profile.add_argument("--isaac-python", type=Path)
    profile.add_argument("--artisan-python", type=Path)
    profile.add_argument("--blender", type=Path)
    export = commands.add_parser("handoff", help="Package a delivery and resolved USD dependencies")
    export.add_argument("--delivery", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--usd-python", type=Path, required=True, help="Separate Python with usd-core installed")
    inspect = commands.add_parser("inspect", help="Verify bundle inventory, hashes and provenance")
    inspect.add_argument("--bundle", type=Path, required=True)
    robot_scene = commands.add_parser("robot-scene", help="Derive a robot test scene with an explicit static floor")
    robot_scene.add_argument("--bundle", type=Path, required=True)
    robot_scene.add_argument("--output", type=Path, required=True)
    robot_scene.add_argument("--usd-python", type=Path, required=True)
    robot_scene.add_argument("--floor-z", type=float, required=True)
    robot_scene.add_argument("--floor-size", type=float, nargs=2, default=(6, 6))
    args = parser.parse_args(argv)
    try:
        if args.command == "tool":
            from ..harness.tooling import run_tool_harness

            result = run_tool_harness(args.output, args.artisan_python, args.blender, args.isaac_python)
        elif args.command == "tool-test":
            from ..harness.tooling import test_tool

            result = test_tool(args.asset, args.output, args.isaac_python)
        elif args.command == "dsh-tool-profile":
            from .tool_backend import prepare_profile

            result = prepare_profile(args.output, args.asset, args.validation, python=Path(sys.executable),
                                     usd_python=args.usd_python, isaac_python=args.isaac_python,
                                     artisan_python=args.artisan_python, blender=args.blender)
        elif args.command == "robot-scene":
            from .robot_scene import prepare_robot_scene

            result = prepare_robot_scene(args.bundle, args.output, args.usd_python, args.floor_z, tuple(args.floor_size))
        else:
            result = export_bundle(args.delivery, args.output, args.usd_python) if args.command == "handoff" else inspect_bundle(args.bundle)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"EnvCreator: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "tool-test":
        return 0 if result["status"] == "ideal_tool_verified" else 2
    if args.command == "tool" and args.isaac_python is not None:
        return 0 if result["validation"]["status"] == "ideal_tool_verified" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
