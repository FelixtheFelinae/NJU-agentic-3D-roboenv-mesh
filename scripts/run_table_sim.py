"""Launch Isaac Lab separately and require a persisted, passing contact report."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--g1-reference", type=Path)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--python", type=Path, default=Path.home()/".local/share/roboenv/isaac-env/bin/python")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--worker", choices=["table", "assets", "scene-check", "hinge", "robot"], default="table")
    parser.add_argument("--suite", choices=["primary", "followup"])
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; use a fresh run directory")
    if not args.python.is_file():
        parser.error("separate Isaac Sim Python runtime is missing; see docs/ISAAC_TABLE.md")
    worker_name = {"table": "isaac_table_worker.py", "assets": "isaac_asset_worker.py",
                   "scene-check": "isaac_scene_check.py", "hinge": "isaac_hinge_worker.py",
                   "robot": "isaac_robot_worker.py"}[args.worker]
    worker = Path(__file__).resolve().parents[1]/"src/roboenv_perception/construction"/worker_name
    command = [str(args.python), "-u", str(worker), "--input", str(args.input.resolve()), "--output", str(args.output.resolve())]
    if args.suite:
        if args.worker != "hinge":
            parser.error("--suite is only supported for hinge tests")
        command.extend(["--suite", args.suite])
    if args.g1_reference:
        command.extend(["--g1-reference", str(args.g1_reference.resolve())])
    if args.gui:
        command.append("--gui")
    environment = dict(os.environ)
    for name in ["PYTHONPATH", "PYTHONHOME"]:
        environment.pop(name, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    log = args.output.parent/"logs"/(args.output.name+".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as stream:
        process = subprocess.Popen(command, env=environment, stdout=stream, stderr=subprocess.STDOUT)
        started = time.monotonic()
        report_time = None
        shutdown = "normal"
        while True:
            try:
                code = process.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                # Only bound cleanup after the worker has persisted success/failure evidence.
                # The GUI path deliberately remains open for user interaction.
                finished = (args.output/"validation.json").is_file() or (args.output/"failure.json").is_file()
                if finished and not args.gui and report_time is None:
                    report_time = now
                if now-started > args.timeout or (report_time is not None and now-report_time > 15):
                    shutdown = "timeout" if now-started > args.timeout else "terminated_after_report"
                    process.terminate()
                    try:
                        code = process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        code = process.wait(timeout=10)
                    break
    report_path = args.output/"validation.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    # Kit may exit with zero during close() even after a caught worker exception.
    passed = ((code == 0 or shutdown == "terminated_after_report") and report.get("status") == "passed"
              and bool(report.get("checks")) and all(report["checks"].values()))
    receipt = {"worker_exit_code": code, "shutdown": shutdown,
               "contact_validation": "passed" if passed else "failed"}
    if args.output.is_dir():
        (args.output/"launcher.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps({"status": "passed" if passed else "failed", "log": str(log.resolve()),
                      "report": str(report_path.resolve()) if report else None}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
