"""Local entry to the same fixed workflow used by the DSH plugin."""

import argparse
import json
import uuid
from pathlib import Path

from ..execution.context import execution
from ..io import read_json, sha256
from ..workbench.config import Settings
from .workflow import Options, Workflow


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scenecreator workflow", description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    start = commands.add_parser("start")
    start.add_argument("--output", type=Path, required=True)
    start.add_argument("--config", type=Path, required=True, help="SceneCreator Studio settings TOML")
    start.add_argument("--task", required=True)
    media = start.add_mutually_exclusive_group(required=True)
    media.add_argument("--video", type=Path)
    media.add_argument("--images", type=Path, nargs="+")
    media.add_argument("--design", type=Path, help="Known-parameter screw_cover or hinged_cover JSON")
    start.add_argument("--background", choices=["off", "masks", "reconstruct"], default="off")
    start.add_argument("--physics", action="store_true")
    advance = commands.add_parser("advance")
    advance.add_argument("--output", type=Path, required=True)
    advance.add_argument("--decision", type=Path, required=True, help="JSON decision with action and current revision")
    args = parser.parse_args(argv)
    root = args.output.resolve()
    try:
        if args.action == "start":
            if not 3 <= len(args.task.strip()) <= 4000:
                raise ValueError("任务描述需要 3 到 4000 字符")
            files = [args.video] if args.video else (args.images or [])
            inputs = [{"name": p.name, "path": str(p.resolve()), "sha256": sha256(p)} for p in files]
            cfg = Settings.load(args.config)
            options = Options(background=args.background, physics=args.physics,
                              declared_design=read_json(args.design) if args.design else None).model_dump(exclude_none=True)
            request = {"task": args.task, "settings": cfg.model_dump(mode="json"), "inputs": inputs,
                       "options": options}
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (root / ".request.json").open("x") as handle:
                json.dump(request, handle, ensure_ascii=False)
            decision = None
        else:
            request = read_json(root / ".request.json")
            cfg = Settings.model_validate(request["settings"])
            decision = read_json(args.decision)
        if cfg.env_file:
            from ..secret_env import load_secret_env

            load_secret_env(cfg.env_file)
        call = uuid.uuid4().hex[:12]
        output = root / "calls" / call
        output.mkdir(parents=True, mode=0o700)
        with execution(output, call, call):
            result = Workflow(root / "workflow", cfg, request["task"], request["inputs"], request["options"]).run(
                output, decision, actor={"origin": "cli", "call_id": call},
            )
        print(json.dumps({**result, "snapshot": str(output / "workflow.json")}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"SceneCreator: {exc}\n")
