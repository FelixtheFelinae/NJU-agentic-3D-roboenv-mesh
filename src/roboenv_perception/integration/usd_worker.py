"""OpenUSD-only packaging worker. No Isaac, model, controller or physics execution."""

import argparse
import hashlib
import json
import math
import os
import shutil
from pathlib import Path


def author_floor(source: Path, destination: Path, floor_z: float, size: tuple[float, float]) -> dict:
    """Add an explicit static test fixture, preserving the referenced scene."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade

    if not math.isfinite(floor_z) or abs(floor_z) > 100 or any(not math.isfinite(v) or not 0 < v <= 100 for v in size):
        raise ValueError("floor coordinates and dimensions must be finite metres")
    original = Usd.Stage.Open(str(source))
    if not original or not original.GetDefaultPrim():
        raise ValueError("source needs a valid default prim")
    if UsdGeom.GetStageMetersPerUnit(original) != 1 or UsdGeom.GetStageUpAxis(original) != "Z":
        raise ValueError("source must use metres and Z-up")
    stage = Usd.Stage.CreateNew(str(destination))
    root = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    environment = UsdGeom.Xform.Define(stage, "/World/Environment").GetPrim()
    environment.GetReferences().AddReference(str(source))
    floor = UsdGeom.Cube.Define(stage, "/World/TestFloor")
    floor.CreateSizeAttr(1)
    floor.AddTranslateOp().Set(Gf.Vec3d(0, 0, floor_z - 0.05))
    floor.AddScaleOp().Set(Gf.Vec3f(*size, 0.1))
    floor.CreateDisplayColorAttr([Gf.Vec3f(0.32, 0.34, 0.36)])
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
    material = UsdShade.Material.Define(stage, "/World/TestFloorMaterial")
    contact = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
    contact.CreateStaticFrictionAttr(0.8)
    contact.CreateDynamicFrictionAttr(0.6)
    contact.CreateRestitutionAttr(0)
    UsdShade.MaterialBindingAPI.Apply(floor.GetPrim()).Bind(material, materialPurpose="physics")
    stage.GetRootLayer().Save()
    # A physical scene must remain owned by the simulator. No gravity, robot
    # pose, joint state or body properties are overridden by this fixture.
    return {"operation": "reference_scene_and_add_static_test_floor", "floor_top_z_m": floor_z,
            "floor_size_xy_m": list(size), "floor_thickness_m": 0.1,
            "contact": {"static_friction": 0.8, "dynamic_friction": 0.6, "restitution": 0},
            "parameter_source": "assumed_test_fixture_not_visual_reconstruction",
            "physics_execution": "not_run", "robot_execution": "not_run"}


def package_scene(source: Path, output: Path) -> dict:
    from pxr import Ar, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

    if output.exists():
        raise FileExistsError(output)
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(source))
    if unresolved:
        raise ValueError(f"unresolved USD dependencies: {unresolved}")
    source_hashes = {}
    for dependency in [layer.realPath for layer in layers] + list(assets):
        if not dependency or not Path(dependency).is_file():
            raise ValueError(f"dependency is not a local file: {dependency}")
        source_hashes[dependency] = hashlib.sha256(Path(dependency).read_bytes()).hexdigest()
    output.mkdir()
    (output / "deps").mkdir()
    # Rewrite against each original layer's anchor, rather than a package-wide
    # anchor. Flattening or extracting USDZ would change composition/path rules.
    mappings = {}
    layer_paths = {str(Path(layer.realPath).resolve()) for layer in layers}
    for index, dependency in enumerate(sorted(source_hashes)):
        path = Path(dependency).resolve(strict=True)
        # Text layers avoid introducing a newer binary crate version than the
        # separately pinned Isaac runtime can read. No schema translation occurs.
        name = path.stem + ".usda" if str(path) in layer_paths else path.name
        mappings[str(path)] = output / "scene.usda" if path == source else output / "deps" / f"{index:04d}-{name}"
    for dependency, target in mappings.items():
        if dependency not in layer_paths:
            shutil.copyfile(dependency, target)
    for layer in layers:
        target = mappings[str(Path(layer.realPath).resolve())]
        copied = Sdf.Layer.CreateNew(str(target))
        copied.TransferContent(layer)

        def relocate(value):
            if not value:
                return value
            anchored = Sdf.ComputeAssetPathRelativeToLayer(layer, value)
            resolved = str(Ar.GetResolver().Resolve(anchored))
            key = str(Path(resolved).resolve()) if resolved else ""
            if key not in mappings:
                raise ValueError(f"unsupported or unresolved asset path: {value}")
            return Path(os.path.relpath(mappings[key], target.parent)).as_posix()

        UsdUtils.ModifyAssetPaths(copied, relocate)
        copied.Save()
    for dependency, expected in source_hashes.items():
        if hashlib.sha256(Path(dependency).read_bytes()).hexdigest() != expected:
            raise ValueError("USD source changed during packaging")
    destination = output / "scene.usda"
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(destination))
    if unresolved:
        raise ValueError(f"unresolved packaged dependencies: {unresolved}")
    for dependency in [layer.realPath for layer in layers] + list(assets):
        path = Path(dependency).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(output.resolve()):
            raise ValueError("packaged USD has an external dependency")
    stage = Usd.Stage.Open(str(destination))
    if not stage or not stage.GetDefaultPrim():
        raise ValueError("scene needs a valid default prim for ClawSpine's USD reference")
    if UsdGeom.GetStageMetersPerUnit(stage) != 1 or UsdGeom.GetStageUpAxis(stage) != "Z":
        raise ValueError("scene must use metres and Z-up; no implicit conversion")
    root = stage.GetDefaultPrim().GetPath()
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.IsA(UsdPhysics.Joint) or prim.HasAPI(UsdPhysics.CollisionAPI):
            if not prim.GetPath().HasPrefix(root):
                raise ValueError("physical prim outside default prim would be omitted by the ClawSpine reference")
        if prim.IsA(UsdPhysics.Joint):
            for name in ("physics:body0", "physics:body1"):
                for target in prim.GetRelationship(name).GetTargets():
                    if not stage.GetPrimAtPath(target):
                        raise ValueError(f"unresolved joint body: {target}")
    return {
        "root_layer": "scene.usda", "meters_per_unit": 1, "up_axis": "Z",
        "default_prim": str(stage.GetDefaultPrim().GetPath()),
        "dependency_closure": "local_and_resolved", "usd_version": list(Usd.GetVersion()),
        "source_files_sha256": source_hashes,
        "rigid_bodies": sum(p.HasAPI(UsdPhysics.RigidBodyAPI) for p in stage.Traverse()),
        "joints": sum(p.IsA(UsdPhysics.Joint) for p in stage.Traverse()),
        "physics_execution": "not_run", "robot_execution": "not_run",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--author-floor", type=float, help="Author a derived scene with this floor top Z")
    parser.add_argument("--floor-size", type=float, nargs=2, default=(6, 6))
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    result = package_scene(source, args.output) if args.author_floor is None else author_floor(source, args.output, args.author_floor, tuple(args.floor_size))
    print(json.dumps(result))
