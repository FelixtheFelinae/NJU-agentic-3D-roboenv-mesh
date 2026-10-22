"""Explicit, independently restartable construction stages."""

import json
from pathlib import Path

from ..io import read_json
from .contracts import ConstructionConfig, load_construction_config


def add_commands(commands):
    doctor = commands.add_parser(
        "construction-doctor", help="Read-only configuration and dependency checks"
    )
    doctor.set_defaults(construction_action="construction-doctor")
    doctor.add_argument("--config", type=Path, required=True)
    doctor.add_argument("--perception-config", type=Path)
    doctor.add_argument("--media", type=Path)
    doctor.add_argument("--output", type=Path)
    demo = commands.add_parser(
        "demo-construction", help="Synthetic A-C plumbing demo; no model/API training"
    )
    demo.set_defaults(construction_action="demo-construction")
    demo.add_argument("--output", type=Path, required=True)

    for name, source, help_text in [
        ("prepare-objects", "bundle", "Select object evidence from a perception run"),
        ("design-objects", "evidence", "Run intake, blueprint and bounded self-review"),
        ("prepare-background", "bundle", "Preserve RGB and export background supervision masks"),
        ("complete-table", "background", "Fuse a manually annotated planar texture atlas"),
        ("estimate-cameras", "background", "Run installed COLMAP and export masked 3DGS inputs"),
        ("train-background", "cameras", "Run installed CUDA 3DGS with region-weighted supervision"),
    ]:
        parser = commands.add_parser(name, help=help_text)
        parser.set_defaults(construction_action=name)
        parser.add_argument("--" + source, type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--config", type=Path, required=name == "design-objects")
        if name == "prepare-background":
            parser.add_argument("--designs", type=Path)
            parser.add_argument("--exclusions", type=Path)
        if name == "complete-table":
            parser.add_argument("--spec", type=Path, required=True)
        if name == "estimate-cameras":
            parser.add_argument("--completion", type=Path)


def execute(args):
    if args.construction_action == "construction-doctor":
        from ..io import write_json
        from .preflight import check_readiness

        result = check_readiness(args.config, args.perception_config, args.media)
        if args.output:
            if args.output.exists():
                raise FileExistsError(args.output)
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    if args.construction_action == "demo-construction":
        from .demo import run_demo

        print(json.dumps(run_demo(args.output), ensure_ascii=False, indent=2))
        return 0

    from .background import prepare_background
    from .design import design_objects
    from .evidence import prepare_objects
    from .reconstruction import estimate_background_cameras, train_background
    from .table import complete_table

    config = load_construction_config(args.config) if args.config else ConstructionConfig()
    action = args.construction_action
    if action == "prepare-objects":
        prepare_objects(args.bundle, args.output, config.evidence)
    elif action == "design-objects":
        design_objects(args.evidence, args.output, config)
    elif action == "prepare-background":
        prepare_background(
            args.bundle, args.output, config.background, args.designs, args.exclusions
        )
    elif action == "complete-table":
        complete_table(args.background, args.spec, args.output)
    elif action == "estimate-cameras":
        estimate_background_cameras(
            args.background, args.output, config.background, args.completion
        )
    elif action == "train-background":
        train_background(args.cameras, args.output, config.background)
    result = {
        "output": str(args.output.resolve()),
        "stage": action,
        "independent_acceptance": False,
    }
    failed = False
    if action == "design-objects":
        designs = read_json(args.output / "designs.json")
        result["objects"] = designs["objects"]
        failed = not designs["all_designs_ready"]
    print(json.dumps(result, ensure_ascii=False))
    return 2 if failed else 0
