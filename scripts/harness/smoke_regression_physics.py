"""Normalize an example export, compose a scene, and run a generic physics probe.

Run this script in the repository environment. Supply a separate USD/Artisan
Python with pxr installed; the repository environment does not need USD bindings.
"""

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from roboenv_perception.harness import usd_adapter
from roboenv_perception.harness.physics import PhysicsProtocol, PhysicsTask, run_physics_task
from roboenv_perception.harness.scene import SceneAsset, SupportSpec, assemble_scene

# The external runtime imports the standalone adapter without importing the
# repository package or depending on its Pydantic environment.
_ADAPT_AND_MEASURE = """
import json
import runpy
import sys
from pathlib import Path
from pxr import Usd, UsdGeom
adapter, source, output, bounds_path = map(Path, sys.argv[1:])
result = runpy.run_path(str(adapter))["adapt"](source, output)
stage = Usd.Stage.Open(result["usd"])
bound = UsdGeom.BBoxCache(0, ["default"]).ComputeWorldBound(
    stage.GetDefaultPrim()).ComputeAlignedRange()
bounds_path.write_text(json.dumps([list(bound.GetMin()), list(bound.GetMax())]) + "\\n")
print(json.dumps(result))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=["rigid", "hinge"], required=True)
    parser.add_argument("--source", type=Path, required=True, help="Existing exported object.usd")
    parser.add_argument("--output", type=Path, required=True, help="Fresh experiment directory")
    parser.add_argument("--hinge-effort-Nm", type=float, default=0.005)
    parser.add_argument(
        "--isaac-python",
        type=Path,
        default=Path.home() / ".local/share/roboenv/isaac-env/bin/python",
        help="Python environment providing Isaac Lab and Isaac Sim",
    )
    parser.add_argument(
        "--usd-python",
        "--artisan-python",
        dest="usd_python",
        type=Path,
        required=True,
        help="Python with pxr bindings, such as the Artisan environment",
    )
    parser.epilog = (
        "The hinge example expects lid_hinge, lid/base links and world +X torque "
        "(the white-table charging-case export). The rigid example expects one body. "
        "Both use an assumed 1m square support; these are regression fixtures."
    )
    args = parser.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    bounds_path = output / "bounds.json"
    with (output / "adapter.log").open("w") as log:
        subprocess.run(
            [
                str(args.usd_python.expanduser().resolve(strict=True)),
                "-c",
                _ADAPT_AND_MEASURE,
                str(Path(usd_adapter.__file__).resolve()),
                str(source),
                str(output / "adapted"),
                str(bounds_path),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    lo, hi = json.loads(bounds_path.read_text())
    scene = assemble_scene(
        [
            SceneAsset(
                asset_id="probe",
                kind="rigid" if args.family == "rigid" else "articulated",
                usd_path=output / "adapted" / source.name,
                frame_id="fixture",
                bbox_xyxy=(40, 40, 60, 60),
                image_size_xy=(100, 100),
                local_bounds_min_m=tuple(lo),
                local_bounds_max_m=tuple(hi),
                bounds_source="measured",
                evidence_refs=[str(source)],
                bounds_note="Rest visual world bounds measured from USD for fixture placement.",
            )
        ],
        output / "scene",
        SupportSpec(
            frame_id="fixture",
            image_size_xy=(100, 100),
            image_bbox_xyxy=(0, 0, 100, 100),
            size_x_m=1.0,
            size_y_m=1.0,
            top_z_m=0.0,
            evidence_refs=["synthetic_validation_fixture"],
        ),
    )
    common = dict(target_prim="/World/Assets/Asset_probe", min_response=0.01, max_baseline=0.002)
    if args.family == "rigid":
        protocol = PhysicsProtocol(
            family="rigid",
            force_world_N=(3.0, 0.0, 0.0),
            drive_s=0.15,
            release_s=0.1,
            **common,
        )
    else:
        protocol = PhysicsProtocol(
            family="hinge",
            joint_name="lid_hinge",
            moving_body_name="lid",
            base_body_name="base",
            torque_axis_world=(1.0, 0.0, 0.0),
            effort_Nm=args.hinge_effort_Nm,
            drive_s=0.25,
            **common,
        )
    result = run_physics_task(
        PhysicsTask(
            scene_usd=scene / "scene.usda",
            output_dir=output / "physics",
            python_executable=args.isaac_python.expanduser(),
            protocol=protocol,
            timeout_s=180,
        )
    )
    summary = json.dumps(asdict(result), default=str, indent=2)
    (output / "audit.json").write_text(summary + "\n")
    print(summary)
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
