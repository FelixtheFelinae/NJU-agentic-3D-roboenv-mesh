"""Resolve referenced standard assembly paths and translation at scene composition time."""

import argparse
import json
from pathlib import Path

from ..io import sha256, write_json


def adapt_scene(scene):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.Open(str(Path(scene).resolve()))
    records = []
    cache = UsdGeom.XformCache()
    for prim in stage.Traverse():
        attr = prim.GetAttribute("envcreator:fastenerSpec")
        if not attr or not attr.Get():
            continue
        spec = json.loads(attr.Get())
        if not spec.get("standard_lock"):
            continue
        root = prim.GetParent()
        prefix = str(root.GetPath())
        # Current native runtime supports world +Z; reject rotated/scaled placement.
        matrix = cache.GetLocalToWorldTransform(root)
        if not Gf.IsClose(matrix.ExtractRotationMatrix(), Gf.Matrix3d(1.0), 1e-8):
            raise ValueError("standard connection scene placement supports translation only")
        for key in (
            "screw",
            "tool",
            "moving_support",
            "clamped_member",
            "guide_joint",
            "lead_joint",
            "helper_body",
            "rotor_body",
            "friction_joint",
        ):
            if spec.get(key):
                spec[key] = prefix + "/" + spec[key].rsplit("/", 1)[-1]
                if not stage.GetPrimAtPath(spec[key]):
                    raise ValueError("unresolved relocated standard binding: " + spec[key])
        support = stage.GetPrimAtPath(spec["moving_support"])
        if not support.HasAPI(UsdPhysics.RigidBodyAPI):
            raise ValueError("relocated support is not a rigid body")
        world_origin = cache.GetLocalToWorldTransform(support).Transform(
            Gf.Vec3d(*spec["origin_support_local_m"])
        )
        spec["origin_world_m"] = list(world_origin)
        spec["solver_thread"]["lid_body"] = spec["clamped_member"]
        attr.Set(json.dumps(spec))
        for joint_key in ("guide_joint", "lead_joint", "friction_joint"):
            joint = UsdPhysics.Joint(stage.GetPrimAtPath(spec[joint_key]))
            if not joint:
                raise ValueError("missing relocated standard joint")
            for relation in (joint.GetBody0Rel(), joint.GetBody1Rel()):
                for target in relation.GetTargets():
                    if not stage.GetPrimAtPath(target):
                        raise ValueError("unresolved composed constraint body: " + str(target))
        records.append(
            {
                "screw": str(prim.GetPath()),
                "tool": spec["tool"],
                "support": spec["moving_support"],
                "origin_world_m": list(world_origin),
                "origin_support_local_m": spec["origin_support_local_m"],
                "compiled_sha256": spec["standard_lock"]["compiled_sha256"],
                "frame_policy": "asset-local compile; translated scene instance; world +Z",
            }
        )
    stage.GetRootLayer().Save()
    report = {
        "schema": "envcreator.standard-scene-bindings/1",
        "bindings": records,
        "scene_sha256": sha256(Path(scene)),
        "physics_status": "not_run",
    }
    write_json(Path(scene).with_name("standard_bindings.json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(adapt_scene(args.scene)))


if __name__ == "__main__":
    main()
