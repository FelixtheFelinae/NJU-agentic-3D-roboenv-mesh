"""Deterministic checks on builder primitive features in the assembled reference pose.

This verifies the declared analytic recipe, not an exported mesh or physical function.
Unknown/unsupported surfaces are unresolved; never certify them by their textual labels.
"""

import math
from pathlib import Path

import numpy as np
from pydantic import Field

from ..construction.assembly_blueprint import AssemblyBlueprint, digest
from ..contracts import Contract
from ..io import write_json


class VerificationPolicy(Contract):
    # Numerical acceptance policy, not inferred manufacturing tolerances.
    axis_tolerance_m: float = Field(default=1e-4, gt=0)
    angular_tolerance_rad: float = Field(default=1e-3, gt=0, lt=math.pi / 2)
    minimum_axial_overlap_m: float = Field(default=1e-5, gt=0)


class GeometryConstraintError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(
            "geometric verification "
            + report["status"]
            + ": "
            + "; ".join(f"{r['requirement_id']}: {r['message']}" for r in report["feedback"])
        )


def rotation(rpy):
    x, y, z = rpy
    cx, cy, cz, sx, sy, sz = (
        math.cos(x),
        math.cos(y),
        math.cos(z),
        math.sin(x),
        math.sin(y),
        math.sin(z),
    )
    return np.array(
        [
            [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx],
        ]
    )


def _offsets(blueprint, recipe):
    """Match the current builder: part-local solids + translation-only rest tree.

    Organizational parents cannot stand in for a mechanical placement. Graphs not
    supported by the current builder are explicitly unresolved here.
    """
    parts = {p.name: p for p in blueprint.part_instances}
    incoming = {}
    placements = {p.joint: np.array(p.origin_parent_m) for p in recipe.joint_placements}
    for j in blueprint.joints:
        if j.child in incoming or j.child == j.parent or j.name not in placements:
            raise ValueError("reference pose requires one incoming joint and placement per child")
        if parts[j.child].parent != j.parent:
            raise ValueError("organizational parent differs from builder joint parent")
        incoming[j.child] = (j.parent, placements[j.name])
    result = {}

    def place(name, seen):
        if name in seen:
            raise ValueError("reference placement contains a joint cycle")
        if name not in result:
            parent = parts[name].parent
            if parent is None:
                if name in incoming:
                    raise ValueError("root has an incoming joint")
                result[name] = np.zeros(3)
            elif name not in incoming:
                raise ValueError("reference pose lacks an explicit mechanical placement")
            else:
                result[name] = place(parent, seen | {name}) + incoming[name][1]
        return result[name]

    for name in parts:
        place(name, set())
    return result


def _feature(interface, binding, solids, offsets):
    solid = solids[binding.solid]
    matrix = rotation(solid.rotation_rpy)
    origin = offsets[solid.part] + np.array(solid.center_m)
    feature = binding.feature
    if feature in {"cylinder_outer", "cylinder_inner"}:
        if solid.shape not in {"cylinder", "annulus"}:
            raise ValueError("cylindrical feature requires cylinder or through-bored annulus")
        if feature == "cylinder_inner" and solid.shape != "annulus":
            raise ValueError("inner cylinder requires an actual through-bored annulus")
        if interface.kind not in {"shaft", "hole", "cylinder"}:
            raise ValueError(
                "candidate or noncylindrical interface is not a verified cylindrical surface"
            )
        if interface.kind == "shaft" and feature != "cylinder_outer":
            raise ValueError("shaft must bind the outside cylindrical surface")
        if interface.kind == "hole" and feature != "cylinder_inner":
            raise ValueError("hole must bind the inside cylindrical surface")
        return {
            "kind": feature,
            "origin": origin,
            "axis": matrix[:, 2],
            "radius": solid.inner_radius_m if feature == "cylinder_inner" else solid.radius_m,
            "length": solid.length_m,
            "part": solid.part,
            "solid": solid.name,
        }
    if solid.shape != "box" or interface.kind != "plane":
        raise ValueError("plane binding currently requires an explicit box face")
    _, component, side = feature.split("_")
    index = "xyz".index(component)
    normal = matrix[:, index] * (-1 if side == "min" else 1)
    return {
        "kind": "plane",
        "origin": origin + normal * solid.size_m[index] / 2,
        "axis": normal,
        "part": solid.part,
        "solid": solid.name,
    }


def _parameter(requirement, key, unit, default=None):
    parameter = requirement.parameters.get(key)
    if parameter is None:
        return default
    if parameter.unit != unit or parameter.quantity.value is None:
        raise ValueError(f"{key} needs a known {unit} parameter")
    return parameter.quantity.value


