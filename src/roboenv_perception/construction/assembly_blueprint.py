"""Simulator-neutral assembly description and explicit legacy readers.

P0 describes structure; it does not solve CAD mates or certify contact dynamics.
"""

import copy
import hashlib
import json
import math
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Contract
from .contracts import AssetBrief, Coupling, Dimensions, Joint, Part, Quantity
from .contracts import Blueprint as LegacyBlueprint

VERSION = "envcreator.assembly-blueprint/1"
Basis = Literal["observed", "inferred", "assumed"]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Evidence(Contract):
    id: str
    origin: Literal["observation", "measurement", "retrieval", "prior", "legacy"]
    description: str = Field(min_length=1)


class Parameter(Contract):
    quantity: Quantity = Field(default_factory=Quantity)
    unit: Literal["m", "rad", "kg", "N*m", "rad/s", "m/s", "dimensionless"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    interval: tuple[float, float] | None = None

    @model_validator(mode="after")
    def interval_order(self):
        if self.interval is not None:
            lo, hi = self.interval
            if lo > hi or (self.quantity.value is not None and not lo <= self.quantity.value <= hi):
                raise ValueError("parameter interval must contain its estimate")
            if self.quantity.source == "unknown":
                raise ValueError(
                    "an uncertainty interval needs an explicit estimate and provenance"
                )
        return self


class LocalFrame(Contract):
    # None is unknown, distinct from a known zero origin/rotation.
    origin: tuple[float, float, float] | None = None
    rotation_rpy: tuple[float, float, float] | None = None
    position_unit: Literal["m", "relative_width"] = "m"
    basis: Basis = "assumed"
    evidence_refs: list[str] = Field(default_factory=list)
    hint: str = "unresolved local frame"


class PartDefinition(Contract):
    id: str
    category: str
    geometry: str
    hollow: bool = False
    dimensions_m: Dimensions = Field(default_factory=Dimensions)
    material_appearance: str
    collision_intent: str
    visual_mesh_ref: str | None = None
    collision_mesh_ref: str | None = None
    mass_kg: Quantity = Field(default_factory=Quantity)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def positive_mass(self):
        if self.mass_kg.value is not None and self.mass_kg.value <= 0:
            raise ValueError("part mass must be positive")
        return self


class PartInstance(Contract):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    definition_id: str
    # Organizational grouping only; mate relations are a separate graph.
    parent: str | None = None


class Interface(Contract):
    id: str
    part: str
    kind: Literal[
        "plane",
        "axis",
        "edge",
        "cylinder",
        "hole",
        "shaft",
        "thread_internal",
        "thread_external",
        "hinge_region",
        "keyway",
        "gear_surface",
        "contact_region",
    ]
    local_frame: LocalFrame = Field(default_factory=LocalFrame)
    axis_local: tuple[float, float, float] | None = Field(
        default=None, description="Direction or plane normal in owning part coordinates; null if unknown."
    )
    parameters: dict[str, Parameter] = Field(default_factory=dict)
    basis: Basis
    evidence_refs: list[str] = Field(default_factory=list)
    description: str

    @model_validator(mode="after")
    def axis_unit(self):
        if self.axis_local is not None and not math.isclose(
            sum(x * x for x in self.axis_local), 1, abs_tol=1e-4
        ):
            raise ValueError("interface axis must be a local unit vector")
        return self


class Mate(Contract):
    id: str
    type: Literal[
        "fixed",
        "revolute",
        "prismatic",
        "cylindrical",
        "threaded",
        "interference_fit",
        "gear_relation",
        "contact",
    ]
    interfaces: tuple[str, str]
    parameters: dict[str, Parameter] = Field(default_factory=dict)
    basis: Basis
    evidence_refs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    contact_status: Literal["not_verified", "not_modelled"] = "not_verified"


class AssemblyJoint(Joint):
    mate_id: str

    # Unknown rest/travel is valid for description, never silently a build default.
    @model_validator(mode="after")
    def legal_motion(self):
        if self.type != "fixed" and (
            self.axis_child is None
            or not math.isclose(sum(v * v for v in self.axis_child), 1, abs_tol=1e-4)
        ):
            raise ValueError("moving joint needs a unit axis in child coordinates")
        for q in (self.effort_limit, self.velocity_limit, self.travel):
            if q.value is not None and q.value <= 0:
                raise ValueError("joint limits must be positive")
        if self.type in {"fixed", "continuous"} and (
            self.travel.value is not None or self.rest is not None
        ):
            raise ValueError("fixed/continuous joints do not have bounded travel/rest")
        return self


class GeometricConstraint(Contract):
    id: str
    type: Literal[
        "orthogonal", "parallel", "coplanar", "coaxial", "equal_width", "clearance", "planar"
    ]
    part_refs: list[str] = Field(default_factory=list)
    interface_refs: list[str] = Field(default_factory=list)
    parameters: dict[str, Parameter] = Field(default_factory=dict)
    basis: Basis
    evidence_refs: list[str] = Field(default_factory=list)
    description: str


class AssemblyState(Contract):
    id: str
    role: Literal["reference", "observed", "hypothesis"]
    description: str
    joint_positions: dict[str, Parameter] = Field(default_factory=dict)
    part_poses: dict[str, LocalFrame] = Field(default_factory=dict)
    active_mate_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class Hypothesis(Contract):
    id: str
    relation_refs: list[str] = Field(default_factory=list)
    explanation: str
    alternatives: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class PhysicsIntent(Contract):
    fidelity: Literal["kinematic", "contact_approximation", "contact_required"] = "kinematic"
    description: str = "physical function not verified"
    unresolved: list[str] = Field(default_factory=list)


class AssemblyBlueprint(Contract):
    schema_version: Literal["envcreator.assembly-blueprint/1"] = VERSION
    object_id: str
    kind: Literal["rigid", "articulated"]
    asset_brief: AssetBrief
    frame_convention: Literal["metres; Z-up; front=+Y; local object frame"] = (
        "metres; Z-up; front=+Y; local object frame"
    )
    coordinate_units: Literal["m", "relative_width"] = "m"
    target_dimensions_m: Dimensions = Field(default_factory=Dimensions)
    part_definitions: list[PartDefinition] = Field(min_length=1)
    part_instances: list[PartInstance] = Field(min_length=1)
    interfaces: list[Interface] = Field(default_factory=list)
    mates: list[Mate] = Field(default_factory=list)
    joints: list[AssemblyJoint] = Field(default_factory=list)
    couplings: list[Coupling] = Field(default_factory=list)
    geometric_constraints: list[GeometricConstraint] = Field(default_factory=list)
    states: list[AssemblyState] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    physics_intent: PhysicsIntent = Field(default_factory=PhysicsIntent)
    acceptance: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    # Populated only by explicit migration; retain original fields without pretending
    # their numeric/physical claims were validated observations.
    migration: dict | None = None

    @property
    def parts(self):
        """Read-only view consumed by existing geometry code; full JSON stays in the assembly schema."""
        definitions = {p.id: p for p in self.part_definitions}
        return [
            Part(
                name=p.name,
                parent=p.parent,
                role=definitions[p.definition_id].category,
                **definitions[p.definition_id].model_dump(
                    exclude={"id", "category", "mass_kg", "visual_mesh_ref", "collision_mesh_ref"}
                ),
            )
            for p in self.part_instances
        ]

    @model_validator(mode="after")
    def references_and_relations(self):
        def unique(rows, field):
            values = [getattr(r, field) for r in rows]
            if len(values) != len(set(values)):
                raise ValueError("duplicate assembly identifier: " + field)
            return set(values)

        defs = unique(self.part_definitions, "id")
        parts = unique(self.part_instances, "name")
        interfaces = unique(self.interfaces, "id")
        mates = unique(self.mates, "id")
        joints = unique(self.joints, "name")
        unique(self.states, "id")
        unique(self.geometric_constraints, "id")
        unique(self.hypotheses, "id")
        refs = unique(self.evidence, "id")
        if (
            self.asset_brief.object_id != self.object_id
            or self.asset_brief.build_route != self.kind
        ):
            raise ValueError("intake identity/kind differs from assembly")
        parents = {p.name: p.parent for p in self.part_instances}
        for p in self.part_instances:
            if p.definition_id not in defs:
                raise ValueError("unknown part definition")
            seen, current = set(), p.name
            while current is not None:
                if current in seen or current not in parents:
                    raise ValueError("invalid or cyclic organizational parent")
                seen.add(current)
                current = parents[current]
        by_interface = {i.id: i for i in self.interfaces}
        by_mate = {m.id: m for m in self.mates}
        for interface in self.interfaces:
            if interface.part not in parts:
                raise ValueError("interface references unknown part")
            if interface.local_frame.position_unit != self.coordinate_units:
                raise ValueError("interface frame units differ from assembly coordinates")
        for mate in self.mates:
            if any(i not in interfaces for i in mate.interfaces):
                raise ValueError("mate references unknown interface")
            if len({by_interface[i].part for i in mate.interfaces}) != 2:
                raise ValueError("mate needs interfaces on distinct part instances")
        for joint in self.joints:
            if joint.mate_id not in mates:
                raise ValueError("joint references unknown mate")
            mate = by_mate[joint.mate_id]
            if {joint.parent, joint.child} != {by_interface[i].part for i in mate.interfaces}:
                raise ValueError("joint endpoints differ from mate interfaces")
            if joint.type != mate.type and not (
                joint.type == "continuous" and mate.type == "revolute"
            ):
                raise ValueError(
                    "joint type contradicts mate; approximate relations need a later backend adapter"
                )
            if joint.evidence_kind == "observed" and not self.asset_brief.observed_motion:
                raise ValueError("observed joint motion absent from intake")
        if len({j.mate_id for j in self.joints}) != len(self.joints):
            raise ValueError(
                "multiple kinematic joints for one mate require an explicit backend representation"
            )
        if (self.kind == "articulated") != any(j.type != "fixed" for j in self.joints):
            raise ValueError("assembly kind differs from declared moving joints")
        for c in self.geometric_constraints:
            if (
                not (c.part_refs or c.interface_refs)
                or not set(c.part_refs) <= parts
                or not set(c.interface_refs) <= interfaces
            ):
                raise ValueError("constraint references unknown part/interface")
        by_joint = {j.name: j for j in self.joints}
        for state in self.states:
            if state.role == "observed" and not set(state.evidence_refs) & {
                e.id for e in self.evidence if e.origin == "observation"
            }:
                raise ValueError("observed state requires observation evidence")
            if (
                not set(state.joint_positions) <= joints
                or not set(state.part_poses) <= parts
                or not set(state.active_mate_ids) <= mates
            ):
                raise ValueError("state references unknown joint/part/mate")
            for name, parameter in state.joint_positions.items():
                unit = "m" if by_joint[name].type == "prismatic" else "rad"
                if parameter.unit != unit:
                    raise ValueError("state joint position unit differs from joint type")
            if any(
                frame.position_unit != self.coordinate_units for frame in state.part_poses.values()
            ):
                raise ValueError("state pose units differ from assembly coordinates")
        for h in self.hypotheses:
            if not set(h.relation_refs) <= (mates | joints | interfaces):
                raise ValueError("hypothesis references unknown relation")
        for c in self.couplings:
            if any(
                n not in joints or by_joint[n].type != "continuous"
                for n in (c.driver_joint, c.driven_joint)
            ):
                raise ValueError("coupling needs declared continuous joints")
        driven = [c.driven_joint for c in self.couplings]
        if len(driven) != len(set(driven)):
            raise ValueError("multiple couplings drive the same joint")
        links = {c.driven_joint: c.driver_joint for c in self.couplings}
        for name in links:
            seen, current = set(), name
            while current in links:
                if current in seen:
                    raise ValueError("cyclic ideal couplings")
                seen.add(current)
                current = links[current]
        if self.coordinate_units == "relative_width" and any(
            q.value is not None
            for d in [self.target_dimensions_m, *(p.dimensions_m for p in self.part_definitions)]
            for q in (d.x, d.y, d.z)
        ):
            raise ValueError("relative coordinates cannot claim dimensions in metres")
        prior_refs = {e.id for e in self.evidence if e.origin == "prior"}
        if any(not ref.startswith("prior:") for ref in prior_refs):
            raise ValueError("mechanical prior evidence IDs must use prior: prefix")
        observed_refs = {e.id for e in self.evidence if e.origin == "observation"}

        # Every provenance reference must be declared, including refs in nested quantities.
        def visit(value):
            if isinstance(value, dict):
                if not set(value.get("evidence_refs", [])) <= refs:
                    raise ValueError("unknown assembly evidence reference")
                if value.get("basis", value.get("evidence_kind")) == "observed" and not (
                    set(value.get("evidence_refs", [])) & observed_refs
                ):
                    raise ValueError("observed relation/frame requires observation evidence")
                if value.get("source") == "measured" and not (
                    set(value.get("evidence_refs", []))
                    & {e.id for e in self.evidence if e.origin == "measurement"}
                ):
                    raise ValueError("measured quantity needs measurement evidence")
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self.model_dump(mode="json", exclude={"migration"}))
        return self


