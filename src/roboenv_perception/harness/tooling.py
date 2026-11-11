"""Tool generation: retrieved references -> existing builder -> executable feasibility.

The first registered tool is a reference-informed electric screwdriver. Future
registrations need their own builder and frozen positive/negative interaction tests.
"""

import subprocess
from dataclasses import asdict
from pathlib import Path

from ..construction.asset_agents import child_environment
from ..io import read_json, sha256, write_json
from ..tools.electric_screwdriver import REFERENCE_URL, build_tool
from .web import ObjectIdentity, WebEvidenceClient, WebSearchPolicy

REQUIRED_CASES = {"aligned", "offset", "tilted", "no_contact", "wrong_socket", "motor_off"}
REQUIRED_CHECKS = {"source_unchanged", "scene_unchanged", "auto_alignment_and_insertion",
                   "screw_turns_under_tool_motor", "thread_clear_after_five_turns", "helical_coupling",
                   "offset_challenge_exercised", "tilt_challenge_exercised", "gap_challenge_exercised",
                   "size_challenge_exercised", "motor_off_still_engages"}


def collect_tool_reference(output: Path, *, client=None, dns_resolver="google") -> dict:
    output.mkdir(parents=True, exist_ok=False)
    client = client or WebEvidenceClient(WebSearchPolicy(
        official_domains=("bosch-professional.com",), allowed_source_domains=("bosch-professional.com",),
        timeout_seconds=20, max_text_chars=30000, dns_resolver=dns_resolver))
    identity = ObjectIdentity("electric_screwdriver_v1", "electric screwdriver", "Bosch", "Bosch GO")
    source = client.fetch_source(identity, REFERENCE_URL, "Bosch GO official product page")
    record = asdict(source)
    text = source.text_excerpt
    (output / "reference.txt").write_text(text + "\n")
    # The numeric geometry is still an explicit preset/assumption. This check only
    # establishes that its declared drive interface is supported by fetched text.
    holder = '1/4"' in text or "1/4”" in text or "1/4 inch" in text
    ready = (source.retrieval_status == "fetched" and source.source_kind == "official"
             and source.applicability == "model_mentioned" and holder)
    result = {"status": "fetched" if ready else "unverified",
              "method": "known manufacturer reference fetched through bounded WebEvidenceClient",
              "sources": [record], "evidence_map": {"reference:bosch-go": source.source_id},
              "interface_quote_found": holder, "text_sha256": sha256(output / "reference.txt"),
              "scope": "retrieved reference supports product form and shank interface; tool dynamics are assumptions",
              "source_text_is_instruction": False}
    write_json(output / "research.json", result)
    return result


def audit_interaction(report: dict, asset: Path) -> bool:
    """Require source binding and negative controls before allowing G1 preparation."""
    checks = report.get("checks", {})
    if (report.get("status") != "passed" or report.get("runtime_pose_writes") is not False
            or set(report.get("cases", {})) != REQUIRED_CASES
            or not REQUIRED_CHECKS <= set(checks) or not all(v is True for v in checks.values())
            or report.get("source_hashes", {}).get(str(asset.resolve())) != sha256(asset)):
        return False
    for case in REQUIRED_CASES:
        required = {case + "_finite", case + "_reciprocal_torque"}
        if case != "aligned":
            required.add(case + "_no_false_loosening")
        if case not in {"aligned", "motor_off"}:
            required.add(case + "_no_virtual_torque")
        if not required <= set(checks):
            return False
    return True


def test_tool(asset: Path, output: Path, isaac_python: Path | None, *, reference=None) -> dict:
    asset = asset.resolve(strict=True)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    reference = reference or collect_tool_reference(output / "research")
    worker = Path(__file__).parents[1] / "tools/isaac_screwdriver_worker.py"
    write_json(output / "plan.json", {"tool": "electric-screwdriver", "asset_sha256": sha256(asset),
        "worker_sha256": sha256(worker), "cases": sorted(REQUIRED_CASES),
        "gate": "fetched reference, successful process, source-bound positive and negative physics evidence",
        "g1_grasp": "not_run", "g1_alignment": "not_run"})
    result = {"status": "unverified", "asset": str(asset), "asset_sha256": sha256(asset),
              "research_status": reference["status"], "robot_execution": "not_run",
              "g1_preparation_allowed": False, "physics_status": "not_run"}
    if isaac_python is not None:
        with (output / "interaction.log").open("w") as log:
            try:
                run = subprocess.run([str(isaac_python), str(worker), "--asset", str(asset),
                                      "--output", str(output / "interaction")],
                                     stdout=log, stderr=subprocess.STDOUT, env=child_environment(), timeout=600)
                result["process_exit_code"] = run.returncode
            except subprocess.TimeoutExpired:
                result["process_exit_code"] = None
                result["error"] = "interaction worker exceeded 600 seconds"
        report_path = output / "interaction/report.json"
        if report_path.is_file():
            report = read_json(report_path)
            passed = result.get("process_exit_code") == 0 and audit_interaction(report, asset)
            result.update(physics_status="ideal_interaction_passed" if passed else "failed",
                          report=str(report_path), report_sha256=sha256(report_path),
                          g1_preparation_allowed=passed and reference["status"] == "fetched")
            if passed and reference["status"] == "fetched":
                result["status"] = "ideal_tool_verified"
        else:
            result["physics_status"] = "failed"
    result["scope"] = "ideal tool-fixture feasibility; robot grasp and screw contact remain separate experiments"
    write_json(output / "validation.json", result)
    return result


def run_tool_harness(output: Path, artisan_python: Path, blender: Path | None = None,
                     isaac_python: Path | None = None) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    research = collect_tool_reference(output / "research")
    built = build_tool(output / "build", artisan_python, blender)
    validation = test_tool(Path(built["scene_usd"]), output / "feasibility", isaac_python, reference=research)
    delivery = read_json(Path(built["delivery"]))
    delivery.update(tool_research={"report": str(output / "research/research.json"),
                                   "sha256": sha256(output / "research/research.json")},
                    tool_validation=validation, physics_status=validation["physics_status"])
    # A local ideal fixture is not a full generated scene/robot-task verification.
    delivery["status"] = "unverified"
    write_json(output / "delivery.json", delivery)
    return {**built, "delivery": str(output / "delivery.json"), "validation": validation}
