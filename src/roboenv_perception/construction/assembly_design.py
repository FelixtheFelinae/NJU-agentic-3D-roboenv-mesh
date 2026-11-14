"""Shared Intake/assembly/selfcheck contract used by component and legacy Harness."""

import json

from pydantic import ValidationError

from ..io import sha256, write_json
from .assembly_blueprint import AssemblyBlueprint, digest
from .contracts import DesignReview
from .structure_prior import PRIOR_INSTRUCTION

ASSEMBLY_INSTRUCTION = """Author the new AssemblyBlueprint only, replacing the legacy blueprint format.
Embed the exact accepted asset_brief unchanged. Use part_definitions and part_instances;
instances may share a definition. parent is organizational, NOT a required mechanical joint tree.
The current construction adapter nevertheless requires each non-root parent to match
one explicit joint. When previous_readiness reports a conflict, inspect original images
and explicitly revise the grouping if justified; never add a fake joint to satisfy export.
Interface.kind uses the literal plane, NOT planar (planar is a constraint type).
For planar reference ONE plane; coplanar needs TWO planes. orthogonal/parallel needs TWO
explicit features: plane, axis or edge. For line-plane parallel, the line lies along the
plane (perpendicular to its normal); for line-plane orthogonal it follows the normal.
axis is an ideal kinematic line or a cylinder centerline, NOT a shaft/hole contact surface.
edge is an actual straight box edge, NOT its adjacent face normal. Use an axis interface
for the joint's ideal rotation axis and an edge interface for a visible rectangular border.
coaxial needs two lines with matching direction AND position, not just parallel normals.
Do not write orthogonal with just one top plane or only a part name. Preserve original
requirements and IDs when resolving hinge-axis/edge relations; never replace them with
helper-plane relations. Refining hinge_region to an axis does NOT resolve its contact mate.
For a rectangular base use TWO ADJACENT SIDE PLANES or TWO ADJACENT EDGES for orthogonality.
A border lying in the top plane is PARALLEL to that plane; it is NOT orthogonal to the plane.
axis_local is a direction/plane normal in the owning part coordinates, null when unknown.
hinge_region/contact_region are valid unresolved descriptions, not recovered shaft/hole
features. Preserve them when evidence is insufficient, even if construction is blocked.
Describe functional interfaces with part-local frames; unknown origin/rotation must be null,
not zero. Keep mates separate from kinematic joints and link each joint by mate_id.
A revolute joint does not prove shaft-hole geometry, friction, or a real hinge implementation.
Use hinge_region/contact_region when leaving an interface unresolved. Explicitly assumed
hidden surfaces are also allowed for a plausible construction candidate; do not label them observed.
Retain evidence records, hypotheses/alternatives, physics_intent and geometric_constraints.
For rectangular base/panel approximations, state intrinsic planar faces and perpendicular
adjacent edges as explicit assumed geometric constraints. These are requirements for the
later geometry validator, not constraints already solved or enforced by this description.
Only intrinsic object/assembly relations belong here. Support_mode=grounded does not mean
the object bottom lies on a world Z=0 plane; external stands and scene placement are separate.
Evidence origin=prior uses prior: IDs for explicit mechanical knowledge, not image observation. Other evidence
IDs must come from supplied views, measurements or external sources. Numeric quantities need
provenance; unknown quantities remain null. Do not hide guessed numbers in rationale text.
Frame convention: metres, Z up, front +Y; axis_child is in child-local coordinates.
coordinate_units=relative_width is allowed for description only; dimensions_m stay unknown.
Keep reference configuration separate from observed state and true motion limits. rest may
remain null for a description; do not use at_max just because the image shows an open lid.
States may describe configurations without numeric poses. Motion is observed only when intake
actually records it. Do not require invisible internals for approximate exterior/hinge tasks.
Selfcheck follows authoring; unknown dimensions or unresolved internal hardware are not by
themselves blocking description defects. No simulation or CAD result has been obtained here.
""" + "\n" + PRIOR_INSTRUCTION


def author_assembly(vlm, context, images, output, *, schema_revisions=0):
    """One optional correction of a failed schema response, retaining both traces."""
    from .design import infer_model

    if schema_revisions not in {0, 1}:
        raise ValueError("assembly schema repair budget must be zero or one")
    for attempt in range(schema_revisions + 1):
        trace = output / ("author" if attempt == 0 else "author-repair-1")
        try:
            return infer_model(
                vlm, "component-blueprint" if attempt == 0 else "component-blueprint-repair-1",
                ASSEMBLY_INSTRUCTION + "\nAuthor or REVISE the assembly description. "
                "Apply previous_findings explicitly; previous is a candidate to correct, "
                "not an authoritative answer to copy.\n" + json.dumps(context, ensure_ascii=False),
                images, AssemblyBlueprint, trace,
            )
        except ValidationError as error:
            write_json(trace / "rejected.json", {"status": "invalid_description", "error": str(error)[:8000]})
            if attempt == schema_revisions:
                raise
            context = {**context, "contract_feedback":
                       "Correct the actual schema error below. Keep the accepted intake, original "
                       "evidence, unknown quantities and mechanical requirements; do not fabricate "
                       "observed hidden geometry or delete difficult constraints. Explicit prior-based "
                       "hidden structure is allowed.\n" + str(error)[:8000]}