def parse_blueprint(value):
    if isinstance(value, (AssemblyBlueprint, LegacyBlueprint)):
        return value
    if value.get("schema_version") == VERSION:
        return AssemblyBlueprint.model_validate(value)
    return LegacyBlueprint.model_validate(value)


def legacy_projection(blueprint):
    """Explicit limited backend view, never overwrite the assembly document with it."""
    if isinstance(blueprint, LegacyBlueprint):
        return blueprint
    if blueprint.coordinate_units != "m":
        raise ValueError(
            "legacy geometry backend requires explicit metric scale; relative_width is description-only"
        )
    if {j.mate_id for j in blueprint.joints} != {m.id for m in blueprint.mates}:
        raise ValueError(
            "legacy geometry backend cannot silently drop mates without joint representations"
        )
    joints = []
    for j in blueprint.joints:
        if j.type in {"revolute", "prismatic"} and j.rest is None:
            raise ValueError("legacy geometry backend needs an explicit reference/rest convention")
        joints.append(j.model_dump(exclude={"mate_id"}))
    return LegacyBlueprint.model_validate(
        {
            "object_id": blueprint.object_id,
            "kind": blueprint.kind,
            "frame_convention": blueprint.frame_convention,
            "target_dimensions_m": blueprint.target_dimensions_m.model_dump(),
            "parts": [p.model_dump() for p in blueprint.parts],
            "joints": joints,
            "couplings": [c.model_dump() for c in blueprint.couplings],
            "acceptance": blueprint.acceptance,
            "assumptions": blueprint.assumptions,
            "unresolved": blueprint.unresolved,
        }
    )


