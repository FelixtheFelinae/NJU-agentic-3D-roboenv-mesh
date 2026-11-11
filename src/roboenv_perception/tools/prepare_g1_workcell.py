"""Prepare the next G1 grasp/alignment scene after source-bound ideal tool tests.

USD authoring only. Does not operate a robot, attach the tool to its hand, or
claim that ClawSpine already hosts the environment's screw mechanics loop.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validation = json.loads(args.validation.read_text())
    asset = Path(validation["asset"])
    report_path = Path(validation["report"])
    if (not validation.get("g1_preparation_allowed") or validation["physics_status"] != "ideal_interaction_passed"
            or digest(asset) != validation["asset_sha256"] or digest(report_path) != validation["report_sha256"]):
        raise ValueError("workcell needs unchanged, source-bound ideal feasibility evidence")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(asset, args.output / "electric_screwdriver.usda")
    path = args.output / "g1_workcell.usda"
    stage = Usd.Stage.CreateNew(str(path))
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1)

    def cube(name, position, size, color):
        c = UsdGeom.Cube.Define(stage, "/World/" + name)
        c.CreateSizeAttr(1)
        c.AddTranslateOp().Set(Gf.Vec3d(*position))
        c.AddScaleOp().Set(Gf.Vec3f(*size))
        c.CreateDisplayColorAttr([Gf.Vec3f(*color)])
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
        return c

    cube("floor", (0, 0, -.025), (6, 6, .05), (.24, .27, .30))
    cube("tabletop", (.58, -.1, .725), (.55, .65, .05), (.56, .48, .35))
    for x in (.36, .8):
        for y in (-.36, .16):
            cube(f"leg_{str(x).replace('.','_')}_{str(y).replace('.','_').replace('-','n')}",
                 (x, y, .35), (.04, .04, .70), (.25, .28, .30))
    tool = UsdGeom.Xform.Define(stage, "/World/Tool")
    tool.GetPrim().GetReferences().AddReference("electric_screwdriver.usda")
    tool.AddTranslateOp(opSuffix="workcell").Set(Gf.Vec3d(.43, -.27, .83))
    # Supports at the two ends leave the central grasp volume open. Their rest
    # placement is a preparation assumption, not a measured grasp feasibility.
    for i, y in enumerate((-.35, -.197)):
        cube(f"cradle_base_{i}", (.43, y, .765), (.065, .018, .03), (.18, .24, .31))
        for side in (-1, 1):
            c = cube(f"cradle_{i}_{'left' if side < 0 else 'right'}",
                     (.43 + side*.019, y, .797), (.009, .018, .045), (.18, .24, .31))
            c.AddRotateYOp().Set(side * -40)
    cube("target_support", (.55, -.10, .763), (.06, .06, .026), (.42, .46, .50))
    # Reuse the exact successful target geometry. For alignment preparation it
    # is explicitly kinematic; a native interaction runtime must replace this
    # approximation before attempting motor-driven loosening with G1.
    bench = Usd.Stage.Open(str(report_path.parent / "initial_scene.usda"))
    flat = bench.Flatten()
    from pxr import Sdf

    Sdf.CopySpec(flat, "/World/Case_aligned/bolt", stage.GetRootLayer(), "/World/TargetScrew")
    target = stage.GetPrimAtPath("/World/TargetScrew")
    xf = UsdGeom.Xformable(target)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(.55, -.10, .780))
    UsdPhysics.RigidBodyAPI(target).CreateKinematicEnabledAttr(True)
    stage.GetRootLayer().Save()
    info = {"scene_id": "g1-electric-screwdriver-alignment-v1", "scene_usd": str(path),
            "status": "unverified", "upstream_validation": str(args.validation.resolve()),
            "upstream_report_sha256": digest(report_path), "asset_sha256": digest(asset),
            "robot_execution": "not_run", "robot_spawn_xy_floorz_yaw": [0, -.1, 0, 0],
            "observer_eye": [1.5, -1.5, 1.5], "observer_target": [.35, -.1, .85],
            "public_goal": "请用右手拿起桌上的电动螺丝刀，将刀尖对准金色螺丝的六角孔，保持对准。",
            "scope": "G1 grasp/alignment preparation; target screw kinematic, motor initially disabled",
            "ideal_hold_option": {"enabled": False, "method": "explicit fixed grasp fixture at tool grasp frame",
                                  "requirement": "bind to measured palm frame before a separately identified trial",
                                  "status": "not_attached_not_grasp_verified"},
            "before_motor_task": ["native environment must host reciprocal engagement + thread runtime",
                                  "expose generic power-tool command through the native tool loop",
                                  "measure hand-to-tool transform and stable grip/stance",
                                  "replace kinematic target with validated dynamic screw"],
            "assumptions": ["table and cradle dimensions are estimates", "spawn/reach/grasp not physically tested",
                            "fixture is a separate ideal test target, not a reconstructed cart connection"]}
    (args.output / "delivery.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    (args.output / "public-task.md").write_text(info["public_goal"] + "\n")
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
