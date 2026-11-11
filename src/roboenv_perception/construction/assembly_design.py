"""Shared Intake/assembly/selfcheck contract used by component and legacy Harness."""

from ..io import sha256, write_json
from .assembly_blueprint import AssemblyBlueprint, digest
from .contracts import DesignReview

ASSEMBLY_INSTRUCTION = """Author the new AssemblyBlueprint only, replacing the legacy blueprint format.
Embed the exact accepted asset_brief unchanged. Use part_definitions and part_instances;
instances may share a definition. parent is organizational, NOT a required mechanical joint tree.
Describe functional interfaces with part-local frames; unknown origin/rotation must be null,
not zero. Keep mates separate from kinematic joints and link each joint by mate_id.
A revolute joint does not prove shaft-hole geometry, friction, or a real hinge implementation.
Use hinge_region/contact_region for unresolved interfaces instead of invented hidden surfaces.
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
"""


def check_assembly(blueprint, *, brief=None, allowed_refs=None):
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
    return {
        "schema_version": "envcreator.assembly-selfcheck/1",
        "status": "needs_revision"
        if issues
        else "ready_with_assumptions"
        if assumptions
        else "ready",
        "issues": issues,
        "assumptions": assumptions,
        "description_sha256": digest(blueprint.model_dump(mode="json")),
        "scope": "description consistency only; not independent review or geometry validation",
        "geometry_status": "not_run",
        "physics_status": "not_run",
    }


def selfcheck_assembly(vlm, blueprint, images, output, *, stage, brief, allowed_refs, context=""):
    from .design import infer_model

    report = check_assembly(blueprint, brief=brief, allowed_refs=allowed_refs)
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
