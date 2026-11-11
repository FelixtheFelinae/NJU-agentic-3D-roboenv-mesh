"""Install frozen components into an exported object and deliver portable USD + runtime.

The caller supplies object geometry and instance bindings. No standard is repaired here.
"""

import argparse
import json
import shutil
from pathlib import Path

from ..io import read_json, sha256, write_json
from .catalog import StandardComponentError, load_catalog
from .compiler import compile_connections, verify_compiled
from .usd import author_connections


def remap_relationships(stage, mapping):
    from pxr import Sdf

    for prim in stage.Traverse():
        for rel in prim.GetRelationships():
            targets = []
            for target in rel.GetTargets():
                for old, new in sorted(mapping.items(), key=lambda item: -len(item[0])):
                    if target.HasPrefix(Sdf.Path(old)):
                        target = target.ReplacePrefix(Sdf.Path(old), Sdf.Path(new))
                        break
                targets.append(target)
            rel.SetTargets(targets)


def frozen_source(repo, suffix, override=None):
    record = next(r for r in load_catalog()["sources"] if r["path"].endswith(suffix))
    path = Path(override) if override else Path(repo) / record["path"]
    if not path.is_file() or sha256(path) != record["sha256"]:
        raise StandardComponentError(f"missing or changed registered component asset: {path}")
    return path


