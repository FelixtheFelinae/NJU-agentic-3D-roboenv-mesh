"""Public experiment commands must survive the main-branch module relocation."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "roboenv_perception."


def invoke(entry, *args):
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, *entry, *args], cwd=ROOT, env=environment,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize(("legacy", "migrated"), [
    ("scripts/build_video_assets.py", "wood_table.build_assets"),
    ("scripts/evaluate_video_assets.py", "wood_table.evaluate_assets"),
    ("construction.isaac_asset_worker", "wood_table.isaac_asset_worker"),
    ("construction.isaac_scene_check", "wood_table.isaac_scene_check"),
    ("construction.isaac_hinge_worker", "wood_table.isaac_hinge_worker"),
    ("construction.isaac_audio_scene_worker", "white_table.isaac_audio_scene_worker"),
    ("construction.isaac_bicycle_worker", "bicycle.isaac_bicycle_worker"),
    ("construction.isaac_drivetrain_worker", "bicycle.isaac_drivetrain_worker"),
    ("construction.isaac_robot_worker", "g1.isaac_robot_worker"),
])
def test_legacy_and_migrated_cli_help_agree_without_starting_isaac(legacy, migrated):
    old_entry = [legacy] if legacy.endswith(".py") else [
        "src/roboenv_perception/" + legacy.replace(".", "/") + ".py",
    ]
    old = invoke(old_entry, "--help")
    new = invoke(["-m", PACKAGE + "experiments." + migrated], "--help")
    assert old.returncode == 0, old.stderr
    assert new.returncode == 0, new.stderr
    # argparse's usage names differ for renamed scripts; descriptions/options must agree.
    assert old.stdout.split("\n\n", 1)[1] == new.stdout.split("\n\n", 1)[1]


@pytest.mark.parametrize("mode", ["prepare", "code", "review"])
@pytest.mark.parametrize("entry", [
    ["scripts/build_video_assets.py"],
    ["-m", PACKAGE + "experiments.wood_table.build_assets"],
])
def test_artisan_checkout_is_explicit_before_loading_inputs_or_credentials(tmp_path, mode, entry):
    root = tmp_path / "not-created"
    result = invoke(entry, mode, "--root", str(root))
    assert result.returncode == 2
    assert "--artisan is required for prepare/code/review" in result.stderr
    assert not root.exists()


def test_migrated_evaluation_cli_keeps_shared_report_contract(tmp_path, monkeypatch, capsys):
    from roboenv_perception.experiments.wood_table import evaluate_assets

    root, source = tmp_path / "assets", tmp_path / "video"
    calls = []

    def evaluate(actual_root, actual_source):
        calls.append((actual_root, actual_source))
        return {"assets": {"asset-0001": {"views": [{"frame": 1}], "mean_iou": 0.75}}}

    monkeypatch.setattr(evaluate_assets, "evaluate", evaluate)
    monkeypatch.setattr(sys, "argv", ["evaluate_assets", "--root", str(root), "--source", str(source)])
    evaluate_assets.main()
    assert calls == [(root, source)]
    assert ast.literal_eval(capsys.readouterr().out) == {"asset-0001": {"mean_iou": 0.75}}
