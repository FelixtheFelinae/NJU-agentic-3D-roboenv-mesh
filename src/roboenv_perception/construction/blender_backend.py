"""Optional headless Blender mesh backend; the Artisan SDK retains physics ownership."""

import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
from pathlib import Path


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_blender(executable=None):
    candidate = executable or os.environ.get("BLENDER_BIN") or shutil.which("blender")
    if not candidate:
        raise FileNotFoundError("Blender is missing: pass --blender or set BLENDER_BIN; see docs/BLENDER_BACKEND.md")
    resolved = shutil.which(str(candidate))
    if not resolved:
        raise FileNotFoundError(f"Blender executable not found: {candidate}")
    return Path(resolved).resolve()


def validate_result(output):
    """Check artifact identity as well as actual mesh validation from the worker."""
    output = Path(output).resolve()
    report = json.loads((output / "result.json").read_text())
    if report.get("status") != "completed" or not report.get("meshes"):
        raise ValueError("Blender did not produce a completed nonempty mesh result")
    names = set()
    for mesh in report["meshes"]:
        name = mesh["name"]
        if name in names:
            raise ValueError(f"Duplicate Blender mesh: {name}")
        names.add(name)
        path = (output / mesh["file"]).resolve()
        if not path.is_relative_to(output) or not path.is_file():
            raise ValueError("Blender mesh path is missing or escapes its run directory")
        if file_hash(path) != mesh["sha256"]:
            raise ValueError(f"Blender mesh changed: {name}")
        if (not mesh["watertight"] or mesh["components"] != 1
                or not math.isfinite(mesh["volume_m3"]) or mesh["volume_m3"] <= 0):
            raise ValueError(f"Blender mesh is not one closed positive-volume component: {name}")
    for ref, expected in report["artifact_hashes"].items():
        path = (output / ref).resolve()
        if not path.is_relative_to(output) or not path.is_file() or file_hash(path) != expected:
            raise ValueError(f"Blender artifact changed or escaped its run: {ref}")
    return report


def build_blender_meshes(generator, output, *, parameters=None, blender=None, timeout=600,
                         render=True):
    """Run a trusted bpy generator in a new directory. No model/API credentials are passed."""
    executable = resolve_blender(blender)
    generator, output = Path(generator).resolve(), Path(output).resolve()
    if not generator.is_file():
        raise FileNotFoundError(generator)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Blender timeout must be positive and finite")
    parameters = {} if parameters is None else parameters
    parameter_text = json.dumps(parameters, indent=2, allow_nan=False) + "\n"
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(generator, output / "generator.py")
    worker = Path(__file__).with_name("blender_worker.py")
    shutil.copyfile(worker, output / "worker.py")
    (output / "parameters.json").write_text(parameter_text)
    request = {"render": bool(render), "generator_sha256": file_hash(output / "generator.py"),
               "worker_sha256": file_hash(output / "worker.py"),
               "parameters_sha256": file_hash(output / "parameters.json")}
    (output / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    command = [str(executable), "--background", "--factory-startup", "--threads", "4",
               "--python-exit-code", "2", "--python", str(output / "worker.py"),
               "--", str(output)]
    environment = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL",
                                                   "LD_LIBRARY_PATH") if key in os.environ}
    environment["PYTHONNOUSERSITE"] = "1"
    try:
        with (output / "blender.log").open("w") as stream:
            process = subprocess.Popen(command, cwd=output, env=environment, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise TimeoutError(f"Blender exceeded {timeout}s; see {output / 'blender.log'}") from None
        if code:
            raise RuntimeError(f"Blender exited {code}; see {output / 'blender.log'}")
        return validate_result(output)
    except Exception as error:
        failure_path = output / "failure.json"
        failure = json.loads(failure_path.read_text()) if failure_path.exists() else {}
        failure["launcher_error"] = str(error)
        failure_path.write_text(json.dumps(failure, indent=2) + "\n")
        raise
