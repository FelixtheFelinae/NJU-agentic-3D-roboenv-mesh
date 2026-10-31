"""Reviewed cart repair using video plus mechanical priors; original v4 stays immutable.

This is a case-specific proposal/compiler using the reusable harness audit. It is
not a claim that the VLM generated these metric placements or identified a BOM.
"""

import argparse
import copy
import json
from pathlib import Path

from roboenv_perception.harness.assembly import AssemblyPlan, audit_assembly
from roboenv_perception.harness.assembly_adapter import cart_parts
from roboenv_perception.io import sha256, write_json

REFS = {"video:top", "video:10.0", "video:18.9"}


def cut_box_solids(part, center, size):
    """Subtract an axis-aligned bore from box solids; render and audit share output."""
    result = []
    cutlo = [a - b / 2 for a, b in zip(center, size)]
    cuthi = [a + b / 2 for a, b in zip(center, size)]
    for s in part["solids"]:
        if s["shape"] != "box":
            result.append(s)
            continue
        lo = [a - b / 2 for a, b in zip(s["center"], s["size"])]
        hi = [a + b / 2 for a, b in zip(s["center"], s["size"])]
        ilo = [max(a, b) for a, b in zip(lo, cutlo)]
        ihi = [min(a, b) for a, b in zip(hi, cuthi)]
        if any(a >= b for a, b in zip(ilo, ihi)):
            result.append(s)
            continue
        for axis in range(3):
            for side in (0, 1):
                a, b = lo.copy(), hi.copy()
                if side == 0:
                    b[axis] = ilo[axis]
                else:
                    a[axis] = ihi[axis]
                if all(x < y - 1e-9 for x, y in zip(a, b)):
                    result.append(
                        dict(
                            s,
                            center=[(x + y) / 2 for x, y in zip(a, b)],
                            size=[y - x for x, y in zip(a, b)],
                        )
                    )
            lo[axis], hi[axis] = ilo[axis], ihi[axis]
    part["solids"] = result


