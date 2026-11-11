"""Independent, bounded pinned-Isaac bench dispatch for installed standard bundles.

The v9 protocol is explicitly limited to the original three-fastener fixture.
Other layouts remain unsupported until a corresponding bench adapter is registered.
"""

import argparse
import importlib.util
import json
import subprocess
import time
from pathlib import Path

from ..io import read_json, relative_file, sha256, write_json
from .compiler import verify_compiled
from .observation import evaluate_release_records

IMAGE = "sha256:d08db932be1f34d1b69162aef98845ed53197ed535fbc42b59d2e43a59bf24c6"
CASES = {
    "preflight": {"count": 0, "seconds": 6, "rpm": 30, "fault": "none"},
    "one-fast": {"count": 1, "seconds": 55, "rpm": 120, "fault": "none"},
    "motor-off": {"count": 1, "seconds": 35, "rpm": 30, "fault": "motor_off"},
}


def verify_bundle(bundle):
    bundle = Path(bundle).resolve()
    manifest = read_json(bundle / "manifest.json")
    for ref, expected in manifest["files_sha256"].items():
        path = relative_file(bundle, ref)
        if sha256(path) != expected:
            raise ValueError("bundle artifact changed: " + ref)
    compiled = read_json(bundle / "compiled_assembly.json")
    verify_compiled(compiled)
    return compiled


def protocol_supported(compiled):
    rows = compiled["connections"]
    return (
        len(rows) == 3
        and [r["binding"]["screw"] for r in rows] == [f"/World/screw_{i}" for i in range(3)]
        and all(
            r["binding"]["support"] == "/World/body"
            and r["binding"]["clamped_member"] == "/World/lid"
            and r["binding"]["engagement_m"] == 0.008
            and r["binding"]["removal_distance_m"] == 0.024
            for r in rows
        )
    )


