import argparse
import importlib.util
import json
import sys
from pathlib import Path

from .contracts import EvaluationManifest, Inventory, Labels, PipelineConfig, TruthDocument
from .demo import create_fixture
from .io import load_config, write_json
from .pipeline import run_image
from .validation import validate_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description="CUDA-first VLM + SAM 3 perception and evaluation")
    parser.add_argument(
        "--env-file", type=Path, help="Explicit local API secret file; no shell evaluation"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Process one image using your configured backends")
    run.add_argument("--image", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--scene-id", default="scene")
    run.add_argument("--task", default=None)
    video = commands.add_parser(
        "run-video", help="Offline video inference on the Linux CUDA server"
    )
    video.add_argument("--video", type=Path, required=True)
    video.add_argument("--config", type=Path, required=True)
    video.add_argument("--output", type=Path, required=True)
    video.add_argument("--scene-id", default="scene")
    video.add_argument("--task", default=None)
    video_demo = commands.add_parser(
        "demo-video", help="Explicit synthetic video fixture; not deployment validation"
    )
    video_demo.add_argument("--output", type=Path, required=True)
    freeze = commands.add_parser("freeze", help="Freeze dataset files, labels and thresholds")
    freeze.add_argument("--manifest", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser(
        "prepare-truth",
        help="Copy only source images into an empty independent annotation template",
    )
    prepare.add_argument("--bundle", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--sample-id", default="scene-001")
    evaluation = commands.add_parser(
        "evaluate", help="Evaluate frozen independent annotations (CUDA by default)"
    )
    evaluation.add_argument("--manifest", type=Path, required=True)
    evaluation.add_argument("--lock", type=Path, required=True)
    evaluation.add_argument("--predictions", type=Path)
    evaluation.add_argument("--output", type=Path, required=True)
    evaluation.add_argument(
        "--fixture-oracle",
        action="store_true",
        help="CPU oracle restricted to synthetic datasets; never formal acceptance",
    )
    demo = commands.add_parser("demo", help="Explicit synthetic replay; no model/API/GPU required")
    demo.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser(
        "validate", help="Validate persisted bundle hashes, geometry and routing"
    )
    validate.add_argument("bundle", type=Path)
    schema = commands.add_parser(
        "schemas", help="Export versioned configuration and VLM JSON schemas"
    )
    schema.add_argument("--output", type=Path, required=True)
    doctor = commands.add_parser("doctor", help="Read-only check of remote runtime dependencies")
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
        if args.command == "run":
            root = run_image(
                args.image, args.output, load_config(args.config), args.scene_id, args.task
            )
            result = validate_bundle(root)
            print(json.dumps({"bundle": str(root.resolve()), **result}, ensure_ascii=False))
            if not result["valid"]:
                return 1
            if result["checks_status"] == "fail":
                return 2
        elif args.command == "run-video":
            from .video_pipeline import run_video

            root = run_video(
                args.video, args.output, load_config(args.config), args.scene_id, args.task
            )
            result = validate_bundle(root)
            print(json.dumps({"bundle": str(root.resolve()), **result}, ensure_ascii=False))
            return 0 if result["valid"] and result["checks_status"] != "fail" else 2
        elif args.command == "demo-video":
            from .evaluation import evaluate, freeze_dataset
            from .video_demo import create_video_fixture
            from .video_pipeline import run_video

            args.output.mkdir(parents=True, exist_ok=False)
            source, config, manifest = create_video_fixture(args.output / "fixture")
            root = run_video(source, args.output / "run", load_config(config), "synthetic-video")
            prediction_map = args.output / "predictions.json"
            write_json(prediction_map, {"clip": str(root.resolve())})
            lock = freeze_dataset(manifest, args.output / "dataset.lock.json")
            result = evaluate(
                manifest, lock, args.output / "evaluation", prediction_map, fixture=True
            )
            print(
                json.dumps(
                    {
                        "bundle": str(root.resolve()),
                        **validate_bundle(root),
                        "model_inference": "NOT RUN",
                        "metrics": result["metrics"],
                        "acceptance_passed": result["acceptance_passed"],
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "freeze":
            from .evaluation import freeze_dataset

            print(freeze_dataset(args.manifest, args.output))
        elif args.command == "prepare-truth":
            from .evaluation import prepare_truth

            print(prepare_truth(args.bundle, args.output, args.sample_id))
        elif args.command == "evaluate":
            from .evaluation import evaluate

            result = evaluate(
                args.manifest, args.lock, args.output, args.predictions, args.fixture_oracle
            )
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in [
                            "dataset_id",
                            "metrics",
                            "metric_gates_passed",
                            "eligible_for_acceptance",
                            "acceptance_passed",
                        ]
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if result["metric_gates_passed"] else 2
        elif args.command == "demo":
            args.output.mkdir(parents=True, exist_ok=False)
            image, config = create_fixture(args.output / "fixture")
            root = run_image(image, args.output / "run", load_config(config), "synthetic-demo")
            print(
                json.dumps(
                    {
                        "bundle": str(root.resolve()),
                        "model_inference": "NOT RUN",
                        **validate_bundle(root),
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "validate":
            result = validate_bundle(args.bundle)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["valid"] else 1
        elif args.command == "schemas":
            from .construction.contracts import Blueprint, ConstructionConfig, TableSpec
            from .construction.simulation import TableSimulationSpec

            args.output.mkdir(parents=True, exist_ok=False)
            for name, model in [
                ("config", PipelineConfig),
                ("inventory", Inventory),
                ("labels", Labels),
                ("evaluation", EvaluationManifest),
                ("truth", TruthDocument),
                ("construction", ConstructionConfig),
                ("blueprint", Blueprint),
                ("table", TableSpec),
                ("table-simulation", TableSimulationSpec),
            ]:
                write_json(args.output / f"{name}.schema.json", model.model_json_schema())
        elif args.command == "doctor":
            if args.require_cuda:
                from .runtime import cuda_info

                print(json.dumps(cuda_info(), indent=2))
                return 0
            torch_installed = importlib.util.find_spec("torch") is not None
            cuda = False
            if torch_installed:
                import torch

                cuda = torch.cuda.is_available()
            print(
                json.dumps(
                    {
                        "python": sys.version.split()[0],
                        "torch": torch_installed,
                        "sam3": importlib.util.find_spec("sam3") is not None,
                        "sam2": importlib.util.find_spec("sam2") is not None,
                        "transformers": importlib.util.find_spec("transformers") is not None,
                        "cuda": cuda,
                        "note": "No weights downloaded; no API requests or model inference executed.",
                    },
                    indent=2,
                )
            )
        return 0
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
