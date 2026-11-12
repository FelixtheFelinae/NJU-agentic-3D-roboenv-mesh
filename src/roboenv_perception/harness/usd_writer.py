"""OpenUSD-only worker. Author Blender meshes and resolved assembly data as Isaac USD."""

import json
import math
import sys
from pathlib import Path


def _mesh(stage, path, record):
    from pxr import Gf, UsdGeom

    points, normals, faces, face_normals = [], [], [], []
    with Path(record["path"]).open() as source:
        for line in source:
            fields = line.split()
            if not fields:
                continue
            if fields[0] in {"v", "vn"}:
                point = Gf.Vec3f(*(float(value) for value in fields[1:4]))
                (points if fields[0] == "v" else normals).append(point)
            elif fields[0] == "f":
                corners = [field.split("/") for field in fields[1:]]
                faces.append([int(corner[0]) - 1 for corner in corners])
                if all(len(corner) > 2 and corner[2] for corner in corners):
                    face_normals.extend(int(corner[2]) - 1 for corner in corners)
    if not points or not faces or any(i < 0 or i >= len(points) for face in faces for i in face):
        raise ValueError(f"invalid Blender mesh: {record['path']}")
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(points)
    mesh.CreateFaceVertexCountsAttr([len(face) for face in faces])
    mesh.CreateFaceVertexIndicesAttr([i for face in faces for i in face])
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateExtentAttr([Gf.Vec3f(*(op(point[i] for point in points) for i in range(3)))
                           for op in (min, max)])
    if len(face_normals) == sum(map(len, faces)):
        mesh.CreateNormalsAttr([normals[i] for i in face_normals])
        mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    return mesh


def _body(stage, path, position, mass, *, inertia=None):
    from pxr import Gf, UsdGeom, UsdPhysics

    xform = UsdGeom.Xform.Define(stage, path)
    xform.AddTranslateOp().Set(Gf.Vec3d(*position))
    prim = xform.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(prim)
    properties = UsdPhysics.MassAPI.Apply(prim)
    properties.CreateMassAttr(mass)
    if inertia is not None:
        properties.CreateDiagonalInertiaAttr(Gf.Vec3f(*inertia))
    return prim


def _bind(joint, parent, child, anchor=(0, 0, 0), axis=None, *, exclude=False):
    from pxr import Gf

    joint.CreateBody0Rel().SetTargets([parent])
    joint.CreateBody1Rel().SetTargets([child])
    joint.CreateLocalPos0Attr(Gf.Vec3f(*anchor))
    joint.CreateCollisionEnabledAttr(False)
    if exclude:
        joint.CreateExcludeFromArticulationAttr(True)
    if axis is not None:
        joint.CreateAxisAttr("Z")
        q = Gf.Rotation(Gf.Vec3d(0, 0, 1), Gf.Vec3d(*axis)).GetQuat()
        frame = Gf.Quatf(q.GetReal(), Gf.Vec3f(*q.GetImaginary()))
        joint.CreateLocalRot0Attr(frame)
        joint.CreateLocalRot1Attr(frame)
    return joint


def _mimic(follower, leader, coefficient):
    from pxr import Sdf

    # PhysX uses its single-DOF articulation axis token, as in the retained v9 author.
    follower.AddAppliedSchema("PhysxMimicJointAPI:rotX")
    follower.CreateRelationship("physxMimicJoint:rotX:referenceJoint").SetTargets([leader])
    follower.CreateAttribute("physxMimicJoint:rotX:gearing", Sdf.ValueTypeNames.Float).Set(coefficient)
    follower.CreateAttribute("physxMimicJoint:rotX:offset", Sdf.ValueTypeNames.Float).Set(0.0)


