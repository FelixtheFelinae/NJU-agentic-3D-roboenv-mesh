"""Trusted post-export normalization, executed in the separate USD runtime."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def adapt(source: Path, output: Path) -> dict:
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    if output.exists():
        raise FileExistsError(output)
    shutil.copytree(source.parent, output)
    destination = output / source.name
    stage = Usd.Stage.Open(str(destination))
    if stage is None or not stage.GetDefaultPrim().IsValid():
        raise ValueError("Artisan export has no valid default prim")
    root = stage.GetDefaultPrim()
    prefix = str(root.GetPath())
    cache = UsdGeom.XformCache()
    root_inverse = cache.GetLocalToWorldTransform(root).GetInverse()
    bodies = [prim for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
    mappings = {str(prim.GetPath()): prefix + "/" + prim.GetName() for prim in bodies}
    if len(set(mappings.values())) != len(mappings):
        raise ValueError("exported rigid bodies must have unique names")
    matrices = {str(prim.GetPath()): cache.GetLocalToWorldTransform(prim) * root_inverse
                for prim in bodies}
    for old in sorted(mappings, key=len, reverse=True):
        new = mappings[old]
        if old == new:
            continue
        if not Sdf.CopySpec(stage.GetRootLayer(), old, stage.GetRootLayer(), new):
            raise ValueError(f"could not normalize rigid body {old}")
        stage.RemovePrim(old)
        transform = UsdGeom.Xformable(stage.GetPrimAtPath(new))
        transform.ClearXformOpOrder()
        transform.AddTransformOp().Set(matrices[old])
    # Joint and mimic relationships must follow moved bodies and joint prims.
    for prim in stage.Traverse():
        for relationship in prim.GetRelationships():
            targets = relationship.GetTargets()
            remapped = []
            for target in targets:
                value = str(target)
                for old in sorted(mappings, key=len, reverse=True):
                    if value == old or value.startswith(old + "/"):
                        value = mappings[old] + value[len(old):]
                        break
                remapped.append(Sdf.Path(value))
            if targets != remapped:
                relationship.SetTargets(remapped)
    stage.GetRootLayer().Save()
    # Reopen to catch unresolved copied dependencies and stale body relations.
    reopened = Usd.Stage.Open(str(destination))
    for prim in reopened.Traverse():
        if prim.IsA(UsdPhysics.Joint):
            for name in ("physics:body0", "physics:body1"):
                for target in prim.GetRelationship(name).GetTargets():
                    if not reopened.GetPrimAtPath(target).IsValid():
                        raise ValueError(f"unresolved normalized joint body: {target}")
    result = {
        "status": "adapted", "source_usd": str(source.resolve()),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "usd": str(destination.resolve()),
        "usd_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "body_path_mapping": mappings,
        "operation": "flatten_rigid_hierarchy_preserving_rest_pose_and_joint_relationships",
        "physics_validation": "not_run",
    }
    (output / "adaptation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(adapt(args.source.resolve(), args.output.resolve())))
