"""Execute a native tool job using shared component events and immutable results."""

import sys
import time
from pathlib import Path

from ..components.catalog import WORKFLOW_OPERATIONS
from ..components.sources import execution_record
from ..execution.context import emit, execution
from ..io import write_json


def legacy_generate(root, spec):
    cfg = spec["settings"]
    if cfg.get("env_file"):
        from ..secret_env import load_secret_env

        load_secret_env(Path(cfg["env_file"]))
    from ..harness.cli import main as generate

    argv = [
        "--task",
        spec["task"],
        "--scene-id",
        "studio-" + spec["run_id"],
        "--output",
        str(root / "output"),
        "--perception-config",
        cfg["perception_config"],
        "--construction-config",
        cfg["construction_config"],
        "--max-revisions",
        str(cfg["max_revisions"]),
        "--max-wall-seconds",
        str(cfg["max_wall_seconds"]),
        "--background-mode",
        cfg["background_mode"],
    ]
    argv += ["--video" if spec["video"] else "--images", *[row["path"] for row in spec["inputs"]]]
    for key in ("blender", "usd_python", "artisan_python", "isaac_python"):
        if cfg.get(key):
            argv += ["--" + key.replace("_", "-"), cfg[key]]
    if not cfg["physics"]:
        argv.append("--no-physics")
    if not cfg["web_search"]:
        argv.append("--no-web-search")
    return generate(argv)


def main(root):
    spec = execution_record(root / "spec.json")
    with execution(root, spec["run_id"], spec["call_id"]):
        if not spec.get("operation"):
            return legacy_generate(root, spec)
        from ..artifacts.registry import publish
        from ..components.operations import run
        from .config import Settings

        cfg = Settings.model_validate(spec["settings"])
        workflow = spec["operation"] in WORKFLOW_OPERATIONS
        if workflow and spec["operation"] == "workflow_advance":
            anchor = root.parent / spec["workflow_id"]
            original = execution_record(anchor / "spec.json")
            cfg = Settings.model_validate(original["settings"])
        else:
            anchor, original = root, spec
        if cfg.env_file:
            from ..secret_env import load_secret_env

            load_secret_env(cfg.env_file)
        try:
            if workflow:
                from ..harness.workflow import Workflow, atomic_json

                controller = Workflow(anchor / "workflow", cfg, original["task"], original["inputs"],
                                      original["params"])
                summary = controller.run(
                    root / "output", spec["params"] if spec["operation"] == "workflow_advance" else None,
                    actor={k: spec[k] for k in ("origin", "session_id", "call_id", "run_id")},
                )
                summary["workflow_id"] = spec["workflow_id"]
                summary["artifact_base"] = "workflow/"
                atomic_json(root / "output/workflow.json", summary)
                outputs, status = {"workflow": "workflow.json"}, summary["phase"]
            else:
                outputs, status = run(
                    spec["operation"],
                    spec["sources"],
                    root / "output",
                    cfg,
                    spec["task"],
                    inputs=spec["inputs"],
                    params=spec["params"],
                    object_id=spec["object_id"],
                    origin=spec["origin"],
                )
            result = publish(
                root / "output",
                spec["operation"],
                outputs,
                sources=spec["sources"],
                object_id=spec["object_id"],
                validation=status,
            )
            result["lineage"] = {**spec["lineage"], **{key: spec["run_id"] for key in outputs}}
            write_json(root / "output/component.json", result)
            write_json(
                root / "output/state.json",
                {
                    "status": status,
                    "completed_unix": time.time(),
                    "operation": spec["operation"],
                    "physics_status": summary["physics_status"] if workflow else
                    ("not_run" if spec["operation"] != "physics" else status),
                    **({"workflow_id": spec["workflow_id"]} if workflow else {}),
                },
            )
            emit("artifact.published", name=spec["operation"], outputs=outputs)
            return 0
        except Exception as error:
            write_json(
                root / "output/state.json",
                {"status": "failed", "error": f"{type(error).__name__}: {error}"},
            )
            raise


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
