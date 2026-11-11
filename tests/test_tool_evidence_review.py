"""Evidence reuse must retain its source binding and may not become a new run."""

import subprocess
import venv
from pathlib import Path

from roboenv_perception.integration import tool_backend
from roboenv_perception.io import read_json, sha256, write_json


def test_evidence_reuse_checks_asset_report_code_and_scene(tmp_path, monkeypatch):
    asset, scene = tmp_path / "tool.usda", tmp_path / "initial_scene.usda"
    asset.write_text("tool")
    scene.write_text("scene")
    sources = {str(asset.resolve()): sha256(asset)}
    root = Path(tool_backend.__file__).parents[1] / "tools"
    sources.update({str(root / file): sha256(root / file) for file in (
        "isaac_screwdriver_worker.py", "power_tool_usd.py", "screwdriving.py", "fastener.py")})
    report_path, validation_path = tmp_path / "report.json", tmp_path / "validation.json"
    report = {"source_hashes": sources, "scene_sha256": sha256(scene)}
    write_json(report_path, report)
    validation = {"report": str(report_path), "asset_sha256": sha256(asset),
                  "report_sha256": sha256(report_path), "status": "ideal_tool_verified"}
    write_json(validation_path, validation)
    monkeypatch.setattr(tool_backend, "audit_interaction", lambda *args: True)
    result = tool_backend.review_evidence(validation_path, asset)
    assert result["status"] == "existing_evidence_valid"
    assert result["simulation_executed_this_call"] is False
    assert result["robot_execution"] == "not_run"
    asset.write_text("different tool")
    assert tool_backend.review_evidence(validation_path, asset)["status"] == "unverified"
    asset.write_text("tool")
    scene.write_text("different scene")
    assert tool_backend.review_evidence(validation_path, asset)["status"] == "unverified"
    scene.write_text("scene")
    sources.pop(str(root / "screwdriving.py"))
    write_json(report_path, report)
    # Even a freshly rehashed report must still match the current source files.
    validation["report_sha256"] = sha256(report_path)
    write_json(validation_path, validation)
    assert not tool_backend.review_evidence(validation_path, asset)["checks"]["source_code_unchanged"]
    assert read_json(validation_path)["status"] == "ideal_tool_verified"  # original verdict preserved


def test_profile_launches_the_configured_virtualenv_with_its_installed_modules(tmp_path):
    environment = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin/python"
    probe = subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                           capture_output=True, text=True, check=True)
    (Path(probe.stdout.strip()) / "envcreator_runtime_probe.py").write_text("VALUE = 'installed-in-venv'\n")
    asset = tmp_path / "tool.usda"
    asset.write_text("#usda 1.0\n")
    prepared = tool_backend.prepare_profile(tmp_path / "profile", asset, None,
                                           python=python, usd_python=python,
                                           isaac_python=python, artisan_python=python)
    config = read_json(Path(prepared["config"]))
    for key in ("python", "usd_python", "isaac_python", "artisan_python"):
        result = subprocess.run([config[key], "-c", "import envcreator_runtime_probe as p; print(p.VALUE)"],
                                capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "installed-in-venv"