def migrate_envcreator(value, brief):
    old = LegacyBlueprint.model_validate(value)
    brief = AssetBrief.model_validate(brief)
    definitions, instances = [], []
    refs = set(brief.evidence_refs)

    def collect(v):
        if isinstance(v, dict):
            refs.update(v.get("evidence_refs", []))
            for child in v.values():
                collect(child)
        elif isinstance(v, list):
            for child in v:
                collect(child)

    collect(old.model_dump(mode="json"))
    for p in old.parts:
        definitions.append(
            PartDefinition(
                id=p.name + "_definition",
                category=p.role,
                **p.model_dump(exclude={"name", "parent", "role"}),
            )
        )
        instances.append(
            PartInstance(name=p.name, definition_id=p.name + "_definition", parent=p.parent)
        )
    interfaces, mates, joints = [], [], []
    for j in old.joints:
        ids = (j.name + "_parent_interface", j.name + "_child_interface")
        for name, part in zip(ids, (j.parent, j.child), strict=True):
            interfaces.append(
                Interface(
                    id=name,
                    part=part,
                    kind="hinge_region"
                    if j.type in {"revolute", "continuous"}
                    else "contact_region",
                    basis="assumed",
                    evidence_refs=j.evidence_refs,
                    description="Migration placeholder for legacy joint endpoint; no shaft/hole surface recovered.",
                    local_frame=LocalFrame(hint=j.origin_hint),
                )
            )
        mate_id = j.name + "_mate"
        mates.append(
            Mate(
                id=mate_id,
                type="revolute" if j.type == "continuous" else j.type,
                interfaces=ids,
                basis="assumed",
                evidence_refs=j.evidence_refs,
                assumptions=["Legacy kinematic joint does not establish physical mating geometry."],
            )
        )
        joints.append(AssemblyJoint(mate_id=mate_id, **j.model_dump()))
    return AssemblyBlueprint(
        object_id=old.object_id,
        kind=old.kind,
        asset_brief=brief,
        target_dimensions_m=old.target_dimensions_m,
        part_definitions=definitions,
        part_instances=instances,
        interfaces=interfaces,
        mates=mates,
        joints=joints,
        couplings=old.couplings,
        acceptance=old.acceptance,
        assumptions=old.assumptions,
        unresolved=old.unresolved,
        evidence=[
            Evidence(
                id=r,
                origin="measurement"
                if r.startswith("measurement:")
                else "observation"
                if r.startswith("view-")
                else "legacy",
                description="Legacy reference; provenance must be checked against original evidence packet.",
            )
            for r in sorted(refs)
        ],
        migration={
            "source_format": "envcreator-v1",
            "source_sha256": digest(value),
            "original": copy.deepcopy(value),
            "warnings": ["Interface surfaces/frames remain unresolved; no new observation."],
        },
    )


