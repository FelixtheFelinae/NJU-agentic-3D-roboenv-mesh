"""Translate Harness hypotheses to the same compiler used by the v9 USD adapter."""

import math

from ..standard_interactions.catalog import StandardComponentError, resolve
from ..standard_interactions.compiler import compile_connections


def compile_plan(plan, parts, *, allowed_refs):
    if not plan or not any(c.standard_ref for c in plan.connections):
        return None
    if any(not c.standard_ref for c in plan.connections):
        raise ValueError("mixed legacy and standard connections require separate plans")
    by_part = {p["id"]: p for p in parts}
    bindings = []
    for c in plan.connections:
        if not set(c.evidence_refs) <= allowed_refs:
            raise ValueError("unknown standard selection evidence reference")
        if any(p not in by_part for p in [c.fastener, *c.stack, c.mate]):
            raise ValueError("missing standard connection member")
        if len(c.stack) != 1 or c.retention != "fixed_support":
            raise StandardComponentError(
                "candidate only supports one clamped member and anchored tapped support"
            )
        f = resolve("fasteners", c.standard_ref)
        tool = resolve("tools", c.tool_ref)
        if (
            c.tool_radius_m != tool["approach_envelope"]["radius_m"]
            or c.tool_length_m != tool["approach_envelope"]["length_m"]
        ):
            raise StandardComponentError("registered tool envelope mutated after validation")
        if c.pitch_m != f["lead_m"]:
            raise StandardComponentError("registered pitch mutated after validation")
        solids = {s["id"]: s for s in by_part[c.fastener]["solids"]}
        for name, radius, length in [
            (c.head_solid, f["head_radius_m"], f["head_height_m"]),
            (c.shaft_solid, f["shaft_radius_m"], f["shaft_length_m"]),
        ]:
            solid = solids.get(name, {})
            if (
                solid.get("shape") != "cylinder"
                or not math.isclose(solid.get("radius", 0), radius, abs_tol=1e-8)
                or not math.isclose(solid.get("length", 0), length, abs_tol=1e-8)
            ):
                raise StandardComponentError(
                    "standard fastener shape differs from registered dimensions"
                )
        head = solids[c.head_solid]
        head_face = [*head["center"][:2], head["center"][2] + head["length"] / 2]
        if math.dist(head_face, c.origin_m) > 1e-7:
            raise ValueError("connection origin does not match actual screw head face")
        for predecessor in c.access_predecessors:
            if predecessor not in by_part:
                raise ValueError("missing access predecessor")
        bindings.append(
            {
                "id": c.id,
                "standard_ref": c.standard_ref,
                "tool_ref": c.tool_ref,
                "runtime_ref": c.runtime_ref,
                "protocol_ref": c.protocol_ref,
                "screw": "/World/" + c.fastener,
                "support": "/World/" + c.mate,
                "clamped_member": "/World/" + c.stack[0],
                "tool": "/World/WristTool",
                "origin_m": c.origin_m,
                "axis": c.axis,
                "engagement_m": c.engagement_m,
                "removal_distance_m": c.removal_distance_m,
                "evidence_refs": c.evidence_refs,
                "assumptions": c.assumptions,
                "mate_kind": c.mate_kind,
            }
        )
    return compile_connections(bindings)