def repair(original):
    spec = copy.deepcopy(original)
    parts = {p["id"]: p for p in spec["parts"]}
    changes = []
    for side, sgn in [("left", -1), ("right", 1)]:
        parts["rail_" + side]["center"][0] = sgn * 0.127
    for end, y in [("front", 0.10795), ("rear", -0.09525)]:
        parts["cross_" + end]["center"][1] = y
    changes.append(
        "Align longitudinal beam centers to ±127 mm and transverse beam rows to longitudinal hole centers (front Y=107.95 mm, rear Y=-95.25 mm); assumed dimensions, not measured."
    )

    def ring(name, x, y, z, radius, inner, length, category):
        p = {
            "id": name,
            "category": category,
            "center": [x, y, z],
            "body": "chassis",
            "evidence_status": "world_knowledge_hypothesis",
            "evidence_refs": sorted(REFS),
            "dimension_source": "assumed_metric_scale",
            "mass_source": "geometry_density_prior",
            "solids": [
                {
                    "shape": "annulus",
                    "center": [0, 0, 0],
                    "radius": radius,
                    "inner_radius": inner,
                    "length": length,
                    "axis": [0, 0, 1],
                    "color": "steel",
                }
            ],
        }
        parts[name] = p
        return p

    plans = []
    for old in spec["connections"]:
        if "fastener" not in old:
            continue
        name = old["fastener"]
        side = "left" if name.endswith("left") else "right"
        sgn = -1 if side == "left" else 1
        f = parts[name]
        if name.startswith("corner_"):
            end = "front" if "front" in name else "rear"
            # Place the corner hole on the inner row of rail holes.  The former
            # rail centreline coincided with the bearing housing and wheel hub.
            x, y = sgn * 0.1143, parts["cross_" + end]["center"][1]
            stack = ["cross_" + end, "rail_" + side]
            top, bottom = 0.0934, 0.0902
            length = 0.010
            predecessors = ["cross_upper"] if end == "front" else []
            observed = [
                "Perforated longitudinal and transverse beams overlap; corner head candidates visible in overhead view."
            ]
        elif name.startswith("mount_"):
            x, y = sgn * 0.1016, 0.12065
            p = parts["bumper_mount_" + side]
            p["center"] = [x, y, 0.0944]
            p["solids"] = [
                {
                    "shape": "box",
                    "center": [0, 0, 0],
                    "size": [0.016, 0.024, 0.002],
                    "color": "metal",
                },
                {
                    "shape": "box",
                    "center": [0, 0.01, -0.015],
                    "size": [0.016, 0.002, 0.030],
                    "color": "metal",
                },
            ]
            cut_box_solids(p, [0, 0, 0], [0.0054, 0.0054, 0.005])
            cut_box_solids(p, [0, 0.01, 0.073 - 0.0944], [0.0072, 0.005, 0.0072])
            stack = ["bumper_mount_" + side, "cross_front"]
            top, bottom = 0.0954, 0.0918
            length = 0.012
            predecessors = ["cross_upper"]
            observed = [
                "Slender support members run from the front beam towards the chassis; detailed bracket fasteners are not resolved."
            ]
        else:
            x, y = sgn * 0.1016, 0.226
            p = parts["bumper_pad_" + side]
            p["center"][2] = 0.0547  # pad upper face seats below beam at Z=77.2 mm
            # A declared side socket avoids making the support rod penetrate solid block.
            cut_box_solids(p, [0, -0.012, 0.073 - 0.0547], [0.0072, 0.020, 0.0072])
            stack = ["bumper", "bumper_pad_" + side]
            top, bottom = 0.0788, 0.0322
            length = 0.056
            predecessors = []
            observed = [
                "Front beam has visible round heads and black blocks below; front view shows projecting fastener-like rods."
            ]
            # Old short screw into invisible receiver is replaced by a long through-bolt hypothesis.
            parts.pop("bumper_receiver_" + side)
        upper = name + "_washer_head"
        lower = name + "_washer_nut"
        nut = name + "_nut"
        ring(upper, x, y, top + 0.0004, 0.0045, 0.0022, 0.0008, "washer")
        ring(lower, x, y, bottom - 0.0004, 0.0045, 0.0022, 0.0008, "washer")
        ring(nut, x, y, bottom - 0.0008 - 0.0016, 0.0040, 0.0020, 0.0032, "thread_receiver")
        seat = top + 0.0008
        f["center"] = [x, y, seat + 0.0014]
        f["solids"][1]["length"] = length
        f["solids"][1]["center"][2] = -0.0014 - length / 2
        # Surface patch denotes a candidate socket; it is not a contact-fit interface.
        f["mass_source"] = "geometry_density_prior"
        volume = 3.141592653589793 * (0.0036**2 * 0.0028 + 0.0019**2 * length)
        f["mass_kg_prior"] = 7850 * volume
        f["axial_inertia_kg_m2_prior"] = (
            0.5 * 7850 * 3.141592653589793 * (0.0036**4 * 0.0028 + 0.0019**4 * length)
        )
        old.update(
            clamped_parts=stack,
            mate=nut,
            type="through_bolt_nut_hypothesis",
            hidden_mate="assumed separate nut with counterhold tool",
            executable_model="not_compiled",
        )
        plans.append(
            {
                "id": old["id"],
                "fastener": name,
                "head_solid": "solid_0",
                "shaft_solid": "solid_1",
                "stack": [upper, *stack, lower],
                "mate": nut,
                "mate_kind": "through_nut",
                "retention": "counterhold_tool",
                "retention_basis": "Separate nut held by a second ideal wrench; actual access and tool fit remain unverified.",
                "axis": [0, 0, 1],
                "evidence_refs": sorted(REFS),
                "observed": observed,
                "assumptions": [
                    "All metric dimensions and 0.8 mm pitch are provisional.",
                    "Hidden washers and nut are mechanical completion priors, not identified hardware.",
                    "Nut represented by a through-bored circular envelope; external wrench flats not modeled.",
                ],
                "alternatives": [
                    "captured nut or threaded insert",
                    "dedicated tapped mounting block",
                ],
                "selection_reason": "Through-bolt and nut produce a continuous bearing stack without assuming threads in thin perforated sheet.",
                "unknowns": [
                    "actual thread standard, material, preload and torque",
                    "counterhold wrench clearance",
                    "complete attachment of support rods and black blocks",
                ],
                "pitch_m": 0.0008,
                "engagement_m": 0.0032,
                "access_predecessors": predecessors,
            }
        )
    spec["parts"] = list(parts.values())
    plan = AssemblyPlan(
        connections=plans,
        unresolved_relations=[
            "Support rod socket geometry is assumed; axial retention and antirotation are not identified.",
            "Mount bolt tool access requires removing cross_upper; its fastening sequence remains unresolved.",
            "Motor, controller and wheel retention hardware remain incomplete.",
            "All fasteners are separate semantic objects but still fused into chassis in the legacy rigid export.",
        ],
    )
    spec["assembly"] = plan.model_dump(mode="json")
    spec["repair_notes"] = changes + [
        "Replaced unreachable mount-to-rail claim with bracket-to-front-crossbeam through-bolt.",
        "Replaced front beam short threaded-support guess with long bolt clamping beam and black pad; alternative retained.",
        "Added explicit head/nut washers, actual through bores, nuts and geometry-derived steel mass/inertia priors.",
        "No clamp dynamics or complete disassembly claim. Prior single-screw runtime does not apply to revised bolts.",
    ]
    return spec, plan


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    original = json.loads(a.source.read_text())
    revised, plan = repair(original)
    write_json(a.output / "assembly_spec.json", revised)
    write_json(a.output / "assembly_plan.json", plan.model_dump(mode="json"))
    report = audit_assembly(cart_parts(revised), plan, allowed_refs=REFS, required=True)
    write_json(a.output / "assembly_validation.json", report)
    write_json(
        a.output / "workflow.json",
        {
            "source_sha256": sha256(a.source),
            "source_spec": str(a.source),
            "status": report["status"],
            "method": "Video reinspection + explicit world-knowledge hypothesis + deterministic geometry repair and independent analytic audit",
            "steps": revised["repair_notes"],
            "issues": report["issues"],
            "geometry_sha256": report["geometry_sha256"],
            "plan_sha256": report["plan_sha256"],
        },
    )
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if not report["issues"] else 2)


if __name__ == "__main__":
    main()
