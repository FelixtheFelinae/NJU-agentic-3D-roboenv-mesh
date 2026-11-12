"""Build a known-parameter screw cover or hinge without perception or Artisan."""

import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from ..construction.assembly_blueprint import AssemblyBlueprint
from ..construction.blender_backend import build_blender_meshes
from ..construction.part_rules import CLEARANCE_RULE, HINGE_RULE, SCREWS, THREAD_RULE, ThreadParameters, compile_mates
from ..contracts import Contract
from ..integration.bundle import export_bundle
from ..io import sha256, write_json
from .direct_usd import author_usd, recipe_joints
from .geometry import GeometryRecipe

PRIOR = "prior:declared-design"


class ScrewCover(Contract):
    kind: Literal["screw_cover"]
    screw_spec: Literal["M3x0.5@1", "M4x0.7@1", "M5x0.8@1"]
    shaft_length_m: float = Field(gt=0, le=.1, allow_inf_nan=False)
    cover_thickness_m: float = Field(default=.003, gt=0, le=.1, allow_inf_nan=False)
    base_thickness_m: float = Field(default=.022, gt=0, le=.2, allow_inf_nan=False)
    outer_radius_m: float = Field(default=.03, gt=0, le=.2, allow_inf_nan=False)
    hole_diameter_m: float | None = Field(default=None, gt=0, le=.1, allow_inf_nan=False)
    thread: ThreadParameters

    @model_validator(mode="after")
    def buildable(self):
        spec = SCREWS[self.screw_spec]
        bore = self.hole_diameter_m or spec.clearance_hole_m
        if not spec.diameter_m < bore < 1.75 * spec.diameter_m < 2 * self.outer_radius_m:
            raise ValueError("shaft, bore, head and plate diameters must be strictly ordered")
        reach = self.shaft_length_m - self.cover_thickness_m
        if not self.thread.engagement_m <= reach < self.base_thickness_m:
            raise ValueError("shaft must reach the declared engagement without exiting the support")
        return self


class HingedCover(Contract):
    kind: Literal["hinged_cover"]
    open_angle_deg: float = Field(gt=0, le=180, allow_inf_nan=False)
    size_x_m: float = Field(default=.08, gt=0, le=.5, allow_inf_nan=False)
    size_y_m: float = Field(default=.06, gt=0, le=.5, allow_inf_nan=False)
    cover_thickness_m: float = Field(default=.004, gt=0, le=.1, allow_inf_nan=False)
    base_thickness_m: float = Field(default=.012, gt=0, le=.2, allow_inf_nan=False)


DESIGN = TypeAdapter(Annotated[ScrewCover | HingedCover, Field(discriminator="kind")])


def _quantity(value):
    return {"value": value, "source": "assumed", "evidence_refs": [PRIOR],
            "rationale": "Explicit design value or generator profile; not a measurement."}


def _parameter(value, unit):
    return {"quantity": _quantity(value), "unit": unit}


def _frame(origin):
    return {"origin": origin, "rotation_rpy": [0, 0, 0], "basis": "assumed",
            "evidence_refs": [PRIOR]}


def _interface(name, part, kind, origin, axis=(0, 0, 1), **params):
    return {"id": name, "part": part, "kind": kind, "local_frame": _frame(origin),
            "axis_local": axis, "parameters": params, "basis": "assumed",
            "evidence_refs": [PRIOR], "description": name}


def _mate(name, kind, rule, endpoints, **params):
    return {"id": name, "type": kind, "rule_ref": rule, "interfaces": endpoints,
            "parameters": params, "basis": "assumed", "evidence_refs": [PRIOR]}


def _solid(name, part, shape, center, **dimensions):
    return {"name": name, "part": part, "shape": shape, "center_m": center,
            "source": "assumed", "evidence_refs": [PRIOR], **dimensions}


