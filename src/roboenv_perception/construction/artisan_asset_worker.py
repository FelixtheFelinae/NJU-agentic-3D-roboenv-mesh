"""Standalone Artisan executor. Compilation/export never certify physical function."""

import dataclasses
import json
import sys
from pathlib import Path

if __package__:
    from .artisan_backend import (
        BACKEND,
        WORKER,
        artifact_hashes,
        file_hash,
        make_request,
        verify_inputs,
        verify_result,
        write_record,
    )
else:
    from artisan_backend import (
        BACKEND,
        WORKER,
        artifact_hashes,
        file_hash,
        make_request,
        verify_inputs,
        verify_result,
        write_record,
    )


def compile_model(workspace):
    from sdk.runtime import runtime

    outcome, display = runtime.compile_for_agent(workspace / "model.py", workspace / "compile-report.json")
    result = {"passed": outcome.status in {"pass", "ok", "success", "passed"},
              "status": outcome.status, "display": display}
    if dataclasses.is_dataclass(outcome):
        result["outcome"] = dataclasses.asdict(outcome)
    return result


def export_model(workspace, request):
    from sdk.runtime.render import export

    destination = workspace / "export"
    # Never accept a stale file left by an earlier compile/export attempt.
    if destination.exists():
        destination.rename(workspace / ".artisan-requests" / (request["request_id"] + ".previous-export"))
    destination.mkdir()
    report = export(workspace / "model.py", request["formats"], destination)
    exports = report.get("exports", {})
    for fmt in request["formats"]:
        path = Path(exports.get(fmt, ""))
        if not path.is_absolute():
            path = workspace / path
        if not path.resolve().is_relative_to(destination.resolve()) or not path.is_file():
            raise ValueError("missing current Artisan export: " + fmt)
    # Include packaged meshes/materials, not just the top-level USD/URDF/XML.
    paths = list(destination.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise ValueError("Artisan export dependencies must be bundle-local regular files")
    hashes = artifact_hashes(workspace, [p for p in paths if not p.is_dir()])
    return {"passed": True, "export_report": report, "exports": exports}, hashes


def require_compile(workspace, request):
    previous = json.loads((workspace / "compile.json").read_text())
    receipt = previous.get("backend_result", {})
    expected = {k: v for k, v in request["input_sha256"].items() if k != "compile.json"}
    compile_request = {**request, "request_id": receipt.get("request_id"), "operation": "compile",
                       "formats": [], "input_sha256": expected}
    verify_result(workspace, compile_request, previous)
    if previous.get("passed") is not True:
        raise ValueError("export requires a successful compile of the current inputs")


def union_meshes(workspace):
    import trimesh

    meshes = []
    paths = sorted((workspace / "inputs").glob("*.obj"))
    for path in paths:
        mesh = trimesh.load(path, force="mesh")
        parts = list(mesh.split())
        if len(parts) > 1:
            mesh = trimesh.boolean.union(parts, engine="manifold")
            mesh.export(path)
        meshes.append({"name": path.name, "watertight": bool(mesh.is_watertight),
                       "volume_m3": float(mesh.volume), "source_components": len(parts)})
    return {"passed": bool(meshes) and all(m["watertight"] and m["volume_m3"] > 0 for m in meshes),
            "meshes": meshes}, artifact_hashes(workspace, paths)


def execute(workspace, request):
    mode = request["operation"]
    phases = dict.fromkeys(("compile", "export", "qa", "union"), "not_run")
    receipt = {key: request[key] for key in
               ("request_id", "operation", "input_sha256", "worker_sha256", "backend_sha256")}
    receipt.update(schema_version="envcreator.artisan-result/1", execution_status="completed",
                   physics_status="not_run", phases=phases, artifact_sha256={}, failure_domain=None)
    result = {"passed": False, "backend_result": receipt}
    phase = {"build": "compile"}.get(mode, mode)
    try:
        if request.get("schema_version") != "envcreator.artisan-request/1":
            raise ValueError("unsupported Artisan request schema")
        if file_hash(WORKER) != request["worker_sha256"]:
            raise ValueError("Artisan worker differs from requested version")
        if file_hash(BACKEND) != request["backend_sha256"]:
            raise ValueError("Artisan backend differs from requested version")
        verify_inputs(workspace, request)
        if mode in {"compile", "build"}:
            old_report = workspace / "compile-report.json"
            if old_report.exists():
                old_report.rename(workspace / ".artisan-requests" /
                                  (request["request_id"] + ".previous-compile-report.json"))
            result.update(compile_model(workspace))
            phases["compile"] = "passed" if result["passed"] else "failed"
            if (workspace / "compile-report.json").is_file():
                receipt["artifact_sha256"].update(artifact_hashes(workspace, [workspace / "compile-report.json"]))
        if mode == "export" or (mode == "build" and result["passed"]):
            phase = "export"
            if mode == "export":
                require_compile(workspace, request)
            exported, hashes = export_model(workspace, request)
            result.update(exported)
            receipt["artifact_sha256"].update(hashes)
            phases["export"] = "passed"
        elif mode == "qa":
            from sdk.runtime import runtime

            result.update(runtime.run_qa_tests(workspace / "model.py", (workspace / "qa_tests.py").read_text()))
            result["passed"] = bool(result.get("ok")) and bool(result.get("passed", True)) and not result.get("failures")
            phases["qa"] = "passed" if result["passed"] else "failed"
        elif mode == "union":
            union, hashes = union_meshes(workspace)
            result.update(union)
            receipt["artifact_sha256"].update(hashes)
            phases["union"] = "passed" if result["passed"] else "failed"
        if mode != "union":
            verify_inputs(workspace, request)
        if not result["passed"]:
            receipt["failure_domain"] = "qa_findings" if mode == "qa" and not result.get("error") else phase
    except Exception as error:
        result.update(passed=False, error=f"{type(error).__name__}: {error}")
        receipt.update(execution_status="error", failure_domain=phase)
        phases[phase] = "failed"
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in {2, 3}:
        raise ValueError("usage: worker operation workspace [request.json]")
    mode, directory = args[:2]
    workspace = Path(directory).resolve()
    # Preserve direct CLI users; both invocation styles now receive bound results.
    request = json.loads(Path(args[2]).read_text()) if len(args) == 3 else make_request(workspace, mode)
    validated = make_request(workspace, mode, request.get("formats") if mode in {"build", "export"} else None)
    if any(request.get(key) != validated[key] for key in ("schema_version", "operation", "formats")):
        raise ValueError("invalid Artisan request operation/formats")
    directory = workspace / ".artisan-requests"
    directory.mkdir(exist_ok=True)
    if len(args) == 2:
        write_record(directory / (request["request_id"] + ".json"), request)
    result = execute(workspace, request)
    write_record(workspace / f"{mode}.json", result)
    write_record(directory / (request["request_id"] + ".result.json"), result)
    print(json.dumps(result, default=str))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