def run_validation(bundle, output, *, cases=("preflight",), timeout_s=7200, repo=None):
    from pxr import Usd

    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    if not cases or any(name not in CASES for name in cases) or len(set(cases)) != len(cases):
        raise ValueError("select distinct registered validation cases")
    compiled = verify_bundle(bundle)
    output.mkdir(parents=True)
    report = {
        "schema": "envcreator.standard-validation/1",
        "status": "unverified",
        "scope": "independent ideal fixed-base/carriage bench; not assembled-scene or robot validation",
        "scene_physics_status": "not_run",
        "compiled_sha256": compiled["compiled_sha256"],
        "bundle_manifest_sha256": sha256(bundle / "manifest.json"),
        "cases": [],
    }
    if not protocol_supported(compiled):
        report.update(
            status="unsupported",
            issues=["v9 bench adapter only supports the registered three-fastener layout"],
        )
        write_json(output / "validation.json", report)
        return report
    repo = Path(repo) if repo else Path(__file__).resolve().parents[3]
    inputs = output / "inputs"
    inputs.mkdir()
    import shutil

    shutil.copytree(bundle / "runtime", inputs / "tools")
    shutil.copy2(bundle / "tool.usda", inputs / "tool.usda")
    # Independent bench installs its declared carriage; remove the unheld packaged tool.
    asset = Usd.Stage.Open(str(bundle / "assembly.usda"))
    asset.GetRootLayer().Export(str(inputs / "box.usda"))
    asset = Usd.Stage.Open(str(inputs / "box.usda"))
    asset.RemovePrim("/World/WristTool")
    asset.GetRootLayer().Save()
    bench = inputs / "bench"
    bench.mkdir()
    for name in ("validate_tool.py", "assembly_protocol.py", "evaluate_assembly.py"):
        shutil.copy2(repo / "experiments/triangular_box" / name, bench / name)
    # Frozen base evaluator stays unchanged; observer evidence is supplemental.
    evaluator_record = next(
        r
        for r in compiled["connections"][0]["components"]["protocol"]["source_files"]
        if r["path"].endswith("evaluate_assembly.py")
    )
    if sha256(bench / "evaluate_assembly.py") != evaluator_record["sha256"]:
        raise ValueError("base acceptance evaluator differs from registered protocol")
    provenance = {
        "image": IMAGE,
        "base_protocol": "assembly-v9@1",
        "instrumentation": "envcreator.release-observation/1",
        "files_sha256": {
            str(p.relative_to(inputs)): sha256(p) for p in inputs.rglob("*") if p.is_file()
        },
        "conditions": "ideal XYZ carriage and fixed base, privileged poses/internal states; no robot",
        "cases": {name: CASES[name] for name in cases},
    }
    write_json(inputs / "provenance.json", provenance)
    spec = importlib.util.spec_from_file_location(
        "frozen_assembly_evaluator", bench / "evaluate_assembly.py"
    )
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    deadline = time.monotonic() + timeout_s
    for name in cases:
        cfg = CASES[name]
        folder = output / name
        folder.mkdir()
        container = "envcreator-standard-" + name + "-" + str(time.time_ns())[-9:]
        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            container,
            "--gpus",
            "all",
            "--network",
            "none",
            "-e",
            "ACCEPT_EULA=Y",
            "-e",
            "PRIVACY_CONSENT=Y",
            "-e",
            "OMNI_KIT_ALLOW_ROOT=1",
            "-e",
            "OMP_NUM_THREADS=1",
            "-e",
            "OPENBLAS_NUM_THREADS=1",
            "-e",
            "MKL_NUM_THREADS=1",
            "-v",
            f"{inputs}:/inputs:ro",
            "-v",
            f"{inputs / 'tools'}:/repo/src/roboenv_perception/tools:ro",
            "-v",
            f"{folder}:/results:rw",
            IMAGE,
            "/inputs/bench/validate_tool.py",
            "--asset",
            "/inputs/box.usda",
            "--tool",
            "/inputs/tool.usda",
            "--output",
            "/results/result.json",
            "--assembly-case",
            "negative" if cfg["fault"] != "none" else "sequence",
            "--boundary",
            "fixed",
            "--remove-count",
            str(cfg["count"]),
            "--order",
            "0,1,2",
            "--rpm",
            str(cfg["rpm"]),
            "--fault",
            cfg["fault"],
            "--seconds",
            str(cfg["seconds"]),
            "--dt",
            ".001",
            "--stage-timeout",
            "38",
            "--synchronous-release",
        ]
        receipt = {
            "name": name,
            "argv": argv,
            "status": "execution_failed",
            "started_unix": time.time(),
        }
        write_json(folder / "receipt.json", receipt)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("validation wall time exhausted")
            with (folder / "result.log").open("w") as log:
                process = subprocess.run(
                    argv, stdout=log, stderr=subprocess.STDOUT, timeout=remaining
                )
            receipt["returncode"] = process.returncode
            if process.returncode == 0 and (folder / "result.json").is_file():
                result = read_json(folder / "result.json")
                acceptance = evaluator.evaluate(result, (folder / "result.log").read_text())
                write_json(folder / "base_evaluation.json", acceptance)
                released_ids = [
                    f"/World/screw_{i}"
                    for i in range(3)
                    if any(
                        not row["screws"][i]["connector_enabled"] for row in result.get("trace", [])
                    )
                ]
                synchronous = evaluate_release_records(
                    result.get("synchronous_releases", []), expected_ids=released_ids
                )
                write_json(folder / "release_evaluation.json", synchronous)
                receipt["status"] = (
                    "passed"
                    if (
                        acceptance["status"] == "passed"
                        and synchronous["status"] in {"passed", "not_exercised"}
                    )
                    else "failed"
                )
                receipt["base_status"] = acceptance["status"]
                receipt["release_observation_status"] = synchronous["status"]
        except Exception as error:
            receipt["error"] = f"{type(error).__name__}: {error}"
            subprocess.run(
                ["docker", "stop", "--time", "10", container], capture_output=True, timeout=30
            )
        receipt["finished_unix"] = time.time()
        receipt["files_sha256"] = {
            p.name: sha256(p) for p in folder.iterdir() if p.is_file() and p.name != "receipt.json"
        }
        write_json(folder / "receipt.json", receipt)
        report["cases"].append(receipt)
        write_json(output / "validation.json", report)
    report["bench_status"] = (
        "passed" if all(r["status"] == "passed" for r in report["cases"]) else "failed"
    )
    report["status"] = (
        "preflight_passed"
        if cases == ("preflight",) and report["bench_status"] == "passed"
        else "unverified"
    )
    report["functional_scope"] = (
        "one-fast removal and motor-off control"
        if "one-fast" in cases
        else "no powered removal verified"
    )
    write_json(output / "validation.json", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cases", nargs="+", choices=CASES, default=["preflight"])
    p.add_argument("--timeout", type=float, default=7200)
    a = p.parse_args()
    report = run_validation(a.bundle, a.output, cases=tuple(a.cases), timeout_s=a.timeout)
    print(json.dumps(report))
    return 0 if report.get("bench_status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