def check_assembly(blueprint, *, brief=None, allowed_refs=None, previous=None):
    """Deterministic description checks; never assert geometry/physical verification."""
    blueprint = AssemblyBlueprint.model_validate(blueprint.model_dump(mode="json"))
    issues, assumptions = [], []
    if brief is not None and blueprint.asset_brief != brief:
        issues.append("assembly changed accepted intake")
    priors = {e.id for e in blueprint.evidence if e.origin == "prior"}
    if allowed_refs is not None:
        for e in blueprint.evidence:
            if e.origin != "prior" and e.id not in allowed_refs:
                issues.append("unprovided evidence: " + e.id)

        def visit(v):
            if isinstance(v, dict):
                if v.get("source") == "measured" and set(v.get("evidence_refs", [])) & priors:
                    issues.append("mechanical prior cannot be a measurement")
                for child in v.values():
                    visit(child)
            elif isinstance(v, list):
                for child in v:
                    visit(child)

        visit(blueprint.model_dump(mode="json", exclude={"migration"}))
    if blueprint.assumptions or blueprint.unresolved or blueprint.migration:
        assumptions.append("description retains assumptions or unknowns")
    if any(
        i.local_frame.origin is None or i.local_frame.rotation_rpy is None
        for i in blueprint.interfaces
    ):
        assumptions.append("interface frames require geometry-stage placement")
    if any(
        j.travel.value is None or j.rest is None
        for j in blueprint.joints
        if j.type in {"revolute", "prismatic"}
    ):
        assumptions.append("joint reference convention or travel remains unresolved")
    if any(m.basis != "observed" for m in blueprint.mates):
        assumptions.append("mechanical relations include hypotheses")
    if any(
        q.value is None
        for p in blueprint.part_definitions
        for q in (p.dimensions_m.x, p.dimensions_m.y, p.dimensions_m.z, p.mass_kg)
    ):
        assumptions.append("part dimensions or mass are not fully known")
    if any(j.evidence_kind != "observed" for j in blueprint.joints) or any(
        q.source in {"unknown", "assumed", "estimated"}
        for p in blueprint.part_definitions
        for q in (p.dimensions_m.x, p.dimensions_m.y, p.dimensions_m.z, p.mass_kg)
    ):
        assumptions.append("geometry/physics contains estimates or assumptions")
    from .assembly_readiness import analyze_readiness, revision_findings

    evidence_issues = list(issues)
    changed = revision_findings(previous, blueprint)
    issues.extend(row["message"] for row in changed)
    readiness = analyze_readiness(blueprint)
    issues.extend(row["message"] for row in readiness["issues"] if row["code"] == "declared_direction_conflict")

    return {
        "schema_version": "envcreator.assembly-selfcheck/1",
        "status": "needs_revision"
        if issues
        else "ready_with_assumptions"
        if assumptions
        else "ready",
        "issues": issues,
        "evidence_issues": evidence_issues,
        "assumptions": assumptions,
        "description_sha256": digest(blueprint.model_dump(mode="json")),
        "scope": "description consistency only; not independent review or geometry validation",
        "geometry_status": "not_run",
        "physics_status": "not_run",
        "construction_readiness": readiness,
        "revision_findings": changed,
        "previous_description_sha256": digest(previous.model_dump(mode="json")) if previous is not None else None,
    }


def selfcheck_assembly(vlm, blueprint, images, output, *, stage, brief, allowed_refs, context="", previous=None):
    from .design import infer_model

    report = check_assembly(blueprint, brief=brief, allowed_refs=allowed_refs, previous=previous)
    path = output / "blueprint.json"
    write_json(path, blueprint.model_dump(mode="json"))
    file_digest = sha256(path)
    if report["issues"]:
        report["blueprint_sha256"] = file_digest
        write_json(output / "selfcheck.json", report)
        return report
    review = infer_model(
        vlm,
        stage,
        "Selfcheck this assembly description against original evidence. This is author selfcheck, "
        "not independent QA. REDO only for concrete structural/evidence contradictions. "
        "Unknown scale, frames or hidden internals may proceed with assumptions. Check mates, "
        "Unknown rest, travel, effort_limit and velocity_limit are valid description outputs; "
        "NEVER require export defaults, simulation readiness or invented values in this phase. "
        "joint/interface correspondence and reference versus observed states. Check that intrinsic "
        "constraints do not silently impose scene supports/world poses, and distinguish declared "
        "geometric requirements from actually enforced or verified geometry. "
        "PASS requires findings=[]. Echo the exact blueprint_sha256: "
        + file_digest
        + "\n"
        + context
        + "\n"
        + blueprint.model_dump_json(),
        images,
        DesignReview,
        output / "self_review",
    )
    if review.blueprint_sha256 != file_digest:
        raise ValueError("selfcheck does not bind the current blueprint")
    if review.decision == "REDO":
        report.update(status="needs_revision", issues=review.findings)
    report.update(blueprint_sha256=file_digest, model_review=review.model_dump(mode="json"))
    write_json(output / "selfcheck.json", report)
    return report
