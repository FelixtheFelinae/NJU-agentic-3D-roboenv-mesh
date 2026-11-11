"""USD-only export and runtime motor adapter. Does not require EnvCreator imports."""

import argparse
import hashlib
import json
import math
from pathlib import Path


def set_motor(stage, tool_root: str, rpm: float = 30.0, *, enabled: bool = True):
    """Command the joint drive only; never writes body pose, joint state, or a screw."""
    from pxr import UsdPhysics

    if type(enabled) is not bool or isinstance(rpm, bool) or not math.isfinite(rpm):
        raise ValueError("motor needs a boolean enable and a finite RPM")
    root = stage.GetPrimAtPath(tool_root)
    if not root:
        raise ValueError("tool root does not exist")
    spec = json.loads(root.GetAttribute("envcreator:toolSpec").Get())
    motor = spec["motor"]
    if abs(rpm) > motor["max_command_rpm"]:
        raise ValueError("motor speed exceeds the declared limit")
    joint = stage.GetPrimAtPath(tool_root + spec["usd_paths"]["joint"])
    if not joint or not joint.IsA(UsdPhysics.RevoluteJoint):
        raise ValueError("motor joint is absent or not revolute")
    drive = UsdPhysics.DriveAPI(joint, "angular")
    drive.GetTargetVelocityAttr().Set(float(rpm) * 6.0 if enabled else 0.0)
    drive.GetMaxForceAttr().Set(motor["torque_limit_Nm"] if enabled else 0.0)
    drive.GetDampingAttr().Set(motor["usd_velocity_damping"] if enabled else 0.0)
    return {"enabled": enabled, "command_rpm": float(rpm) if enabled else 0.0,
            "target_degrees_per_second": float(rpm) * 6.0 if enabled else 0.0}


def export_tool(source: Path, spec_path: Path, output: Path):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    output.mkdir(parents=True, exist_ok=False)
    destination = output / "electric_screwdriver.usda"
    original = Usd.Stage.Open(str(source))
    original.Flatten().Export(str(destination))
    stage = Usd.Stage.Open(str(destination))
    root = stage.GetDefaultPrim()
    root_path = str(root.GetPath())
    spec = json.loads(spec_path.read_text())
    bodies = {p.GetName(): p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)}
    if set(bodies) != {"housing", "spindle"}:
        raise ValueError("tool must contain exactly housing and spindle rigid bodies")
    joints = [p for p in stage.Traverse() if p.IsA(UsdPhysics.RevoluteJoint)]
    if len(joints) != 1:
        raise ValueError("tool must have one continuous rotary joint")
    joint = UsdPhysics.RevoluteJoint(joints[0])
    axis = {"X": Gf.Vec3f(1, 0, 0), "Y": Gf.Vec3f(0, 1, 0), "Z": Gf.Vec3f(0, 0, 1)}[joint.GetAxisAttr().Get()]
    for rotation in (joint.GetLocalRot0Attr().Get(), joint.GetLocalRot1Attr().Get()):
        if (rotation.Transform(axis) - Gf.Vec3f(0, 1, 0)).GetLength() > 1e-5:
            raise ValueError("spindle effective axis must be +Y in both body frames")
    for attr in (joint.GetLowerLimitAttr(), joint.GetUpperLimitAttr()):
        attr.Clear()
    # The reusable tool has no world fixture. A robot can hold its free housing.
    fixed = [p for p in stage.Traverse() if p.IsA(UsdPhysics.FixedJoint)]
    if fixed:
        raise ValueError("unexpected world fixture or extra fixed joint")
    for p in stage.Traverse():
        if p.HasAPI(UsdPhysics.ArticulationRootAPI):
            p.RemoveAPI(UsdPhysics.ArticulationRootAPI)
    UsdPhysics.ArticulationRootAPI.Apply(root)
    for name, center, inertia in [
        ("housing", (0, -.012, 0), (.00075, .000054, .00075)),
        ("spindle", (0, .015, 0), (.0000071, .00000036, .0000071)),
    ]:
        mass = UsdPhysics.MassAPI.Apply(bodies[name])
        mass.CreateMassAttr(.300 if name == "housing" else .020)
        mass.CreateCenterOfMassAttr(Gf.Vec3f(*center))
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(*inertia))
        mass.CreatePrincipalAxesAttr(Gf.Quatf(1))

    materials = {}
    for name, values in {"housing": (.6, .5), "grip": (.9, .7), "steel": (.4, .3)}.items():
        mat = UsdShade.Material.Define(stage, root_path + "/PhysicsMaterials/" + name)
        props = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
        props.CreateStaticFrictionAttr(values[0])
        props.CreateDynamicFrictionAttr(values[1])
        props.CreateRestitutionAttr(0.0)
        materials[name] = mat
    # Artisan has already decomposed concave solids into separate convex col_N
    # meshes. Preserve these pieces, particularly those surrounding the nose bore.
    collider_count = 0
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            mesh.CreateSubdivisionSchemeAttr("none")
            if prim.GetName().startswith("vis_"):
                mesh.CreatePurposeAttr("default")
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        collider_count += 1
        name = str(prim.GetPath())
        kind = "steel" if "/spindle/" in name else "housing"
        if prim.IsA(UsdGeom.Mesh):
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr("convexHull")
            points = UsdGeom.Mesh(prim).GetPointsAttr().Get()
            lo = [min(v[i] for v in points) for i in range(3)]
            hi = [max(v[i] for v in points) for i in range(3)]
            # This preset's only long lateral convex pieces are its grip pads.
            if "/housing/" in name and hi[1] - lo[1] > .08 and (lo[0] > .012 or hi[0] < -.012):
                kind = "grip"
            UsdGeom.Imageable(prim).CreateVisibilityAttr("invisible")
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(materials[kind], materialPurpose="physics")
        prim.AddAppliedSchema("PhysxCollisionAPI")
        prim.CreateAttribute("physxCollision:contactOffset", Sdf.ValueTypeNames.Float).Set(.0002)
        prim.CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(0.0)
    if collider_count == 0:
        raise ValueError("tool has no collision geometry")
    # Adjacent links have mechanical bearings; their own collision is suppressed,
    # but both retain collision against fingers, fixtures and screws.
    joint.CreateCollisionEnabledAttr(False)
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular")
    drive.CreateTypeAttr("force")
    drive.CreateStiffnessAttr(0.0)
    drive.CreateDampingAttr(0.0)
    drive.CreateTargetVelocityAttr(0.0)
    drive.CreateMaxForceAttr(0.0)
    spec["usd_paths"] = {"housing": str(bodies["housing"].GetPath())[len(root_path):],
                         "spindle": str(bodies["spindle"].GetPath())[len(root_path):],
                         "joint": str(joint.GetPath())[len(root_path):]}
    spec["motor"]["simulation_joint_name"] = joint.GetPrim().GetName()
    for name, frame in spec["frames"].items():
        path = str(bodies[frame["body"]].GetPath()) + "/" + name + "_frame"
        UsdGeom.Xform.Define(stage, path).AddTranslateOp().Set(Gf.Vec3d(*frame["position_m"]))
        spec["usd_paths"][name] = path[len(root_path):]
    spec["inertia_source"] = "assumed equivalent cylinder; no reflected gearbox inertia"
    spec["collision_count"] = collider_count
    root.CreateAttribute("envcreator:toolSpec", Sdf.ValueTypeNames.String).Set(json.dumps(spec, sort_keys=True))
    stage.GetRootLayer().Save()
    manifest = {**spec, "default_prim": root_path, "asset": destination.name,
                "asset_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    (output / "tool.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    export_tool(args.source, args.spec, args.output)
