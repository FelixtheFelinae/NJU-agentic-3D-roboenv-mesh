"""Fixed EnvCreator operations behind a native DSH plugin, without a model loop."""

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path

from ..harness.tooling import audit_interaction, collect_tool_reference, run_tool_harness, test_tool
from ..io import read_json, sha256, write_json
from .bundle import export_bundle

OPERATIONS = {"inspect", "research", "review", "validate", "build", "prepare_g1"}


def review_evidence(validation_path: Path, asset: Path) -> dict:
    """Recheck existing evidence; never label evidence reuse as a new simulation."""
    validation = read_json(validation_path)
    report_path = Path(validation["report"])
    report = read_json(report_path)
    expected_sources = {str(asset.resolve()): asset, **{
        str(p.resolve()): p for p in (Path(__file__).parents[1] / "tools").glob("*.py")
        if p.name in {"isaac_screwdriver_worker.py", "power_tool_usd.py", "screwdriving.py", "fastener.py"}
    }}
    checked = {
        "validation_passed": validation.get("status") == "ideal_tool_verified",
        "validation_asset_identity": Path(validation.get("asset", str(asset))).resolve() == asset.resolve(),
        "asset_matches_validation": validation.get("asset_sha256") == sha256(asset),
        "report_matches_validation": validation.get("report_sha256") == sha256(report_path),
        "required_cases_and_checks": audit_interaction(report, asset),
        "source_code_unchanged": all(report.get("source_hashes", {}).get(key) == sha256(path)
                                     for key, path in expected_sources.items()),
        "initial_scene_unchanged": report.get("scene_sha256") == sha256(report_path.with_name("initial_scene.usda")),
    }
    return {"status": "existing_evidence_valid" if all(checked.values()) else "unverified",
            "checks": checked, "validation": str(validation_path), "report": str(report_path),
            "report_sha256": sha256(report_path), "cases": report.get("cases", {}),
            "simulation_executed_this_call": False, "robot_execution": "not_run",
            "scope": report.get("scope")}


def prepare_profile(output: Path, asset: Path, validation: Path | None, *, python: Path,
                    usd_python: Path, isaac_python: Path | None = None,
                    artisan_python: Path | None = None, blender: Path | None = None) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    asset = asset.resolve(strict=True)
    # Resolving a virtualenv executable symlink selects its base interpreter
    # and loses the installed environment. Keep the supplied launch path.
    configuration = {"asset": str(asset), "python": str(python.expanduser().absolute()),
                     "usd_python": str(usd_python.expanduser().absolute()), "output_root": str(output / "calls"),
                     "validation": str(validation.resolve(strict=True)) if validation else None,
                     "isaac_python": str(isaac_python.expanduser().absolute()) if isaac_python else None,
                     "artisan_python": str(artisan_python.expanduser().absolute()) if artisan_python else None,
                     "blender": str(blender.expanduser().absolute()) if blender else None}
    config_path = output / "plugin.config.json"
    write_json(config_path, configuration)
    entry = Path(__file__).with_name("tool_plugin.mjs")
    patch = [{"insert": [{"id": "envcreator-tools", "name": str(entry),
                           "config": {"configFile": str(config_path)}},
                          {"id": "preset-envcreator-asset", "name": "@deepseek-ai/dsh-agent-preset",
                           "config": {"id": "envcreator-asset", "plugins": [
                               {"id": "envcreator-asset-scope", "name": str(entry.with_name("tool_scope.mjs")),
                                "config": {"configFile": str(config_path)}}]}}]}]
    patch_path = output / "envcreator.patch.yml"
    patch_path.write_text("".join("- " + json.dumps(row) + "\n" for row in patch))
    result = {"schema": "envcreator.dsh-tool-profile.v1", "patch": str(patch_path),
              "config": str(config_path), "plugin_entry": str(entry), "plugin_sha256": sha256(entry),
              "backend_sha256": sha256(Path(__file__)),
              "asset_preset_sha256": sha256(entry.with_name("tool_scope.mjs")),
              "asset_sha256": sha256(asset), "native_execution": "not_run", "robot_execution": "not_run",
              "asset_only_agent_preset": "envcreator-asset",
              "use": "native ClawSpine --native-extra-patch; native DSH owns model and tool execution"}
    write_json(output / "profile.json", result)
    return result


