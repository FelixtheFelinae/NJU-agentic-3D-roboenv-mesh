"""Versioned part dimensions and pre-placement compatibility rules for Isaac assets."""

import math
from dataclasses import dataclass

from pydantic import Field, model_validator

from ..contracts import Contract

CLEARANCE_RULE = "screw_clearance_hole@1"
THREAD_RULE = "releasable_metric_thread@1"
HINGE_RULE = "bounded_hinge@1"


@dataclass(frozen=True)
class MetricScrew:
    diameter_m: float
    pitch_m: float
    clearance_hole_m: float

    @property
    def lead_m(self) -> float:
        return self.pitch_m  # This catalog contains single-start right-hand threads.

    @property
    def lead_angle_rad(self) -> float:
        basic_pitch_diameter = self.diameter_m - (3 * math.sqrt(3) / 8) * self.pitch_m
        return math.atan(self.lead_m / (math.pi * basic_pitch_diameter))


# ISO metric coarse single-start pitches and ISO 273 medium-series clearance bores.
# The geometry builder uses simplified heads and smooth shanks as simulation proxies.
SCREWS = {
    "M3x0.5@1": MetricScrew(.003, .0005, .0034),
    "M4x0.7@1": MetricScrew(.004, .0007, .0045),
    "M5x0.8@1": MetricScrew(.005, .0008, .0055),
}


class ThreadParameters(Contract):
    engagement_m: float = Field(gt=0, allow_inf_nan=False)
    unload_travel_m: float = Field(gt=0, allow_inf_nan=False)
    preload_N: float = Field(ge=0, allow_inf_nan=False)
    breakaway_torque_Nm: float = Field(gt=0, allow_inf_nan=False)
    running_torque_Nm: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_thresholds(self):
        if self.unload_travel_m >= self.engagement_m:
            raise ValueError("unload travel must be shorter than initial thread engagement")
        if self.running_torque_Nm > self.breakaway_torque_Nm:
            raise ValueError("running torque must not exceed breakaway torque")
        return self


def _value(interface, name, unit):
    parameter = interface.parameters.get(name)
    if parameter is None or parameter.unit != unit or parameter.quantity.value is None:
        raise ValueError(f"{interface.id} requires {name} in {unit}")
    value = parameter.quantity.value
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{interface.id}.{name} must be finite and positive")
    return value


def compile_mates(blueprint):
    """Check declared interfaces in their local frames before any Blender job."""
    interfaces = {interface.id: interface for interface in blueprint.interfaces}
    definitions = {part.id: part for part in blueprint.part_definitions}
    specs = {part.name: definitions[part.definition_id].spec_ref for part in blueprint.part_instances}
    results = []
    for mate in blueprint.mates:
        if mate.rule_ref is None:
            continue
        a, b = (interfaces[ref] for ref in mate.interfaces)
        row = {"mate_id": mate.id, "rule_ref": mate.rule_ref, "interfaces": list(mate.interfaces)}
        if mate.rule_ref == CLEARANCE_RULE:
            if a.kind == "hole":
                a, b = b, a
            if mate.type != "contact" or (a.kind, b.kind) != ("shaft", "hole"):
                raise ValueError(f"{mate.id}: clearance rule needs shaft and through-hole interfaces")
            diameter, bore = _value(a, "diameter", "m"), _value(b, "diameter", "m")
            spec = SCREWS.get(specs[a.part])
            if spec is None or not math.isclose(spec.diameter_m, diameter):
                raise ValueError(f"{mate.id}: shaft diameter differs from the screw specification")
            if bore <= diameter:
                raise ValueError(f"{mate.id}: through-hole must clear the shaft envelope")
            row.update(radial_clearance_m=(bore - diameter) / 2,
                       recommended_diameter_m=spec.clearance_hole_m)
        elif mate.rule_ref == THREAD_RULE:
            if a.kind == "thread_internal":
                a, b = b, a
            if mate.type != "threaded" or (a.kind, b.kind) != ("thread_external", "thread_internal"):
                raise ValueError(f"{mate.id}: thread rule needs external and internal thread interfaces")
            spec_ref = specs[a.part]
            spec = SCREWS.get(spec_ref)
            if spec is None:
                raise ValueError(f"{mate.id}: unsupported screw specification: {spec_ref}")
            for interface in (a, b):
                if (not math.isclose(_value(interface, "diameter", "m"), spec.diameter_m)
                        or not math.isclose(_value(interface, "lead", "m"), spec.lead_m)
                        or _value(interface, "right_handed", "dimensionless") != 1):
                    raise ValueError(f"{mate.id}: incompatible thread geometry or handedness")
            params = ThreadParameters(
                engagement_m=_value(mate, "engagement", "m"),
                unload_travel_m=_value(mate, "unload_travel", "m"),
                preload_N=_nonnegative(mate, "preload", "N"),
                breakaway_torque_Nm=_value(mate, "breakaway_torque", "N*m"),
                running_torque_Nm=_nonnegative(mate, "running_torque", "N*m"),
            )
            if params.engagement_m > min(_value(i, "length", "m") for i in (a, b)) + 1e-10:
                raise ValueError(f"{mate.id}: engagement exceeds the shorter thread")
            row.update(params.model_dump(), spec_ref=spec_ref, lead_m=spec.lead_m,
                       lead_angle_rad=spec.lead_angle_rad, handedness="right",
                       turns_to_clear=params.engagement_m / spec.lead_m,
                       screw=a.part, support=b.part)
        elif mate.rule_ref == HINGE_RULE:
            joints = [joint for joint in blueprint.joints if joint.mate_id == mate.id]
            if (mate.type != "revolute" or len(joints) != 1
                    or any(i.kind not in {"axis", "shaft", "hole", "cylinder"} for i in (a, b))):
                raise ValueError(f"{mate.id}: hinge needs two axial interfaces and one revolute joint")
            joint = joints[0]
            travel = joint.travel.value
            if joint.type != "revolute" or travel is None or not math.isfinite(travel) or not joint.rest:
                raise ValueError(f"{mate.id}: hinge needs a finite range and reference zero")
            lower, upper = joint_limits(travel, joint.rest)
            row.update(joint=joint.name, lower_rad=lower, upper_rad=upper)
        else:
            raise ValueError(f"{mate.id}: unknown connection rule {mate.rule_ref}")
        results.append(row)
    return results


def _nonnegative(mate, name, unit):
    parameter = mate.parameters.get(name)
    value = parameter.quantity.value if parameter and parameter.unit == unit else None
    if value is None or not math.isfinite(value) or value < 0:
        raise ValueError(f"{mate.id} requires nonnegative {name} in {unit}")
    return value


def joint_limits(travel, rest):
    if travel is None or not math.isfinite(travel) or travel <= 0:
        raise ValueError("bounded joint requires explicit positive travel")
    lower = {"at_min": 0.0, "centered": -travel / 2, "at_max": -travel}[rest]
    return lower, lower + travel
