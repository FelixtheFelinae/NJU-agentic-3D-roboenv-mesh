"""Perception and independently callable reconstruction stages for SceneCreator."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from .contracts import Inventory, Labels, PipelineConfig
from .io import load_config, write_json
from .pipeline import run_image
from .validation import validate_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Explicit local credential file")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, media in (("run", "image"), ("run-video", "video")):
        command = commands.add_parser(name, help=f"Process one {media}")
        command.add_argument("--" + media, type=Path, required=True)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--scene-id", default="scene")
        command.add_argument("--task")
    validate = commands.add_parser("validate", help="Verify persisted perception artifacts")
    validate.add_argument("bundle", type=Path)
    schema = commands.add_parser("schemas", help="Export reconstruction schemas")
    schema.add_argument("--output", type=Path, required=True)
    doctor = commands.add_parser("doctor", help="Inspect runtime dependencies")
    doctor.add_argument("--require-cuda", action="store_true")
    from .construction.cli import add_commands

    add_commands(commands)
    args = parser.parse_args(argv)
    try:
        if args.env_file:
            from .secret_env import load_secret_env

            load_secret_env(args.env_file)
        if hasattr(args, "construction_action"):
            from .construction.cli import execute

            return execute(args)
        if args.command in {"run", "run-video"}:
            if args.command == "run":
                root = run_image(
                    args.image, args.output, load_config(args.config), args.scene_id, args.task
                )
            else:
                from .video_pipeline import run_video

                root = run_video(
                    args.video, args.output, load_config(args.config), args.scene_id, args.task
                )
            result = validate_bundle(root)
            print(json.dumps({"bundle": str(root.resolve()), **result}, ensure_ascii=False))
            if args.command == "run" and not result["valid"]:
                return 1
            return 0 if result["valid"] and result.get("checks_status") != "fail" else 2
        if args.command == "validate":
            result = validate_bundle(args.bundle)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["valid"] else 1
        if args.command == "schemas":
            from .construction.assembly_blueprint import AssemblyBlueprint
            from .construction.contracts import Blueprint, ConstructionConfig, TableSpec

            args.output.mkdir(parents=True, exist_ok=False)
            for name, model in (
                ("config", PipelineConfig),
                ("inventory", Inventory),
                ("labels", Labels),
                ("construction", ConstructionConfig),
                ("blueprint", Blueprint),
                ("assembly-blueprint", AssemblyBlueprint),
                ("table", TableSpec),
            ):
                write_json(args.output / f"{name}.schema.json", model.model_json_schema())
            return 0
        if args.require_cuda:
            from .runtime import cuda_info

            print(json.dumps(cuda_info(), indent=2))
            return 0
        modules = {
            name: importlib.util.find_spec(name) is not None
            for name in ("torch", "sam3", "sam2", "transformers")
        }
        cuda = False
        if modules["torch"]:
            import torch

            cuda = torch.cuda.is_available()
        print(json.dumps({"python": sys.version.split()[0], **modules, "cuda": cuda}, indent=2))
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