def verify_geometry(blueprint, recipe, *, policy=None):
    """Return hash-bound findings; no writes or model/simulator calls."""
    policy = policy or VerificationPolicy()
    if not isinstance(blueprint, AssemblyBlueprint):
        return {
            "schema_version": "envcreator.geometric-verification/1",
            "status": "not_applicable",
            "checks": [],
            "feedback": [],
            "physics_status": "not_run",
        }
    requirements = [("mate", m) for m in blueprint.mates] + [
        ("constraint", c) for c in blueprint.geometric_constraints
    ]
    rows, features, feature_errors = [], {}, {}
    interfaces = {i.id: i for i in blueprint.interfaces}
    # Reuse the recipe identity, ownership and provenance checks even in direct CLI use.
    refs = {ref for s in recipe.solids for ref in s.evidence_refs} | {
        ref for p in recipe.joint_placements for ref in p.evidence_refs
    }
    binding_error = None
    try:
        recipe.check_against(blueprint, refs)
        if blueprint.coordinate_units != "m":
            raise ValueError("metric feature verification requires resolved metric coordinates")
        offsets = _offsets(blueprint, recipe)
        solids = {s.name: s for s in recipe.solids}
        for binding in recipe.interface_bindings:
            try:
                features[binding.interface_id] = _feature(
                    interfaces[binding.interface_id], binding, solids, offsets
                )
            except ValueError as error:
                feature_errors[binding.interface_id] = str(error)
    except ValueError as error:
        binding_error = str(error)

    def check(source, req):
        row = {
            "source": source,
            "requirement_id": req.id,
            "type": req.type,
            "status": "unresolved",
            "metrics": {},
            "message": "",
            "repair_target": "geometry",
            "failure_domain": "object_geometry",
        }
        ids = list(req.interfaces if source == "mate" else req.interface_refs)
        if binding_error:
            row.update(message=binding_error)
            return row
        if (
            not ids
            or (req.type != "planar" and len(ids) != 2)
            or (req.type == "planar" and len(ids) != 1)
        ):
            row.update(
                message="requirement must identify concrete interface surfaces",
                repair_target="blueprint",
                failure_domain="assembly_description",
            )
            return row
        if len(set(ids)) != len(ids):
            row.update(
                message="requirement repeats the same interface",
                repair_target="blueprint",
                failure_domain="assembly_description",
            )
            return row
        if any(i not in features for i in ids):
            row["message"] = "; ".join(
                f"{i}: {feature_errors.get(i, 'missing actual-solid feature binding')}"
                for i in ids
                if i not in features
            )
            if any(interfaces[i].kind in {"hinge_region", "contact_region"} for i in ids):
                row.update(repair_target="blueprint", failure_domain="assembly_description")
            return row
        fs = [features[i] for i in ids]
        angle_limit = _parameter(req, "angular_tolerance", "rad", policy.angular_tolerance_rad)
        offset_limit = _parameter(req, "axis_tolerance", "m", policy.axis_tolerance_m)
        if not 0 < angle_limit < math.pi / 2 or offset_limit <= 0:
            raise ValueError("positive axis tolerance and angular tolerance < pi/2 required")
        if req.type == "planar":
            if fs[0]["kind"] != "plane":
                raise ValueError("planarity check needs a bound plane face")
            row.update(status="passed", message="analytic box face is planar by construction")
            return row
        a, b = fs
        cosine = float(np.clip(abs(np.dot(a["axis"], b["axis"])), 0, 1))
        angle = math.acos(cosine)
        row["metrics"]["axis_angle_rad"] = angle
        if req.type in {"orthogonal", "parallel", "coplanar"}:
            if any(f["kind"] != "plane" for f in fs):
                raise ValueError("plane relation requires two explicit plane faces")
            residual = abs(math.pi / 2 - angle) if req.type == "orthogonal" else angle
            ok = residual <= angle_limit
            row["metrics"]["angular_residual_rad"] = residual
            if req.type == "coplanar":
                distance = abs(float(np.dot(a["origin"] - b["origin"], a["axis"])))
                row["metrics"]["plane_offset_m"] = distance
                ok = ok and distance <= offset_limit
            row.update(
                status="passed" if ok else "failed",
                message="analytic plane relation " + ("satisfied" if ok else "violated"),
            )
            return row
        if req.type not in {"coaxial", "clearance", "revolute", "cylindrical"}:
            row.update(
                message="relation has no geometric verifier in this version",
                repair_target="verifier",
                failure_domain="unsupported_geometry",
            )
            return row
        if any(f["kind"] not in {"cylinder_inner", "cylinder_outer"} for f in fs):
            raise ValueError("axis/clearance check requires actual cylindrical surfaces")
        endpoints = [a["origin"] + sign * a["axis"] * a["length"] / 2 for sign in (-1, 1)]
        radial = max(
            float(
                np.linalg.norm((p - b["origin"]) - np.dot(p - b["origin"], b["axis"]) * b["axis"])
            )
            for p in endpoints
        )
        row["metrics"]["axis_offset_over_feature_m"] = radial
        ok = angle <= angle_limit and radial <= offset_limit
        if req.type == "coaxial":
            row.update(
                status="passed" if ok else "failed",
                message="finite-feature axis alignment " + ("satisfied" if ok else "violated"),
            )
            return row
        if {a["kind"], b["kind"]} != {"cylinder_inner", "cylinder_outer"}:
            raise ValueError(
                "fit needs one actual shaft surface and one actual through-hole surface"
            )
        hole, shaft = (a, b) if a["kind"] == "cylinder_inner" else (b, a)
        if hole["part"] == shaft["part"]:
            raise ValueError("fit must relate two distinct part instances")
        delta = shaft["origin"] - hole["origin"]
        center_z = float(np.dot(delta, hole["axis"]))
        projection = float(np.dot(shaft["axis"], hole["axis"]))
        low = max(-hole["length"] / 2, center_z - shaft["length"] * abs(projection) / 2)
        high = min(hole["length"] / 2, center_z + shaft["length"] * abs(projection) / 2)
        overlap = max(0.0, high - low)
        # Evaluate eccentricity over the finite overlapping bore. A tilted circular
        # shaft has an elliptical section; r/cos(theta) is a conservative bound.
        if abs(projection) > 1e-12 and overlap > 0:
            points = [delta + shaft["axis"] * ((z - center_z) / projection) for z in (low, high)]
            eccentricity = max(
                float(np.linalg.norm(p - np.dot(p, hole["axis"]) * hole["axis"])) for p in points
            )
            clearance = hole["radius"] - shaft["radius"] / abs(projection) - eccentricity
        else:
            eccentricity, clearance = None, None
        minimum = _parameter(req, "minimum_radial_clearance", "m", 0.0)
        maximum = _parameter(req, "maximum_radial_clearance", "m")
        overlap_min = _parameter(req, "minimum_axial_overlap", "m", policy.minimum_axial_overlap_m)
        if minimum < 0 or overlap_min <= 0 or (maximum is not None and maximum < minimum):
            raise ValueError("invalid radial clearance or overlap requirement")
        nominal = hole["radius"] - shaft["radius"]
        row["metrics"].update(
            nominal_radial_clearance_m=nominal,
            minimum_radial_clearance_m=clearance,
            eccentricity_over_overlap_m=eccentricity,
            axial_overlap_m=overlap,
        )
        ok = (
            angle <= angle_limit
            and overlap >= overlap_min
            and clearance is not None
            and clearance >= minimum - 1e-12
            and (maximum is None or nominal <= maximum + 1e-12)
        )
        row.update(
            status="passed" if ok else "failed",
            message="finite shaft-hole fit " + ("satisfied" if ok else "violated"),
        )
        return row

    for source, req in requirements:
        try:
            rows.append(check(source, req))
        except ValueError as error:
            rows.append(
                {
                    "source": source,
                    "requirement_id": req.id,
                    "type": req.type,
                    "status": "unresolved",
                    "metrics": {},
                    "message": str(error),
                    "repair_target": "blueprint",
                    "failure_domain": "assembly_description",
                }
            )
    status = (
        "failed"
        if any(r["status"] == "failed" for r in rows)
        else "unresolved"
        if any(r["status"] == "unresolved" for r in rows)
        else "passed"
        if rows
        else "not_applicable"
    )
    report = {
        "schema_version": "envcreator.geometric-verification/1",
        "status": status,
        "object_id": blueprint.object_id,
        "checks": rows,
        "feedback": [r for r in rows if r["status"] != "passed"],
        "input_sha256": {
            "blueprint": digest(blueprint.model_dump(mode="json")),
            "recipe": digest(recipe.model_dump(mode="json")),
        },
        "policy": policy.model_dump(mode="json"),
        "scope": "analytic builder recipe, finite features, assembled reference pose only",
        "exported_mesh_status": "not_run",
        "physics_status": "not_run",
    }
    report["report_sha256"] = digest(report)
    return report