def _screw_geometry(design):
    spec = SCREWS[design.screw_spec]
    diameter, lead = spec.diameter_m, spec.lead_m
    shaft, plate, base = design.shaft_length_m, design.cover_thickness_m, design.base_thickness_m
    bore = design.hole_diameter_m or spec.clearance_hole_m
    radius, head_radius, head_height = design.outer_radius_m, .875 * diameter, .75 * diameter
    solids = [
        _solid("base_ring", "base", "annulus", (0, 0, -base / 2), length_m=base,
               radius_m=radius, inner_radius_m=bore / 2),
        _solid("cover_ring", "cover", "annulus", (0, 0, plate / 2), length_m=plate,
               radius_m=radius, inner_radius_m=bore / 2),
        _solid("screw_head", "screw", "cylinder", (0, 0, head_height / 2), length_m=head_height,
               radius_m=head_radius, assembly_role="fastener_head", material_class="steel"),
        _solid("screw_shaft", "screw", "cylinder", (0, 0, -shaft / 2), length_m=shaft,
               radius_m=diameter / 2, assembly_role="fastener_shaft", material_class="steel"),
    ]
    poses = {"base": (0, 0, base), "cover": (0, 0, base), "screw": (0, 0, base + plate)}
    ring_area = math.pi * (radius**2 - (bore / 2)**2)
    masses = {"base": 2700 * ring_area * base, "cover": 2700 * ring_area * plate,
              "screw": 7850 * math.pi * (head_radius**2 * head_height + (diameter / 2)**2 * shaft)}
    sizes = {"base": (2 * radius, 2 * radius, base), "cover": (2 * radius, 2 * radius, plate),
             "screw": (2 * head_radius, 2 * head_radius, head_height + shaft)}
    thread_shape = {"diameter": _parameter(diameter, "m"), "lead": _parameter(lead, "m"),
                    "right_handed": _parameter(1, "dimensionless")}
    interfaces = [
        _interface("screw.shank", "screw", "shaft", (0, 0, -shaft / 2), diameter=_parameter(diameter, "m")),
        _interface("cover.bore", "cover", "hole", (0, 0, 0), diameter=_parameter(bore, "m")),
        _interface("screw.thread", "screw", "thread_external", (0, 0, -plate),
                   length=_parameter(shaft - plate, "m"), **thread_shape),
        _interface("base.thread", "base", "thread_internal", (0, 0, 0),
                   length=_parameter(base, "m"), **thread_shape),
    ]
    thread = design.thread
    mates = [
        _mate("cover_clearance", "contact", CLEARANCE_RULE, ["screw.shank", "cover.bore"]),
        _mate("main_thread", "threaded", THREAD_RULE, ["screw.thread", "base.thread"],
              engagement=_parameter(thread.engagement_m, "m"),
              unload_travel=_parameter(thread.unload_travel_m, "m"),
              preload=_parameter(thread.preload_N, "N"),
              breakaway_torque=_parameter(thread.breakaway_torque_Nm, "N*m"),
              running_torque=_parameter(thread.running_torque_Nm, "N*m")),
    ]
    return solids, poses, masses, sizes, interfaces, mates, [], []


def _hinge_geometry(design):
    x, y, plate, base = design.size_x_m, design.size_y_m, design.cover_thickness_m, design.base_thickness_m
    pivot = (-x / 2, 0, base + .0005)
    axis = (0, -1, 0)
    solids = [_solid("base_plate", "base", "box", (0, 0, base / 2), size_m=(x, y, base)),
              _solid("cover_plate", "cover", "box", (x / 2, 0, plate / 2), size_m=(x, y, plate))]
    poses, sizes = {"base": (0, 0, 0), "cover": pivot}, {"base": (x, y, base), "cover": (x, y, plate)}
    masses = {name: 2700 * math.prod(size) for name, size in sizes.items()}
    interfaces = [_interface("base.hinge", "base", "axis", pivot, axis),
                  _interface("cover.hinge", "cover", "axis", (0, 0, 0), axis)]
    mates = [_mate("main_hinge", "revolute", HINGE_RULE, ["base.hinge", "cover.hinge"])]
    joints = [{"name": "cover_hinge", "mate_id": "main_hinge", "type": "revolute",
               "parent": "base", "child": "cover", "axis_child": axis, "rest": "at_min",
               "travel": _quantity(math.radians(design.open_angle_deg)), "origin_hint": "cover pivot",
               "evidence_kind": "assumed", "evidence_refs": [PRIOR]}]
    placements = [{"joint": "cover_hinge", "origin_parent_m": pivot, "source": "assumed",
                   "evidence_refs": [PRIOR]}]
    return solids, poses, masses, sizes, interfaces, mates, joints, placements