def migrate_artisan(value, *, object_id, evidence_refs):
    """Import the original Artisan numeric blueprint as assumptions, never measurements.

    The caller supplies identity and source refs; original metadata/tasks/dynamics
    remain in the migration record until dedicated typed backends can consume them.
    """
    refs = list(evidence_refs)
    if not refs:
        raise ValueError("Artisan migration needs explicit source evidence refs")

    def quantity(v, rationale):
        return Quantity(
            value=v,
            source="assumed" if v is not None else "unknown",
            evidence_refs=refs if v is not None else [],
            rationale=rationale,
        ).model_dump()

    def dimensions(d):
        return {
            a: quantity((d or {}).get(a), "Imported Artisan value; not a measurement.")
            for a in ("x", "y", "z")
        }

    parts = [
        {
            "name": p["name"],
            "parent": p.get("parent"),
            "role": p.get("role", "part"),
            "geometry": p.get("geometry_hint", "unspecified"),
            "hollow": p.get("hollow", False),
            "dimensions_m": dimensions(p.get("dimensions")),
            "material_appearance": p.get("material", "unknown"),
            "collision_intent": "legacy approximation; unverified",
            "evidence_refs": refs,
        }
        for p in value.get("parts", [])
    ]
    joints = []
    for j in value.get("joints", []):
        joints.append(
            {
                "name": j["name"],
                "parent": j["parent"],
                "child": j["child"],
                "type": j["type"],
                "axis_child": j.get("axis"),
                "origin_hint": j.get("origin_hint", "unknown"),
                "travel": quantity(
                    j.get("travel"), "Imported Artisan limit; simulation assumption."
                ),
                "rest": j.get("rest"),
                "evidence_kind": "assumed",
                "evidence_refs": refs,
            }
        )
    kind = "articulated" if any(j["type"] != "fixed" for j in joints) else "rigid"
    brief = AssetBrief(
        object_id=object_id,
        category="legacy_artisan_asset",
        summary=(value.get("asset_brief") or {}).get(
            "summary", "Imported Artisan engineering description"
        ),
        build_route=kind,
        support_mode="unknown",
        reference_interpretation="single",
        evidence_refs=refs,
        key_interactions=value.get("tasks", []),
        unresolved=["Legacy numeric parameters are assumptions; verify against source images."],
    )
    if value.get("couplings"):
        raise ValueError(
            "Artisan coupling migration needs an explicit signed-ratio adapter; refusing silent loss"
        )
    result = migrate_envcreator(
        {
            "object_id": object_id,
            "kind": kind,
            "frame_convention": "metres; Z-up; front=+Y; local object frame",
            "target_dimensions_m": dimensions(value.get("target_dimensions")),
            "parts": parts,
            "joints": joints,
            "acceptance": value.get("acceptance") or ["legacy asset, not verified"],
        },
        brief,
    )
    result.migration = {
        "source_format": "artisan",
        "source_sha256": digest(value),
        "original": copy.deepcopy(value),
        "warnings": [
            "Imported numeric values are assumed. Dynamics intent, tasks and metadata preserved in original; not physical certification."
        ],
    }
    return result


