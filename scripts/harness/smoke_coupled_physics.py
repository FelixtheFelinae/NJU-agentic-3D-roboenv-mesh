"""Audit the example native 2.5:1 crank/wheel export and disconnected control."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from roboenv_perception.harness.physics import PhysicsProtocol, PhysicsTask, run_physics_task


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True, help="Assembled example scene.usda")
    parser.add_argument("--output", type=Path, required=True, help="Fresh physics output directory")
    parser.add_argument(
        "--isaac-python",
        type=Path,
        default=Path.home() / ".local/share/roboenv/isaac-env/bin/python",
        help="Python environment providing Isaac Lab and Isaac Sim",
    )
    parser.epilog = (
        "Example fixture: /World/Assets/Asset_asset_0002, spin_crank/spin_wheel joints, "
        "crank/body links, world +Y torque, native ratio 2.5."
    )
    args = parser.parse_args()
    protocol = PhysicsProtocol(
        family="coupled",
        target_prim="/World/Assets/Asset_asset_0002",
        min_response=0.03,
        max_baseline=0.02,
        joint_name="spin_crank",
        follower_joint_name="spin_wheel",
        moving_body_name="crank",
        base_body_name="body",
        torque_axis_world=(0.0, 1.0, 0.0),
        effort_Nm=0.02,
        target_ratio=2.5,
        max_ratio_residual=0.02,
        max_disconnected_response=0.02,
        drive_s=0.25,
    )
    result = run_physics_task(
        PhysicsTask(
            scene_usd=args.scene,
            output_dir=args.output,
            python_executable=args.isaac_python.expanduser(),
            protocol=protocol,
        )
    )
    summary = json.dumps(asdict(result), default=str, indent=2)
    args.output.parent.joinpath(args.output.name + "-audit.json").write_text(summary + "\n")
    print(summary)
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