def persist_verification(output, blueprint, recipe, *, require_pass=False, policy=None):
    report = verify_geometry(blueprint, recipe, policy=policy)
    write_json(Path(output) / "geometric_verification.json", report)
    if require_pass and report["status"] not in {"passed", "not_applicable"}:
        raise GeometryConstraintError(report)
    return report


def bound_feedback(report, blueprint, recipe=None):
    """Do not repair a candidate from an altered or unrelated verifier result."""
    content = {k: v for k, v in report.items() if k != "report_sha256"}
    if report.get("schema_version") != "envcreator.geometric-verification/1" or report.get(
        "report_sha256"
    ) != digest(content):
        raise ValueError("geometric feedback report is missing or altered")
    expected = report.get("input_sha256", {})
    if expected.get("blueprint") != digest(blueprint.model_dump(mode="json")):
        raise ValueError("geometric feedback belongs to a different blueprint")
    if recipe is not None and expected.get("recipe") != digest(recipe.model_dump(mode="json")):
        raise ValueError("geometric feedback belongs to a different recipe")
    return report["feedback"]


def main(argv=None):
    import argparse
    import json

    from ..construction.assembly_blueprint import parse_blueprint
    from ..io import read_json
    from .geometry import GeometryRecipe

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    bp = parse_blueprint(read_json(args.blueprint))
    recipe = GeometryRecipe.model_validate(read_json(args.recipe))
    report = persist_verification(args.output, bp, recipe)
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": len(report["checks"]),
                "output": str(args.output.resolve()),
            }
        )
    )
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