def main(argv=None):
    """Offline migration/check entry; semantic selfcheck still uses the Harness tool."""
    import argparse
    from pathlib import Path

    from ..io import read_json, sha256, write_json
    from .assembly_design import check_assembly

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--source-format", choices=["assembly", "envcreator", "artisan"], default="assembly"
    )
    parser.add_argument("--intake", type=Path)
    parser.add_argument("--object-id")
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    value = read_json(args.input)
    if args.source_format == "envcreator":
        if not args.intake:
            parser.error("EnvCreator migration needs --intake")
        bp = migrate_envcreator(value, read_json(args.intake))
    elif args.source_format == "artisan":
        if not args.object_id:
            parser.error("Artisan migration needs --object-id")
        bp = migrate_artisan(value, object_id=args.object_id, evidence_refs=args.evidence_ref)
    else:
        bp = AssemblyBlueprint.model_validate(value)
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "blueprint.json", bp.model_dump(mode="json"))
    report = check_assembly(bp)
    report["blueprint_sha256"] = sha256(args.output / "blueprint.json")
    report["model_selfcheck_status"] = "not_run"
    write_json(args.output / "selfcheck.json", report)
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "status": report["status"]}, ensure_ascii=False
        )
    )
    return 0 if report["status"] != "needs_revision" else 2


if __name__ == "__main__":
    raise SystemExit(main())
