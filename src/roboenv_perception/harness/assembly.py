"""Evidence-labelled fastener hypotheses and geometry checks, independent of rendering.

The initial analytic checker supports straight +Z withdrawal, box plates and
Z-aligned cylinders/annuli. Unsupported intersecting geometry fails closed.
It does not certify preload, strength, actual hardware, or disassembly dynamics.
"""

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Contract
from ..io import write_json

ASSEMBLY_INTENT = re.compile(
    r"fasten|screw|bolt|disassembl|assembly.aware|紧固|螺丝|螺钉|螺栓|拆装|拆卸", re.I
)
FASTENER_NAME = re.compile(r"screw|bolt|fastener|螺丝|螺钉|螺栓", re.I)


class ConnectionHypothesis(Contract):
    id: str = Field(min_length=1)
    fastener: str
    head_solid: str
    shaft_solid: str
    # Ordered from the head towards the mating thread, including optional washers.
    stack: list[str] = Field(min_length=1)
    mate: str
    mate_kind: Literal["through_nut", "tapped_support"]
    retention: Literal["counterhold_tool", "captive", "fixed_support", "unresolved"]
    retention_basis: str = Field(min_length=1)
    axis: tuple[float, float, float] = (0, 0, 1)
    evidence_refs: list[str] = Field(min_length=1)
    observed: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(min_length=1)
    alternatives: list[str] = Field(min_length=1)
    selection_reason: str = Field(min_length=1)
    unknowns: list[str] = Field(default_factory=list)
    access_predecessors: list[str] = Field(default_factory=list)
    # These parameters are priors; no "measured" flag can be supplied by a model.
    pitch_m: float = Field(gt=0, le=0.01, allow_inf_nan=False)
    engagement_m: float = Field(gt=0, le=0.1, allow_inf_nan=False)
    tool_radius_m: float = Field(default=0.003, gt=0, le=0.1, allow_inf_nan=False)
    tool_length_m: float = Field(default=0.06, gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def distinct_members(self):
        members = [self.fastener, *self.stack, self.mate]
        if len(members) != len(set(members)):
            raise ValueError("fastener, ordered stack and mate must be distinct parts")
        if not all(math.isfinite(x) for x in self.axis):
            raise ValueError("axis must be finite")
        return self


class AssemblyPlan(Contract):
    schema_version: Literal["assembly-hypotheses/1"] = "assembly-hypotheses/1"
    connections: list[ConnectionHypothesis] = Field(min_length=1, max_length=64)
    unresolved_relations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        for field in ("id", "fastener"):
            values = [getattr(c, field) for c in self.connections]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate connection {field}")
        return self


ASSEMBLY_PROMPT = """Reason about mechanical connections from visible parts AND world knowledge.
Hidden structure may be inferred as explicit alternatives and assumptions; never label it observed.
For perforated thin plates, prefer a through bolt plus nut over an unsupported tapped thin sheet.
A bolt must pass aligned real holes, span the ordered clamped stack, seat its head, and reach
an explicit mating thread carrier. Specify nut counterhold/capture, or the threaded support's
anchorage. Merely overlapping parts, assigning one rigid body, or a decorative screw is not
fastening evidence. Every fastener needs a connection. Include washers in stack when inferred.
Retain competing hypotheses and unknown hardware standards, preload, material and dimensions.
World knowledge motivates assumptions but is not an image or manufacturer measurement.
Use only supplied evidence refs. Geometry validation checks hypotheses, not real-product truth.
The analytic checker currently supports +Z withdrawal and box/cylinder/annulus geometry;
unsupported geometry must remain unverified, never invent a pass. Geometry and metadata must agree.
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def bounds(s):
    c = s["center"]
    if s["shape"] == "box":
        half = [v / 2 for v in s["size"]]
    elif s["shape"] in {"cylinder", "annulus"}:
        axis = s.get("axis", [0, 0, 1])
        half = [abs(v) * s["length"] / 2 + s["radius"] * math.sqrt(max(0, 1 - v * v)) for v in axis]
    else:
        return s["bounds"]
    return [[c[i] - half[i] for i in range(3)], [c[i] + half[i] for i in range(3)]]


def radial_overlap(s, x, y, radius):
    """Exact disk vs box/circular annulus cross section, conservative otherwise."""
    lo, hi = bounds(s)
    if math.hypot(max(lo[0] - x, 0, x - hi[0]), max(lo[1] - y, 0, y - hi[1])) >= radius - 1e-9:
        return False
    if s["shape"] == "box":
        return True
    if s["shape"] in {"cylinder", "annulus"} and s.get("axis", [0, 0, 1]) == [0, 0, 1]:
        d = math.hypot(s["center"][0] - x, s["center"][1] - y)
        return d < s["radius"] + radius - 1e-9 and d + radius > s.get("inner_radius", 0) + 1e-9
    return True


def _is_supported(s):
    return s["shape"] == "box" or (
        s["shape"] in {"cylinder", "annulus"} and s.get("axis", [0, 0, 1]) == [0, 0, 1]
    )


def audit_assembly(
    parts: list[dict], plan: AssemblyPlan | None, *, allowed_refs: set[str], required=False
) -> dict:
    """Parts/solids are world-frame, derived from the exact rendered recipe.

    Coordinates, geometry and evidence are hashed into the report. A hypothesis
    passing here is geometrically plausible only; runtime constraints are separate.
    """
    by_part = {p["id"]: p for p in parts}
    errors = []
    if len(by_part) != len(parts):
        errors.append("duplicate part identifiers")
    expected = {p["id"] for p in parts if p.get("category") == "fastener"}
    if not plan:
        errors += ["missing assembly plan"] if expected or required else []
        return {
            "status": "failed" if errors else "not_applicable",
            "issues": errors,
            "connections": [],
            "geometry_sha256": digest(parts),
            "physics_status": "not_run",
        }
    represented = {c.fastener for c in plan.connections}
    if expected - represented:
        errors.append(f"fasteners without connection semantics: {sorted(expected - represented)}")
    reports = []
    for c in plan.connections:
        issues = []
        facts = {}
        if not set(c.evidence_refs) <= allowed_refs:
            issues.append("unknown evidence reference")
        if c.axis != (0, 0, 1):
            issues.append("unsupported withdrawal axis; requires oriented geometry checker")
        members = [c.fastener, *c.stack, c.mate]
        if any(m not in by_part for m in members):
            issues.append("missing connection member")
        if c.retention == "unresolved":
            issues.append("mate reaction/counterhold unresolved")
        if any(n not in by_part or n in members for n in c.access_predecessors):
            issues.append(
                "invalid access predecessor; cannot remove connection members to pass tool access"
            )
        if issues:
            reports.append({"id": c.id, "status": "failed", "issues": issues, "facts": facts})
            continue
        own = {s["id"]: s for s in by_part[c.fastener]["solids"]}
        head, shaft = own.get(c.head_solid), own.get(c.shaft_solid)
        if (
            not head
            or not shaft
            or any(s["shape"] != "cylinder" or not _is_supported(s) for s in [head, shaft])
        ):
            issues.append("head and shaft must reference actual +Z cylinder solids")
        elif math.dist(head["center"][:2], shaft["center"][:2]) > 1e-8:
            issues.append("head and shaft are not coaxial")
        else:
            x, y, _ = shaft["center"]
            r = shaft["radius"]
            shlo, shhi = bounds(shaft)
            hlo, hhi = bounds(head)
            if abs(hlo[2] - shhi[2]) > 1e-5 or head["radius"] <= r:
                issues.append("head/shaft seat geometry inconsistent")
            facts["shaft_z_m"] = [shlo[2], shhi[2]]
            mate_solids = by_part[c.mate]["solids"]
            if c.mate_kind == "through_nut" and not any(
                s["shape"] == "annulus"
                and _is_supported(s)
                and math.dist(s["center"][:2], [x, y]) < 1e-6
                and r <= s["inner_radius"] <= r + 0.0003
                for s in mate_solids
            ):
                issues.append("nut requires a coaxial thread-envelope bore near shaft diameter")
            # All solids participate, not just the nominated clamped parts.
            clashes, tool_hits = [], []
            for p in parts:
                if p["id"] == c.fastener:
                    continue
                for s in p["solids"]:
                    lo, hi = bounds(s)
                    if min(shhi[2], hi[2]) - max(shlo[2], lo[2]) > 1e-7 and radial_overlap(
                        s, x, y, r
                    ):
                        clashes.append(f"{p['id']}/{s['id']}")
                    if min(hhi[2] + c.tool_length_m, hi[2]) - max(
                        hhi[2] + 1e-5, lo[2]
                    ) > 1e-7 and radial_overlap(s, x, y, c.tool_radius_m):
                        tool_hits.append(f"{p['id']}/{s['id']}")
            if clashes:
                issues.append("shaft intersects solid material: " + ", ".join(clashes[:12]))
            undeclared = [
                hit for hit in tool_hits if hit.split("/")[0] not in c.access_predecessors
            ]
            if undeclared:
                issues.append("straight tool approach blocked: " + ", ".join(undeclared[:12]))
            facts.update(shaft_collisions=clashes, tool_obstacles=tool_hits)
            facts["tool_access_status"] = (
                "requires_unverified_predecessor" if tool_hits else "clear"
            )
            outer_clashes = []
            for member in [c.fastener, *c.stack, c.mate]:
                for solid in by_part[member]["solids"]:
                    if member == c.fastener and solid["id"] != c.head_solid:
                        continue
                    if solid["shape"] not in {"annulus", "cylinder"} or not _is_supported(solid):
                        continue
                    blo, bhi = bounds(solid)
                    for other in parts:
                        if other["id"] == member or other["id"] == c.fastener:
                            continue
                        for obstacle in other["solids"]:
                            olo, ohi = bounds(obstacle)
                            if min(bhi[2], ohi[2]) - max(blo[2], olo[2]) > 1e-7 and radial_overlap(
                                obstacle, solid["center"][0], solid["center"][1], solid["radius"]
                            ):
                                outer_clashes.append(
                                    f"{member} intersects {other['id']}/{obstacle['id']}"
                                )
            if outer_clashes:
                issues.append("bearing hardware collision: " + ", ".join(outer_clashes[:12]))
            facts["bearing_hardware_collisions"] = outer_clashes
            previous_bottom = hlo[2]
            surfaces = []
            for name in [*c.stack, c.mate]:
                # Inspect material beneath the actual head footprint, not whole-part bounds.
                sections = [
                    bounds(s)
                    for s in by_part[name]["solids"]
                    if radial_overlap(s, x, y, head["radius"])
                    and bounds(s)[0][2] < hlo[2] + 1e-5
                    and bounds(s)[1][2] > shlo[2] - 1e-5
                ]
                if any(
                    not _is_supported(s) and radial_overlap(s, x, y, head["radius"])
                    for s in by_part[name]["solids"]
                ):
                    issues.append(f"{name}: unsupported bearing geometry")
                if not sections:
                    issues.append(f"{name}: no bearing material within bolt reach/head footprint")
                    continue
                top = max(b[1][2] for b in sections)
                # Only the contiguous material layer starting at this top surface counts.
                bottom = min(b[0][2] for b in sections if b[1][2] >= top - 1e-7)
                gap = previous_bottom - top
                if abs(gap) > 0.00005:
                    issues.append(f"{name}: stack gap/overlap {gap * 1000:.3f} mm")
                surfaces.append({"part": name, "top_m": top, "bottom_m": bottom, "gap_m": gap})
                previous_bottom = bottom
            facts["bearing_stack"] = surfaces
            if surfaces and surfaces[-1]["part"] == c.mate:
                mate = surfaces[-1]
                overlap = min(shhi[2], mate["top_m"]) - max(shlo[2], mate["bottom_m"])
                facts["available_engagement_m"] = max(0, overlap)
                if overlap + 1e-7 < c.engagement_m:
                    issues.append("shaft too short for declared thread engagement")
                if c.mate_kind == "through_nut" and shlo[2] > mate["bottom_m"] - c.pitch_m + 1e-7:
                    issues.append("bolt does not project one assumed pitch beyond nut")
            # Same rigid body is allowed for a visual asset, but never fastening proof.
            facts["merged_into_one_body"] = len({by_part[n].get("body", n) for n in members}) == 1
        reports.append(
            {
                "id": c.id,
                "status": "failed" if issues else "geometrically_plausible",
                "issues": issues,
                "facts": facts,
            }
        )
    errors += [f"{r['id']}: {issue}" for r in reports for issue in r["issues"]]
    return {
        "status": "failed" if errors else "geometrically_plausible",
        "issues": errors,
        "connections": reports,
        "geometry_sha256": digest(parts),
        "plan_sha256": digest(plan.model_dump(mode="json")),
        "allowed_evidence_refs": sorted(allowed_refs),
        "unresolved_relations": plan.unresolved_relations,
        "physics_status": "not_run",
        "real_hardware_status": "hypothesis",
        "scope": "Analytic shaft clearance, bearing stack, reach and straight tool corridor; "
        "not preload, material strength, nut tool fit, robot reach or assembly dynamics.",
    }


def persist_audit(output: Path, parts, plan, *, allowed_refs, required=False):
    report = audit_assembly(parts, plan, allowed_refs=allowed_refs, required=required)
    write_json(output / "assembly_geometry.json", {"parts": parts})
    write_json(output / "assembly_plan.json", plan.model_dump(mode="json") if plan else None)
    write_json(output / "assembly_validation.json", report)
    if report["status"] == "failed":
        raise ValueError("assembly validation failed: " + "; ".join(report["issues"]))
    return report


class ConnectionFamilyProposal(Contract):
    region: str
    observed: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    alternatives: list[str] = Field(min_length=2)
    preferred_hypothesis: str
    mechanical_reason: str
    assumptions: list[str] = Field(min_length=1)
    unresolved: list[str] = Field(min_length=1)


class AssemblyReasoning(Contract):
    families: list[ConnectionFamilyProposal] = Field(min_length=1, max_length=12)
    rejected_inferences: list[str]


def infer_connections(config, context: dict, images: list[Path], output: Path):
    """Optional focused VLM reinspection, reusing the repository client and trace format."""
    from ..construction.design import infer_model
    from ..io import sha256
    from ..vlm import make_vlm

    write_json(
        output / "input.json",
        {**context, "images": [{"file": str(p), "sha256": sha256(p)} for p in images]},
    )
    result = infer_model(
        make_vlm(config, system=ASSEMBLY_PROMPT),
        "assembly-reasoning",
        json.dumps(context, ensure_ascii=False),
        images,
        AssemblyReasoning,
        output / "trace",
    )
    allowed = set(context["allowed_refs"])
    if any(not set(f.evidence_refs) <= allowed for f in result.families):
        raise ValueError("unknown assembly evidence reference")
    write_json(output / "proposal.json", result.model_dump(mode="json"))
    return result
