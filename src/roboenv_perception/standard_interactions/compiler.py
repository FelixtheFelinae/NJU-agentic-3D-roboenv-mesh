"""Compile instance bindings into locked parameters for the existing v9 runtime.

No model calls, simulator actions, geometry repair, or physical pass claims occur here.
"""

import copy
import math
from pathlib import PurePosixPath

from pydantic import Field, model_validator

from ..contracts import Contract
from .catalog import (
    CATALOG_SHA256,
    StandardComponentError,
    digest,
    load_catalog,
    resolve,
    verify_runtime_sources,
)

FASTENER_REF = "M4x0.7_hex_socket@1"
TOOL_REF = "electric_screwdriver_v9@1"
RUNTIME_REF = "native-mimic-helical-v9@1"
PROTOCOL_REF = "assembly-v9@1"


class ConnectionBinding(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    standard_ref: str
    tool_ref: str
    runtime_ref: str
    protocol_ref: str
    screw: str
    support: str
    clamped_member: str
    tool: str
    origin_m: tuple[float, float, float]
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    engagement_m: float = Field(gt=0, le=0.1)
    removal_distance_m: float = Field(gt=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(min_length=1)
    predecessors: list[str] = Field(default_factory=list)
    mate_kind: str = "tapped_support"

    @model_validator(mode="after")
    def valid_binding(self):
        if self.removal_distance_m <= self.engagement_m:
            raise ValueError("removal distance must exceed engagement")
        if not all(math.isfinite(x) for x in (*self.origin_m, *self.axis)):
            raise ValueError("connection frame must be finite")
        paths = [self.screw, self.support, self.clamped_member, self.tool]
        for path in paths:
            if (
                not path.startswith("/")
                or "//" in path
                or any(p in {".", ".."} for p in path.split("/"))
                or str(PurePosixPath(path)) != path
                or any(not p.isidentifier() for p in path.split("/")[1:])
            ):
                raise ValueError("body bindings must be absolute USD prim paths")
        if len(set(paths)) != len(paths):
            raise ValueError("screw, support, clamped member and tool must be distinct")
        parents = {str(PurePosixPath(p).parent) for p in paths}
        if len(parents) != 1:
            raise ValueError("v9 runtime requires sibling body/tool roots")
        return self


def compile_connections(bindings, *, tool_profile=None):
    """Only instance geometry/lengths are supplied by callers; mechanics come from the kit."""
    bindings = [ConnectionBinding.model_validate(b) for b in bindings]
    if not bindings:
        raise ValueError("no connection bindings")
    ids = [b.id for b in bindings]
    if len(set(ids)) != len(ids) or len({b.screw for b in bindings}) != len(bindings):
        raise ValueError("duplicate connection or screw binding")
    if len({(b.support, b.clamped_member, b.tool) for b in bindings}) != 1:
        raise StandardComponentError("v9 AssemblyRuntime requires one shared support, lid and tool")
    for b in bindings:
        if not set(b.predecessors) <= set(ids) - {b.id}:
            raise ValueError("invalid connection predecessor")
    pending = {b.id: set(b.predecessors) for b in bindings}
    while pending:
        ready = {key for key, value in pending.items() if not value}
        if not ready:
            raise ValueError("cyclic disassembly dependencies")
        pending = {key: value - ready for key, value in pending.items() if key not in ready}
    kit = load_catalog()
    profile = kit["tool_profiles"].get(tool_profile) if tool_profile else None
    if tool_profile and profile is None:
        raise StandardComponentError(f"unregistered tool profile: {tool_profile}")
    connections = []
    for binding in bindings:
        b = binding
        f = resolve("fasteners", b.standard_ref)
        tool = resolve("tools", b.tool_ref)
        runtime = resolve("runtimes", b.runtime_ref)
        protocol = resolve("protocols", b.protocol_ref)
        verify_runtime_sources(runtime)
        if (
            list(b.axis) != runtime["supported_axis"]
            or b.mate_kind != runtime["supported_mate_kind"]
        ):
            raise StandardComponentError(
                "unsupported axis or mate kind; requires a new runtime version"
            )
        if (
            b.engagement_m > f["shaft_length_m"]
            or b.engagement_m <= runtime["thread_parameters"]["preload_release_m"]
        ):
            raise ValueError("engagement outside the registered shaft/preload geometry")
        if b.removal_distance_m < protocol["complete_removal_m"]:
            raise ValueError("removal travel shorter than frozen protocol")
        if tool["bit"]["tip_across_flats_m"] >= f["socket_af_m"]:
            raise StandardComponentError("registered bit does not fit registered socket")
        prefix = str(PurePosixPath(b.screw).parent)
        paths = {
            key: f"{prefix}/{name}_{b.id}"
            for key, name in {
                "guide_joint": "thread_guide",
                "lead_joint": "lead_joint",
                "helper_body": "lead",
                "rotor_body": "thread_rotor",
                "friction_joint": "friction",
                "hinge_joint": "thread_hinge",
            }.items()
        }
        body_paths = {
            path
            for item in bindings
            for path in (item.screw, item.support, item.clamped_member, item.tool)
        }
        if body_paths.intersection(paths.values()):
            raise ValueError("generated constraint paths collide with body bindings")
        thread = dict(
            runtime["thread_parameters"],
            lead_m=f["lead_m"],
            engagement_m=b.engagement_m,
            separation_m=b.removal_distance_m,
        )
        solver = dict(runtime["solver_thread"], lid_body=b.clamped_member)
        spec = {
            "schema": "envcreator.screw-interaction.v1",
            "screw": b.screw,
            "tool": b.tool,
            "fixed_support": None,
            "moving_support": b.support,
            "clamped_member": b.clamped_member,
            "origin_world_m": list(b.origin_m),
            "origin_support_local_m": list(b.origin_m),
            "axis_world": list(b.axis),
            "lead_m": f["lead_m"],
            "engagement_m": b.engagement_m,
            "separation_m": b.removal_distance_m,
            "socket_af_m": f["socket_af_m"],
            "tip_af_m": tool["bit"]["tip_across_flats_m"],
            "solver": runtime["solver"],
            "thread_parameters": thread,
            "engagement_parameters": runtime["engagement_parameters"],
            "solver_thread": solver,
            "friction_axis": "rotX",
            "axial_contact_enabled": True,
            "nominal_thread": f["nominal_thread"],
            "pitch_diameter_m": f["pitch_diameter_m"],
            "tilt_stiffness_Nm_rad": runtime["tilt_stiffness_Nm_rad"],
            "magnetic_retention": runtime["magnetic_retention"],
            "assumptions": runtime["assumptions"] + b.assumptions,
            **{key: value for key, value in paths.items() if key != "hinge_joint"},
        }
        motor = dict(tool["motor"])
        if profile:
            motor["max_command_rpm"] = profile["max_command_rpm"]
        expanded = {
            "fastener": f,
            "tool": dict(tool, motor=motor),
            "runtime": runtime,
            "protocol": protocol,
            "tool_profile": tool_profile,
        }
        connections.append(
            {
                "id": b.id,
                "binding": b.model_dump(mode="json"),
                "components": expanded,
                "components_sha256": digest(expanded),
                "paths": paths,
                "fastener_spec": spec,
            }
        )
    result = {
        "schema": "envcreator.compiled-assembly/1",
        "kit_id": kit["id"],
        "catalog_sha256": CATALOG_SHA256,
        "tool_profile": tool_profile,
        "status": "compiled_not_physically_verified",
        "physics_status": "not_run",
        "observation_permissions": "privileged instance geometry; no visual identification claim",
        "connections": connections,
        "limitations": kit["limitations"],
    }
    result["compiled_sha256"] = digest(result)
    return result


def verify_compiled(compiled):
    candidate = copy.deepcopy(compiled)
    checksum = candidate.pop("compiled_sha256", None)
    if digest(candidate) != checksum:
        raise StandardComponentError("compiled assembly hash mismatch")
    expected = compile_connections(
        [c["binding"] for c in candidate["connections"]], tool_profile=candidate["tool_profile"]
    )
    if compiled != expected:
        raise StandardComponentError("compiled parameters differ from registered components")
    return compiled
