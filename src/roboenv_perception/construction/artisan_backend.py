"""Versioned subprocess boundary for Artisan; no model or task orchestration.

This module deliberately uses only the standard library: the standalone worker
imports it inside the separately installed SDK environment as well.
"""

import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import uuid
from pathlib import Path

OPERATIONS = {"build", "compile", "export", "qa", "union"}
FORMATS = ("urdf", "mjcf", "usd")
WORKER = Path(__file__).with_name("artisan_asset_worker.py")
BACKEND = Path(__file__)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_record(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n")


def child_environment():
    env = {k: v for k, v in os.environ.items()
           if not any(word in k.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))}
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def input_hashes(workspace, operation):
    workspace = Path(workspace).resolve()
    paths = [workspace / name for name in ("model.py", "blueprint.json", "recipe.json", "build-spec.json")]
    if operation == "qa":
        paths.append(workspace / "qa_tests.py")
    if operation == "export":
        paths.append(workspace / "compile.json")
    inputs = workspace / "inputs"
    if inputs.is_symlink():
        raise ValueError("Artisan inputs directory must be bundle-local")
    if inputs.is_dir():
        paths.extend(sorted(inputs.rglob("*")))
    result = {}
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(workspace):
            raise ValueError("Artisan input must be a bundle-local regular file")
        if path.is_file():
            result[str(path.relative_to(workspace))] = file_hash(path)
    return result


def make_request(workspace, operation, formats=None):
    if operation not in OPERATIONS:
        raise ValueError("unsupported Artisan operation: " + operation)
    selected = list(FORMATS if formats is None else formats) if operation in {"build", "export"} else []
    if formats is not None and operation not in {"build", "export"}:
        raise ValueError("formats apply only to build/export")
    if operation in {"build", "export"} and (
        not selected or len(set(selected)) != len(selected) or any(f not in FORMATS for f in selected)
    ):
        raise ValueError("select distinct supported export formats")
    inputs = input_hashes(workspace, operation)
    required = [] if operation == "union" else ["model.py"]
    required += ["qa_tests.py"] if operation == "qa" else []
    required += ["compile.json"] if operation == "export" else []
    if any(name not in inputs for name in required):
        raise ValueError("Artisan operation is missing required inputs: " + ", ".join(required))
    return {"schema_version": "envcreator.artisan-request/1", "request_id": uuid.uuid4().hex,
            "operation": operation, "formats": selected, "input_sha256": inputs,
            "worker_sha256": file_hash(WORKER), "backend_sha256": file_hash(BACKEND)}


def verify_inputs(workspace, request):
    if input_hashes(workspace, request["operation"]) != request["input_sha256"]:
        raise ValueError("Artisan inputs changed after request creation")


def artifact_hashes(workspace, paths):
    workspace = Path(workspace).resolve()
    result = {}
    for path in paths:
        path = Path(path)
        if path.is_symlink() or not path.resolve().is_relative_to(workspace) or not path.is_file():
            raise ValueError("Artisan output must be a bundle-local regular file")
        result[str(path.resolve().relative_to(workspace))] = file_hash(path)
    return result


def verify_result(workspace, request, report):
    """Reject stale receipts, contradictory success, and changed/missing exports."""
    receipt = report.get("backend_result", {})
    if (receipt.get("schema_version") != "envcreator.artisan-result/1"
            or any(receipt.get(key) != request[key]
                   for key in ("request_id", "operation", "input_sha256", "worker_sha256", "backend_sha256"))):
        raise ValueError("Artisan result is not bound to this request")
    if receipt.get("physics_status") != "not_run":
        raise ValueError("Artisan compilation/export cannot certify physical function")
    if request["operation"] != "union":
        verify_inputs(workspace, request)
    phases = receipt.get("phases", {})
    if (not isinstance(phases, dict) or set(phases) != {"compile", "export", "qa", "union"}
            or any(value not in {"passed", "failed", "not_run"} for value in phases.values())):
        raise ValueError("invalid Artisan phase results")
    required = {"build": ("compile", "export"), "compile": ("compile",),
                "export": ("export",), "qa": ("qa",), "union": ("union",)}[request["operation"]]
    if report.get("passed") is True and (
        receipt.get("execution_status") != "completed"
        or report.get("error") or report.get("failures")
        or any(phases.get(phase) != "passed" for phase in required)
    ):
        raise ValueError("Artisan success contradicts phase results")
    if not isinstance(receipt.get("artifact_sha256"), dict):
        raise ValueError("missing Artisan artifact manifest")
    for ref, expected in receipt.get("artifact_sha256", {}).items():
        path = Path(workspace) / ref
        if artifact_hashes(workspace, [path]).get(ref) != expected:
            raise ValueError("Artisan output changed: " + ref)
    if report.get("passed") is True and request["operation"] in {"build", "export"}:
        exports = report.get("exports", {})
        for fmt in request["formats"]:
            path = Path(exports.get(fmt, ""))
            if not path.is_absolute():
                path = Path(workspace) / path
            bound = artifact_hashes(workspace, [path])
            if not all(receipt.get("artifact_sha256", {}).get(ref) == digest
                       for ref, digest in bound.items()):
                raise ValueError("Artisan export is not hash-bound: " + fmt)
    return report


def run_artisan(python: Path, workspace: Path, mode="build", timeout=900, *, formats=None):
    """Compatibility facade; explicit compile/export/qa operations share one protocol."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Artisan timeout must be positive and finite")
    workspace = Path(workspace).resolve()
    request = make_request(workspace, mode, formats)
    directory = workspace / ".artisan-requests"
    request_path = directory / (request["request_id"] + ".json")
    result_path = directory / (request["request_id"] + ".result.json")
    write_record(request_path, request)
    log = directory / (request["request_id"] + ".log")
    with log.open("w") as stream:
        process = subprocess.Popen(
            [str(python), str(WORKER), mode, str(workspace), str(request_path)],
            cwd=workspace, env=child_environment(), stdout=stream,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except BaseException:
            # SDK compilers may spawn children. Own and stop the whole process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        finally:
            stream.flush()
            shutil.copyfile(log, workspace / f"{mode}.log")
    if not result_path.is_file():
        raise RuntimeError(f"Artisan {mode} produced no current result: {log}")
    report = verify_result(workspace, request, json.loads(result_path.read_text()))
    if file_hash(WORKER) != request["worker_sha256"] or file_hash(BACKEND) != request["backend_sha256"]:
        raise RuntimeError("Artisan backend changed during execution")
    # A completed QA finding is candidate feedback, not a broken worker.
    if (mode == "qa" and process.returncode in {0, 2} and report.get("failures")
            and report.get("passed") is False and not report.get("error")
            and report["backend_result"]["phases"]["qa"] == "failed"
            and report["backend_result"]["execution_status"] == "completed"):
        return report
    if process.returncode or report.get("passed") is not True:
        raise RuntimeError(f"Artisan {mode} failed: {log}; {json.dumps(report)[:5000]}")
    return report