def install_bundle(
    source, compiled, output, *, component_root=None, fastener_asset=None, tool_asset=None
):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    source, output = Path(source).resolve(), Path(output).resolve()
    verify_compiled(compiled)
    if output.exists():
        raise FileExistsError(output)
    repo = Path(component_root) if component_root else Path(__file__).resolve().parents[3]
    fastener_path = frozen_source(repo, "delivery/triangular_box.usda", fastener_asset)
    tool_path = frozen_source(repo, "0001-electric_screwdriver.usda", tool_asset)
    original = Usd.Stage.Open(str(source))
    if not original or not original.GetDefaultPrim():
        raise ValueError("source needs a valid default prim")
    if UsdGeom.GetStageUpAxis(original) != "Z" or UsdGeom.GetStageMetersPerUnit(original) != 1.0:
        raise ValueError("standard installation requires a metre-scale Z-up export")
    # Flatten references and use a canonical asset-local root. Scene references may relocate it.
    old_root = str(original.GetDefaultPrim().GetPath())
    layer = original.Flatten()
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    Sdf.CopySpec(layer, old_root, stage.GetRootLayer(), "/World")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/World"))
    remap_relationships(stage, {old_root: "/World"})
    # Bind by actual normalized sibling body names, never the old USD root spelling.
    bindings = []
    for row in compiled["connections"]:
        b = dict(row["binding"])
        for key in ("screw", "support", "clamped_member", "tool"):
            b[key] = "/World/" + b[key].rsplit("/", 1)[-1]
        support = stage.GetPrimAtPath(b["support"])
        if not support or not support.HasAPI(UsdPhysics.RigidBodyAPI):
            raise ValueError("missing exported support body: " + b["support"])
        transform = UsdGeom.XformCache().GetLocalToWorldTransform(support)
        b["origin_m"] = list(transform.GetInverse().Transform(Gf.Vec3d(*b["origin_m"])))
        bindings.append(b)
    installed = compile_connections(bindings, tool_profile=compiled["tool_profile"])
    registered = {b[k] for b in bindings for k in ("screw", "support", "clamped_member")}
    detached = {b[k] for b in bindings for k in ("screw", "clamped_member")}
    generated = {p for row in installed["connections"] for p in row["paths"].values()}
    # Remove only explicit assembly closure joints. Unknown constraints fail closed.
    removed = []
    for prim in list(stage.Traverse()):
        if not prim.IsA(UsdPhysics.Joint) or str(prim.GetPath()) in generated:
            continue
        joint = UsdPhysics.Joint(prim)
        targets = {
            str(p) for rel in (joint.GetBody0Rel(), joint.GetBody1Rel()) for p in rel.GetTargets()
        }
        if targets & detached:
            if (
                not prim.IsA(UsdPhysics.FixedJoint)
                or not targets <= registered
                or len(targets) != 2
            ):
                raise ValueError(f"unresolved constraint on detachable member: {prim.GetPath()}")
            removed.append(str(prim.GetPath()))
    for path in removed:
        stage.RemovePrim(path)
    template = Usd.Stage.Open(str(fastener_path))
    template_layer = template.Flatten()
    for name in ("Material", "SteelContact"):
        target = "/World/" + name
        if not stage.GetPrimAtPath(target):
            Sdf.CopySpec(template_layer, target, stage.GetRootLayer(), target)
    for row in installed["connections"]:
        b = row["binding"]
        if not stage.GetPrimAtPath(b["screw"]):
            raise ValueError("missing exported standard body: " + b["screw"])
        stage.RemovePrim(b["screw"])
        Sdf.CopySpec(template_layer, "/World/screw_0", stage.GetRootLayer(), b["screw"])
        prim = stage.GetPrimAtPath(b["screw"])
        xf = UsdGeom.Xformable(prim)
        xf.ClearXformOpOrder()
        support_transform = UsdGeom.XformCache().GetLocalToWorldTransform(
            stage.GetPrimAtPath(b["support"])
        )
        xf.AddTranslateOp().Set(support_transform.Transform(Gf.Vec3d(*b["origin_m"])))
        # Remove inherited fixture-specific strings before authoring registered bindings.
        prim.GetAttribute("envcreator:fastenerSpec").Set("{}")
        UsdPhysics.FilteredPairsAPI.Apply(prim).CreateFilteredPairsRel().SetTargets([b["support"]])
    tool_target = bindings[0]["tool"]
    if stage.GetPrimAtPath(tool_target):
        raise ValueError("tool target already occupied; implicit replacement is not supported")
    tool_stage = Usd.Stage.Open(str(tool_path))
    tool_root = str(tool_stage.GetDefaultPrim().GetPath())
    Sdf.CopySpec(tool_stage.Flatten(), tool_root, stage.GetRootLayer(), tool_target)
    # Map only copied tool relationships; other object prims must not be remapped.
    for prim in Usd.PrimRange(stage.GetPrimAtPath(tool_target)):
        for rel in prim.GetRelationships():
            rel.SetTargets(
                [
                    p.ReplacePrefix(Sdf.Path(tool_root), Sdf.Path(tool_target))
                    if p.HasPrefix(Sdf.Path(tool_root))
                    else p
                    for p in rel.GetTargets()
                ]
            )
    tool = stage.GetPrimAtPath(tool_target)
    tool_spec = json.loads(tool.GetAttribute("envcreator:toolSpec").Get())
    tool_spec["motor"] = installed["connections"][0]["components"]["tool"]["motor"]
    tool.GetAttribute("envcreator:toolSpec").Set(json.dumps(tool_spec))
    xf = UsdGeom.Xformable(tool)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(0.30, 0.23, 0.30))
    xf.AddOrientOp().Set(Gf.Quatf(2**-0.5, Gf.Vec3f(-(2**-0.5), 0, 0)))
    for b in bindings:
        for name in ("housing", "spindle"):
            UsdPhysics.FilteredPairsAPI.Apply(
                stage.GetPrimAtPath(tool_target + "/" + name)
            ).CreateFilteredPairsRel().AddTarget(b["screw"])
    # A single object-wide articulation root would prevent detachable parts being independent.
    for prim in list(stage.Traverse()):
        if not str(prim.GetPath()).startswith(tool_target + "/") and prim != tool:
            if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
                prim.RemoveAPI(UsdPhysics.ArticulationRootAPI)
    author_connections(stage, installed)
    stage.GetDefaultPrim().CreateAttribute(
        "envcreator:screwPaths", Sdf.ValueTypeNames.StringArray
    ).Set([b["screw"].rsplit("/", 1)[-1] for b in bindings])
    output.mkdir(parents=True)
    usd_path = output / "assembly.usda"
    stage.GetRootLayer().Export(str(usd_path))
    # Fail before delivery when references still point at removed exporter bodies.
    reopened = Usd.Stage.Open(str(usd_path))
    for prim in reopened.Traverse():
        for rel in prim.GetRelationships():
            for target in rel.GetTargets():
                if target.IsPrimPath() and not reopened.GetPrimAtPath(target):
                    raise ValueError(f"unresolved relationship: {prim.GetPath()} -> {target}")
    write_json(output / "compiled_assembly.json", installed)
    runtime = output / "runtime"
    runtime.mkdir()
    tools_root = Path(__file__).resolve().parents[1] / "tools"
    for record in installed["connections"][0]["components"]["runtime"]["source_files"]:
        name = Path(record["path"]).name
        shutil.copy2(tools_root / name, runtime / name)
    shutil.copy2(Path(__file__).with_name("observation.py"), runtime / "observation.py")
    shutil.copy2(tool_path, output / "tool.usda")
    write_json(
        output / "test_plan.json",
        {
            "protocol": installed["connections"][0]["components"]["protocol"],
            "status": "not_run",
            "scope": "independent ideal fixture protocol; scene/robot validation separate",
        },
    )
    report = {
        "schema": "envcreator.standard-bundle/1",
        "status": "installed_not_physically_verified",
        "source_usd": str(source),
        "source_sha256": sha256(source),
        "usd": str(usd_path),
        "usd_sha256": sha256(usd_path),
        "compiled_sha256": installed["compiled_sha256"],
        "removed_closure_joints": removed,
        "components": {
            "fastener_asset_sha256": sha256(fastener_path),
            "tool_asset_sha256": sha256(tool_path),
        },
        "physics_status": "not_run",
        "tool_mount": "free_unheld; independent bench adds declared carriage",
        "observation_permissions": "privileged geometry and mechanism state",
    }
    write_json(output / "installation.json", report)
    write_json(
        output / "manifest.json",
        {
            "schema": "envcreator.standard-bundle-manifest/1",
            "files_sha256": {
                str(p.relative_to(output)): sha256(p) for p in output.rglob("*") if p.is_file()
            },
        },
    )
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--compiled", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--component-root", type=Path)
    a = p.parse_args()
    print(
        json.dumps(
            install_bundle(
                a.source, read_json(a.compiled), a.output, component_root=a.component_root
            )
        )
    )


if __name__ == "__main__":
    main()
