"""Derive a dynamic screw workcell; native tool mechanics still need a runtime.

Separate version from the existing kinematic alignment fixture. This authors
geometry and semantics, not robot motion or a success predicate.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    foundation = Path(__file__).with_name("prepare_g1_workcell.py")
    subprocess.run([sys.executable, str(foundation), "--validation", str(args.validation),
                    "--output", str(args.output)], check=True, capture_output=True, text=True)
    output = args.output.resolve()
    initial = json.loads((output / "delivery.json").read_text())
    (output / "alignment-foundation.json").write_text(json.dumps(initial, indent=2) + "\n")
    stage_path = output / "g1_workcell.usda"
    stage = Usd.Stage.Open(str(stage_path))
    target = stage.GetPrimAtPath("/World/TargetScrew")
    UsdPhysics.RigidBodyAPI(target).GetKinematicEnabledAttr().Set(False)
    # A solid plate would intersect the copied shaft by 10 mm. Use an actual
    # 4.5 mm square passage around the 4 mm cylindrical simplified shaft.
    stage.RemovePrim("/World/target_support")
    support = UsdGeom.Xform.Define(stage, "/World/target_support")
    cx, cy, cz, outer, inner = .55, -.10, .763, .03, .00225
    for axis in (0, 1):
        for sign in (-1, 1):
            pos, size = [cx, cy, cz], [inner * 2, inner * 2, .026]
            pos[axis] += sign * (outer + inner) / 2
            size[axis] = outer - inner
            size[1 - axis] = outer * 2 if axis == 0 else inner * 2
            cube = UsdGeom.Cube.Define(stage, f"{support.GetPath()}/strip_{axis}_{'p' if sign > 0 else 'n'}")
            cube.CreateSizeAttr(1)
            cube.AddTranslateOp().Set(Gf.Vec3d(*pos))
            cube.AddScaleOp().Set(Gf.Vec3f(*size))
            cube.CreateDisplayColorAttr([Gf.Vec3f(.42, .46, .50)])
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())

    tool = stage.GetPrimAtPath("/World/Tool")
    spec = json.loads(tool.GetAttribute("envcreator:toolSpec").Get())
    for body in ("housing", "spindle"):
        prim = stage.GetPrimAtPath("/World/Tool" + spec["usd_paths"][body])
        UsdPhysics.FilteredPairsAPI.Apply(prim).CreateFilteredPairsRel().SetTargets([target.GetPath()])
    mechanics = {"schema": "envcreator.screw-interaction.v1", "tool": "/World/Tool",
                 "screw": "/World/TargetScrew", "fixed_support": str(support.GetPath()),
                 "origin_world_m": [.55, -.10, .780], "axis_world": [0, 0, 1],
                 "lead_m": .0008, "engagement_m": .004, "separation_m": .018,
                 "socket_af_m": .0031, "tip_af_m": .003,
                 "model": "force-only pitch coupling plus geometry-gated reciprocal hex torque",
                 "runtime_binding": "pending; metadata alone does not execute mechanics",
                 "bit_socket_contact": "filtered; requires declared virtual coupling runtime",
                 "assumptions": ["fixed anti-rotated threaded support", "estimated M4-like shaft and pitch",
                                 "ideal task target, not an identified cart fastener",
                                 "no thread mesh, magnetic retention or automatic pickup"]}
    target.CreateAttribute("envcreator:fastenerSpec", Sdf.ValueTypeNames.String).Set(json.dumps(mechanics))
    stage.GetRootLayer().Save()
    result = {**initial, "scene_id": "g1-electric-screwdriver-dynamic-v1", "status": "unverified",
              "scene_sha256": digest(stage_path), "target_kinematic": False,
              "public_goal": "让机器人抓握这把螺丝刀 尝试拧松并取下螺丝",
              "scope": "dynamic workcell authoring; motor/thread runtime and G1 execution unverified",
              "interaction": mechanics, "native_mechanics_ready": False,
              "before_motor_task": ["bind force-only mechanics at each native physics substep",
                                    "expose motor commands through native DSH plugin",
                                    "validate gravitational equilibrium and grasp/reach in pinned container"],
              "sources": {str(foundation.resolve()): digest(foundation),
                          str(Path(__file__).resolve()): digest(__file__)}}
    (output / "delivery.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (output / "interaction.json").write_text(json.dumps(mechanics, ensure_ascii=False, indent=2) + "\n")
    (output / "public-task.md").write_text(result["public_goal"] + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