def _thread(stage, row, positions):
    from pxr import Sdf, UsdGeom, UsdPhysics

    scope = UsdGeom.Scope.Define(stage, "/World/Connections/" + row["mate_id"]).GetPrim()
    screw, support, cover = ("/World/Parts/" + row[key] for key in ("screw", "support", "cover"))
    initial = positions[row["screw"]]
    anchor = [a - b for a, b in zip(initial, positions[row["support"]], strict=True)]
    prefix = "/World/Guides/" + row["mate_id"]
    slider, rotor = prefix + "/Slider", prefix + "/Rotor"
    for path in (slider, rotor):
        guide = _body(stage, path, initial, .001, inertia=(1e-7, 1e-7, 1e-7))
        guide.CreateAttribute("envcreator:numericalGuide", Sdf.ValueTypeNames.Bool, custom=True).Set(True)
    UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath(support))
    slide = _bind(UsdPhysics.PrismaticJoint.Define(stage, prefix + "/Slide"), support, slider,
                  anchor, (0, 0, 1))
    hinge = _bind(UsdPhysics.RevoluteJoint.Define(stage, prefix + "/Hinge"), slider, rotor,
                  axis=(0, 0, 1))
    bound = 360 * max(2.0, row["turns_to_clear"] + 1)
    hinge.CreateLowerLimitAttr(0.0)
    hinge.CreateUpperLimitAttr(bound)
    slide.CreateLowerLimitAttr(0.0)
    slide.CreateUpperLimitAttr(bound * row["lead_m"] / 360)
    _mimic(slide.GetPrim(), hinge.GetPath(), -row["lead_m"] / 360)
    connector = _bind(UsdPhysics.FixedJoint.Define(stage, prefix + "/Connector"), rotor, screw,
                      exclude=True)
    drive = UsdPhysics.DriveAPI.Apply(hinge.GetPrim(), "angular")
    drive.CreateTypeAttr("force")
    drive.CreateStiffnessAttr(5.0)  # Numerical drive gains in USD angular units.
    drive.CreateDampingAttr(.01)
    drive.CreateTargetPositionAttr(0.0)
    drive.CreateTargetVelocityAttr(0.0)
    drive.CreateMaxForceAttr(row["breakaway_torque_Nm"])
    for key, target in {"screw": screw, "support": support, "cover": cover,
                        "connector": connector.GetPath(), "hinge": hinge.GetPath()}.items():
        scope.CreateRelationship(f"envcreator:{key}", custom=True).SetTargets([target])
    scope.CreateAttribute("envcreator:threadRule", Sdf.ValueTypeNames.String, custom=True).Set(
        json.dumps(dict(row, initial_axial_m=anchor[2], drive_stiffness_Nm_per_deg=5.0),
                   sort_keys=True, allow_nan=False)
    )


def write(request, destination):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateNew(str(destination))
    root = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    positions = {row["id"]: row["position_m"] for row in request["parts"]}
    paths = {name: "/World/Parts/" + name for name in positions}
    for part in request["parts"]:
        body = _body(stage, paths[part["id"]], part["position_m"], part["mass_kg"])
        body.CreateAttribute("envcreator:partId", Sdf.ValueTypeNames.String, custom=True).Set(part["id"])
    contact_offset = request.get("contact_offset_m")
    for record in request["meshes"]:
        prefix = paths[record["part"]]
        name = record["name"]
        if not record.get("collision_only"):
            visual = _mesh(stage, f"{prefix}/Visuals/{name}", record)
            visual.CreateDisplayColorPrimvar().Set([Gf.Vec3f(*record["rgba"][:3])])
            visual.CreateDisplayOpacityPrimvar().Set([record["rgba"][3]])
        if record.get("approximation"):
            collision = _mesh(stage, f"{prefix}/Colliders/{name}", record)
            collision.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
            UsdPhysics.CollisionAPI.Apply(collision.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(collision.GetPrim()).CreateApproximationAttr(
                record["approximation"]
            )
            if contact_offset is not None:
                collider = collision.GetPrim()
                collider.AddAppliedSchema("PhysxCollisionAPI")
                collider.CreateAttribute("physxCollision:contactOffset", Sdf.ValueTypeNames.Float).Set(contact_offset)
                collider.CreateAttribute("physxCollision:restOffset", Sdf.ValueTypeNames.Float).Set(0.0)
    types = {"fixed": UsdPhysics.FixedJoint, "revolute": UsdPhysics.RevoluteJoint,
             "continuous": UsdPhysics.RevoluteJoint, "prismatic": UsdPhysics.PrismaticJoint}
    joint_paths = {}
    if any(row["type"] != "fixed" for row in request["joints"]):
        UsdPhysics.ArticulationRootAPI.Apply(root)
    for row in request["joints"]:
        joint = types[row["type"]].Define(stage, "/World/Joints/" + row["name"])
        _bind(joint, paths[row["parent"]], paths[row["child"]], row["origin_parent_m"], row["axis"])
        if row.get("limits") is not None:
            convert = float if row["type"] == "prismatic" else math.degrees
            lo, hi = map(convert, row["limits"])
            joint.CreateLowerLimitAttr(lo)
            joint.CreateUpperLimitAttr(hi)
        joint_paths[row["name"]] = joint.GetPath()
    for row in request["couplings"]:
        _mimic(stage.GetPrimAtPath(joint_paths[row["driven_joint"]]),
               joint_paths[row["driver_joint"]], -row["ratio"])
    for row in request["threads"]:
        _thread(stage, row, positions)
    if request.get("ground"):
        UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
        ground = UsdGeom.Cube.Define(stage, "/World/Ground")
        ground.CreateSizeAttr(1.0)
        ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -.01))
        ground.AddScaleOp().Set(Gf.Vec3f(1, 1, .02))
        UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    stage.GetRootLayer().Save()


if __name__ == "__main__":
    write(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
