"""Exercise migrated experiment commands without starting Isaac or a model client."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("legacy", "module", "option"),
    [
        ("scripts/build_video_assets.py", "wood_table.build_assets", "--artisan"),
        ("scripts/evaluate_video_assets.py", "wood_table.evaluate_assets", "--source"),
        ("construction/isaac_asset_worker.py", "wood_table.isaac_asset_worker", "--input"),
        (
            "construction/isaac_audio_scene_worker.py",
            "white_table.isaac_audio_scene_worker",
            "--record-orbit-seconds",
        ),
        ("construction/isaac_bicycle_worker.py", "bicycle.isaac_bicycle_worker", "--ground"),
        ("construction/isaac_drivetrain_worker.py", "bicycle.isaac_drivetrain_worker", "--ground"),
        ("construction/isaac_hinge_worker.py", "wood_table.isaac_hinge_worker", "--suite"),
        ("construction/isaac_robot_worker.py", "g1.isaac_robot_worker", "--input"),
        ("construction/isaac_scene_check.py", "wood_table.isaac_scene_check", "--input"),
    ],
)
def test_legacy_and_migrated_commands_accept_help(legacy, module, option):
    path = ROOT / legacy if legacy.startswith("scripts/") else ROOT / "src/roboenv_perception" / legacy
    commands = [
        [sys.executable, str(path), "--help"],
        [sys.executable, "-m", f"roboenv_perception.experiments.{module}", "--help"],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT, timeout=30)
        assert result.returncode == 0, result.stderr
        assert option in result.stdout
        assert "--output" in result.stdout or "--root" in result.stdout


@pytest.mark.parametrize("mode", ["prepare", "code", "review"])
def test_video_build_requires_explicit_artisan_before_work(mode, tmp_path):
    root = tmp_path / "not-created"
    commands = [
        [sys.executable, str(ROOT / "scripts/build_video_assets.py")],
        [sys.executable, "-m", "roboenv_perception.experiments.wood_table.build_assets"],
    ]
    for command in commands:
        result = subprocess.run(
            [*command, mode, "--root", str(root)],
            capture_output=True, text=True, cwd=ROOT, timeout=30,
        )
        assert result.returncode == 2
        assert "--artisan is required" in result.stderr
        assert not root.exists()
