"""Author the frozen +Z native helical constraints onto existing object geometry."""

import json
import math

from .compiler import verify_compiled


def author_connections(stage, compiled):
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics

    verify_compiled(compiled)
    if UsdGeom.GetStageUpAxis(stage) != "Z" or not math.isclose(
        UsdGeom.GetStageMetersPerUnit(stage), 1.0
    ):
        raise ValueError("candidate requires a metre-scale Z-up stage")
    # Validate all target bodies before making any changes.
    for row in compiled["connections"]:
        b = row["binding"]
        for path in (b["screw"], b["support"], b["clamped_member"]):
            p = stage.GetPrimAtPath(path)
            if not p or not p.HasAPI(UsdPhysics.RigidBodyAPI):
                raise ValueError(f"connection target is not a rigid body: {path}")
        transform = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(b["support"]))
        if not Gf.IsClose(transform.ExtractRotationMatrix(), Gf.Matrix3d(1.0), 1e-8):
            raise ValueError(
                "candidate requires unrotated unscaled support; translation is supported"
            )
    for row in compiled["connections"]:
        b, spec = row["binding"], dict(row["fastener_spec"])
        paths = row["paths"]
        runtime = row["components"]["runtime"]
        u = runtime["usd_authoring"]
        carrier = runtime["numerical_carrier"]
        origin_local = Gf.Vec3d(*b["origin_m"])
        transform = UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(b["support"]))
        origin = transform.Transform(origin_local)
        spec["origin_world_m"] = list(origin)
        screw = stage.GetPrimAtPath(b["screw"])
        support = stage.GetPrimAtPath(b["support"])
        UsdPhysics.ArticulationRootAPI.Apply(support)
        support.AddAppliedSchema("PhysxArticulationAPI")
        for name, value, kind in [
            (
                "solverPositionIterationCount",
                u["articulation_position_iterations"],
                Sdf.ValueTypeNames.Int,
            ),
            (
                "solverVelocityIterationCount",
                u["articulation_velocity_iterations"],
                Sdf.ValueTypeNames.Int,
            ),
            ("sleepThreshold", 0.0, Sdf.ValueTypeNames.Float),
            ("stabilizationThreshold", 0.0, Sdf.ValueTypeNames.Float),
        ]:
            support.CreateAttribute("physxArticulation:" + name, kind).Set(value)
        for key in (
            "guide_joint",
            "lead_joint",
            "helper_body",
            "rotor_body",
            "friction_joint",
            "hinge_joint",
        ):
            stage.RemovePrim(paths[key])
        for key in ("helper_body", "rotor_body"):
            body = UsdGeom.Xform.Define(stage, paths[key])
            body.AddTranslateOp().Set(origin)
            UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
            mass = UsdPhysics.MassAPI.Apply(body.GetPrim())
            mass.CreateMassAttr(carrier["mass_kg"])
            mass.CreateDiagonalInertiaAttr(Gf.Vec3f(carrier["diagonal_inertia_kg_m2"]))
            mass.CreateCenterOfMassAttr(Gf.Vec3f(0))
        slide = UsdPhysics.PrismaticJoint.Define(stage, paths["lead_joint"])
        slide.CreateBody0Rel().SetTargets([b["support"]])
        slide.CreateBody1Rel().SetTargets([paths["helper_body"]])
        slide.CreateAxisAttr("Z")
        slide.CreateLocalPos0Attr(Gf.Vec3f(*origin_local))
        connector = UsdPhysics.FixedJoint.Define(stage, paths["guide_joint"])
        connector.CreateBody0Rel().SetTargets([paths["rotor_body"]])
        connector.CreateBody1Rel().SetTargets([b["screw"]])
        connector.CreateExcludeFromArticulationAttr(True)
        connector.CreateCollisionEnabledAttr(False)
        hinge = UsdPhysics.RevoluteJoint.Define(stage, paths["hinge_joint"])
        hinge.CreateBody0Rel().SetTargets([paths["helper_body"]])
        hinge.CreateBody1Rel().SetTargets([paths["rotor_body"]])
        hinge.CreateAxisAttr("Z")
        hinge.CreateLowerLimitAttr(-u["angular_limit_deg"])
        hinge.CreateUpperLimitAttr(u["angular_limit_deg"])
        drive = UsdPhysics.DriveAPI.Apply(hinge.GetPrim(), "angular")
        drive.CreateTypeAttr("force")
        drive.CreateStiffnessAttr(0.0)
        drive.CreateDampingAttr(u["hinge_damping_Nm_s_rad"] * math.pi / 180)
        drive.CreateMaxForceAttr(0.0)
        drive.CreateTargetVelocityAttr(0.0)
        friction = UsdPhysics.Joint.Define(stage, paths["friction_joint"])
        friction.CreateBody0Rel().SetTargets([b["support"]])
        friction.CreateBody1Rel().SetTargets([b["screw"]])
        friction.CreateExcludeFromArticulationAttr(True)
        frame = Gf.Quatf(math.sqrt(0.5), Gf.Vec3f(0, -math.sqrt(0.5), 0))
        friction.CreateLocalRot0Attr(frame)
        friction.CreateLocalRot1Attr(frame)
        drive = UsdPhysics.DriveAPI.Apply(friction.GetPrim(), "rotX")
        drive.CreateTypeAttr("force")
        drive.CreateStiffnessAttr(u["friction_drive_stiffness_Nm_rad"] * math.pi / 180)
        drive.CreateDampingAttr(u["friction_drive_damping_Nm_s_rad"] * math.pi / 180)
        drive.CreateMaxForceAttr(u["friction_initial_limit_Nm"])
        drive.CreateTargetVelocityAttr(0.0)
        slide.GetPrim().AddAppliedSchema("PhysxMimicJointAPI:rotX")
        slide.GetPrim().CreateRelationship("physxMimicJoint:rotX:referenceJoint").SetTargets(
            [hinge.GetPath()]
        )
        slide.GetPrim().CreateAttribute(
            "physxMimicJoint:rotX:gearing", Sdf.ValueTypeNames.Float
        ).Set(-spec["lead_m"] / 360)
        slide.GetPrim().CreateAttribute(
            "physxMimicJoint:rotX:offset", Sdf.ValueTypeNames.Float
        ).Set(0.0)
        old = screw.GetAttribute("envcreator:fastenerSpec")
        metadata = json.loads(old.Get()) if old and old.Get() else {}
        # Preserve provenance/mount metadata, replace every compiled mechanics field.
        metadata.update(spec)
        metadata["standard_lock"] = {
            "catalog_sha256": compiled["catalog_sha256"],
            "compiled_sha256": compiled["compiled_sha256"],
            "components_sha256": row["components_sha256"],
            "binding": b,
        }
        screw.CreateAttribute("envcreator:fastenerSpec", Sdf.ValueTypeNames.String).Set(
            json.dumps(metadata)
        )
        for path in (
            b["support"],
            b["screw"],
            b["clamped_member"],
            paths["helper_body"],
            paths["rotor_body"],
        ):
            p = stage.GetPrimAtPath(path)
            p.CreateAttribute(
                "physxRigidBody:solverPositionIterationCount", Sdf.ValueTypeNames.Int
            ).Set(u["rigid_position_iterations"])
            p.CreateAttribute(
                "physxRigidBody:solverVelocityIterationCount", Sdf.ValueTypeNames.Int
            ).Set(u["rigid_velocity_iterations"])