def execute_operation(operation: str, config: dict, output: Path) -> dict:
    if operation not in OPERATIONS:
        raise ValueError("unknown EnvCreator operation")
    asset = Path(config["asset"])
    state_path = Path(config["output_root"]) / "state.json"
    state = read_json(state_path) if state_path.exists() else {}
    validation_path = state.get("validation") or config.get("validation")
    if operation == "inspect":
        return {"asset": str(asset), "asset_sha256": sha256(asset),
                "validation": validation_path, "robot_execution": "not_run",
                "scope": "asset development; no robot commands or live-world mutation",
                "can_validate": bool(config.get("isaac_python")),
                "can_build": bool(config.get("artisan_python")),
                "operations": sorted(OPERATIONS)}
    if operation == "research":
        return collect_tool_reference(output / "research")
    if operation == "review":
        if not validation_path:
            raise ValueError("no existing feasibility evidence; run validate first")
        return review_evidence(Path(validation_path), asset)
    if operation == "build":
        if not config.get("artisan_python"):
            raise ValueError("tool build runtime was not configured")
        result = run_tool_harness(output / "build", Path(config["artisan_python"]),
                                  Path(config["blender"]) if config.get("blender") else None,
                                  Path(config["isaac_python"]) if config.get("isaac_python") else None)
        # New candidates are separate artifacts; never replace the bound/live tool.
        return {**result, "bound_asset_replaced": False, "robot_execution": "not_run"}
    if operation == "validate":
        if not config.get("isaac_python"):
            raise ValueError("ideal fixture runtime was not configured")
        result = test_tool(asset, output / "feasibility", Path(config["isaac_python"]))
        if result["status"] == "ideal_tool_verified":
            write_json(state_path, {"validation": str(output / "feasibility/validation.json")})
        return result
    if not validation_path:
        raise ValueError("G1 scene preparation requires existing validated evidence")
    evidence = review_evidence(Path(validation_path), asset)
    if evidence["status"] != "existing_evidence_valid":
        raise ValueError("existing tool evidence changed or did not pass")
    worker = Path(__file__).parents[1] / "tools/prepare_g1_workcell.py"
    workcell = output / "g1-workcell"
    with (output / "prepare-g1.log").open("w") as log:
        subprocess.run([config["usd_python"], str(worker), "--validation", validation_path,
                        "--output", str(workcell)], stdout=log, stderr=subprocess.STDOUT,
                       timeout=120, check=True)
    bundle = export_bundle(workcell / "delivery.json", output / "g1-bundle", Path(config["usd_python"]))
    return {"status": "prepared_not_executed", "bundle": bundle,
            "robot_execution": "not_run", "motor_task_ready": False,
            "scope": "grasp/alignment preparation; native motor/thread runtime still required"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--operation", choices=sorted(OPERATIONS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = read_json(args.config)
    asset = Path(config["asset"])
    before = {"asset": str(asset), "asset_sha256": sha256(asset),
              "operation": args.operation, "robot_execution": "not_run"}
    write_json(args.output / "before.json", before)
    try:
        result = execute_operation(args.operation, config, args.output)
    except Exception:
        write_json(args.output / "failure.json", {"error": traceback.format_exc()})
        raise
    finally:
        write_json(args.output / "after.json", {"asset": str(asset), "asset_sha256": sha256(asset),
                   "bound_asset_unchanged": sha256(asset) == before["asset_sha256"], "robot_execution": "not_run"})
    write_json(args.output / "result.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result.get("status") in {"unverified", "failed"} else 0


if __name__ == "__main__":
    sys.exit(main())