def make_blueprint(design, task):
    is_screw = isinstance(design, ScrewCover)
    solids, poses, masses, sizes, interfaces, mates, joints, placements = (
        _screw_geometry(design) if is_screw else _hinge_geometry(design)
    )
    kind = "rigid" if is_screw else "articulated"
    ident = design.kind
    blueprint = AssemblyBlueprint.model_validate({
        "object_id": ident, "kind": kind,
        "asset_brief": {"object_id": ident, "category": ident, "summary": task, "build_route": kind,
                        "support_mode": "grounded", "reference_interpretation": "single",
                        "evidence_refs": [PRIOR]},
        "part_definitions": [
            {"id": name, "category": "fastener" if name == "screw" else "structural_part",
             "spec_ref": design.screw_spec if name == "screw" else None,
             "geometry": "deterministic parametric geometry", "material_appearance": "metal",
             "collision_intent": "preserve functional bores and independently moving parts",
             "dimensions_m": dict(zip("xyz", map(_quantity, sizes[name]), strict=True)),
             "mass_kg": _quantity(masses[name]), "evidence_refs": [PRIOR]} for name in poses
        ],
        "part_instances": [{"name": name, "definition_id": name,
                            "parent": "base" if not is_screw and name == "cover" else None}
                           for name in poses],
        "interfaces": interfaces, "mates": mates, "joints": joints,
        "states": [{"id": "assembled_reference", "role": "reference", "description": "Declared initial pose",
                    "part_poses": {name: _frame(position) for name, position in poses.items()},
                    "active_mate_ids": [mate["id"] for mate in mates], "evidence_refs": [PRIOR]}],
        "acceptance": ["Physical behavior requires separate Isaac validation."],
        "evidence": [{"id": PRIOR, "origin": "prior", "description": "Explicit design input"}],
        "assumptions": ["Parametric simulation proxies; smooth bores with no physical thread surfaces."]
                       if is_screw else ["Ideal revolute joint with declared angular limits."],
    })
    recipe = GeometryRecipe.model_validate({"object_id": ident, "solids": solids,
                                            "part_masses_kg": masses, "joint_placements": placements})
    recipe.check_against(blueprint, {PRIOR})
    return blueprint, recipe, poses


def build_declared(design, task, output, *, blender, usd_python, timeout_seconds):
    design = DESIGN.validate_python(design)
    blueprint, recipe, poses = make_blueprint(design, task)
    rules = [dict(row, cover="cover") if row["rule_ref"] == THREAD_RULE else row
             for row in compile_mates(blueprint)]
    threads = [row for row in rules if row["rule_ref"] == THREAD_RULE]
    clearance = next((row["radial_clearance_m"] for row in rules
                      if row["rule_ref"] == CLEARANCE_RULE), None)
    contact_offset = min(.00005, clearance / 4) if clearance is not None else None
    output.mkdir(parents=True, exist_ok=False)
    build = output / "built"
    build.mkdir()
    write_json(build / "blueprint.json", blueprint.model_dump(mode="json"))
    write_json(build / "recipe.json", recipe.model_dump(mode="json"))
    write_json(output / "design.json", design.model_dump(mode="json"))
    rules_path = output / "assembly_rules.json"
    runtime = Path(__file__).parents[1] / "execution/isaac_thread.py"
    write_json(rules_path, {"schema": "envcreator.assembly-rules/1", "rules": rules,
                            "blueprint": blueprint.model_dump(mode="json"),
                            "contact_offset_m": contact_offset,
                            "runtime": "isaac-thread/1" if threads else "isaac-native-joints",
                            "runtime_source_sha256": sha256(runtime) if threads else None,
                            "physics_status": "not_run"})
    visuals = build_blender_meshes(Path(__file__).with_name("blender_recipe.py"), build / "visual",
                                   parameters={**recipe.model_dump(mode="json"), "preview_offsets": poses,
                                               "explicit_collision": True},
                                   blender=blender, timeout=timeout_seconds)
    candidate = author_usd(build, usd_python, recipe, visuals, poses,
                           joints=recipe_joints(blueprint, recipe), threads=threads,
                           ground=True, contact_offset_m=contact_offset,
                           timeout_seconds=timeout_seconds)
    delivery = output / "delivery.json"
    write_json(delivery, {"status": "unverified", "task": task, "scene_usd": candidate["usd"],
                          "scene_id": design.kind + "-" + sha256(rules_path)[:12],
                          "assembly_rules": rules_path.name, "assembly_rules_sha256": sha256(rules_path),
                          "physics": {"status": "not_run"}})
    export_bundle(delivery, output / "bundle", usd_python, assembly_rules=rules_path)
