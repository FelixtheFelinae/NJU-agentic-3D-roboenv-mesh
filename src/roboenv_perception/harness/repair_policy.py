"""Only explicitly attributed object/binding defects may trigger asset regeneration."""

from ..standard_interactions.catalog import StandardComponentError, digest

REPAIRABLE_DOMAINS = {"object_geometry", "instance_binding"}


def route_physics_failures(report):
    feedback, routes = {}, []
    for row in report.get("results", []):
        if row.get("status") != "failed":
            continue
        failed = [test for test in row.get("tests", []) if test.get("status") == "failed"]
        domains = {test.get("failure_domain", "unclassified") for test in failed}
        if not domains:
            domains = {row.get("failure_domain", "unclassified")}
        allowed = bool(domains) and domains <= REPAIRABLE_DOMAINS and bool(row.get("report"))
        routes.append(
            {
                "asset_id": row["asset_id"],
                "failure_domains": sorted(domains),
                "action": "repair_object_or_binding" if allowed else "stop_and_record_defect",
                "issues": row.get("issues", []),
            }
        )
        if allowed:
            feedback[row["asset_id"]] = row.get("issues", [])
    return feedback, routes


def standard_recipe_signature(recipe):
    """Freeze standard selection, intrinsic solids and mass; allow instance translation.

    Translation is a binding change. Relative solid geometry, dimensions and rotation
    stay locked, so a model cannot shrink a standard screw to repair a hole.
    """
    if not recipe.assembly:
        return {}
    result = {}
    for c in recipe.assembly.connections:
        if not c.standard_ref:
            continue
        solids = sorted(
            (s.model_dump(mode="json") for s in recipe.solids if s.part == c.fastener),
            key=lambda s: s["name"],
        )
        if not solids:
            raise ValueError("standard fastener has no geometry")
        origin = solids[0]["center_m"]
        for solid in solids:
            solid["center_m"] = [round(x - y, 12) for x, y in zip(solid["center_m"], origin)]
            solid.pop("evidence_refs", None)
            solid.pop("source", None)
        result[c.fastener] = digest(
            {
                "refs": [c.standard_ref, c.tool_ref, c.runtime_ref, c.protocol_ref],
                "solids": solids,
                "mass_kg": recipe.part_masses_kg.get(c.fastener),
            }
        )
    return result


def enforce_standard_recipe(recipe, frozen):
    current = standard_recipe_signature(recipe)
    if frozen and current != frozen:
        raise StandardComponentError(
            "asset repair changed frozen standard selection, shape or mass"
        )
    return current


def is_standard_defect(error):
    """Pydantic preserves the original domain error in validation context."""
    if isinstance(error, StandardComponentError):
        return True
    if hasattr(error, "errors"):
        return any(
            isinstance(item.get("ctx", {}).get("error"), StandardComponentError)
            for item in error.errors()
        )
    return False
