"""Reference-informed electric screwdriver; explicit geometry and interaction assumptions."""

import math
import subprocess
from pathlib import Path

from ..construction.asset_agents import child_environment
from ..construction.contracts import Blueprint
from ..harness.geometry import GeometryRecipe, build_recipe
from ..io import read_json, sha256, write_json

REFERENCE_URL = "https://www.bosch-professional.com/gb/en/products/bosch-go-gen3-06019H2201"
REFERENCE_IMAGE = "https://www.bosch-professional.com/binary/ocsmedia/optimized/750x422/o428771v54_Bosch_GO_3_SOS_06019H2201.png"
REFS = {"user:electric-tool", "reference:bosch-go", "assumption:tool-v1"}
AXIAL_ROTATION = (-math.pi / 2, 0, 0)


def specification() -> tuple[Blueprint, GeometryRecipe, dict]:
    """Codex-authored first candidate; no claim of an autonomous VLM reconstruction run."""
    evidence = ["reference:bosch-go", "assumption:tool-v1"]
    blueprint = Blueprint.model_validate({
        "object_id": "electric_screwdriver_v1", "kind": "articulated",
        "frame_convention": "metres; Z-up; front=+Y; local object frame",
        "parts": [
            {"name": "housing", "parent": None, "role": "held body, battery and gearbox",
             "geometry": "contoured straight handle with rubber panels, clutch ring and open nose",
             "material_appearance": "blue polymer, dark elastomer grip, red switches",
             "collision_intent": "graspable exterior; nose bore remains open", "evidence_refs": evidence},
            {"name": "spindle", "parent": "housing", "role": "rotating holder and installed hex bit",
             "geometry": "steel shaft, 6.35 mm across-flats shank, 3 mm across-flats hex tip",
             "material_appearance": "steel holder and bit",
             "collision_intent": "separate rotating collision body and hex contact tip", "evidence_refs": evidence},
        ],
        "joints": [{"name": "spindle_spin", "parent": "housing", "child": "spindle",
                    "type": "continuous", "axis_child": [0, 1, 0],
                    "effort_limit": {"value": .2, "source": "assumed", "evidence_refs": ["assumption:tool-v1"]},
                    "velocity_limit": {"value": 6 * math.pi, "source": "assumed", "evidence_refs": ["assumption:tool-v1"]},
                    "origin_hint": "coaxial bearing in open front nose; +Y points toward screw",
                    "evidence_kind": "assumed", "evidence_refs": ["user:electric-tool"]}],
        "acceptance": ["closed positive-volume component meshes", "one continuous spindle joint",
                       "finite motor torque, forward/reverse/brake", "public grip and TCP frames"],
        "assumptions": ["generic Bosch GO-like silhouette, not an exact branded replica",
                        "220 mm overall length and 42 mm maximum diameter are estimates",
                        "320 g total mass is a modeling prior; battery and gearbox merged into housing",
                        "installed 3 mm hex bit is a selectable hypothesis, not matched to the cart",
                        "electrical trigger bypassed by an explicit robot tool command"],
        "unresolved": ["G1 grasp and slip resistance", "cart screw drive size and fit",
                       "real motor curve, battery, clutch and thermal behavior"],
    })
    solids = []

    def solid(name, shape, *, part="housing", color=(0.025, 0.25, 0.42, 1), **kw):
        material = "steel" if part == "spindle" or color[:3] == (.48, .52, .57) else (
            "rubber" if max(color[:3]) < .1 else "matte_plastic")
        solids.append({"name": name, "part": part, "shape": shape, "rgba": color,
                       "material_class": material,
                       "source": "estimated", "evidence_refs": evidence, **kw})

    rubber = (0.035, 0.043, 0.05, 1)
    metal = (0.48, 0.52, 0.57, 1)
    red = (0.78, 0.07, 0.035, 1)
    solid("body_shell", "lathe", rotation_rpy=AXIAL_ROTATION, sides=48,
          profile_m=[(-.09, .013), (-.087, .017), (-.08, .019), (-.055, .020),
                     (.025, .019), (.045, .018), (.055, .020), (.065, .020)])
    for side in (-1, 1):
        solid("grip_left" if side < 0 else "grip_right", "ellipsoid", color=rubber,
              center_m=(side * .017, -.022, 0), size_m=(.007, .113, .027))
    solid("rear_bumper", "lathe", color=rubber, rotation_rpy=AXIAL_ROTATION,
          sides=48, profile_m=[(-.09, .013), (-.087, .0172), (-.078, .0192)])
    solid("trigger", "box", color=red, center_m=(0, .003, .019), size_m=(.012, .027, .005))
    solid("direction_switch", "box", color=rubber, center_m=(0, -.038, .019),
          size_m=(.009, .016, .004))
    solid("direction_marker", "box", color=red, center_m=(0, -.038, .021),
          size_m=(.003, .006, .001))
    solid("charge_port_mark", "box", color=(.008, .01, .012, 1),
          center_m=(0, -.0894, 0), size_m=(.009, .0015, .0035))
    solid("clutch_collar", "annulus", color=rubber, center_m=(0, .073, 0),
          rotation_rpy=AXIAL_ROTATION, radius_m=.021, inner_radius_m=.006, length_m=.020)
    solid("shaft_bearing", "annulus", color=metal, center_m=(0, .073, 0),
          rotation_rpy=AXIAL_ROTATION, radius_m=.0065, inner_radius_m=.00455, length_m=.012)
    for i in range(12):
        angle = i * math.tau / 12
        solid(f"collar_ridge_{i:02d}", "box", color=(.06, .07, .08, 1),
              center_m=(.0205 * math.cos(angle), .073, .0205 * math.sin(angle)),
              rotation_rpy=(0, -angle, 0), size_m=(.0018, .015, .002))
    solid("nose_ring", "annulus", color=metal, center_m=(0, .084, 0),
          rotation_rpy=AXIAL_ROTATION, radius_m=.019, inner_radius_m=.006, length_m=.002)
    solid("work_light_ring", "annulus", color=(.80, .85, .75, 1), center_m=(0, .0853, 0),
          rotation_rpy=AXIAL_ROTATION, radius_m=.0165, inner_radius_m=.013, length_m=.0008)
    # Spindle geometry is relative to the joint origin, at housing Y=80 mm.
    solid("rotor_shaft", "cylinder", part="spindle", color=metal,
          center_m=(0, -.003, 0), rotation_rpy=AXIAL_ROTATION, radius_m=.0045, length_m=.022)
    solid("bit_holder", "lathe", part="spindle", color=rubber, rotation_rpy=AXIAL_ROTATION,
          sides=48, profile_m=[(.006, .0048), (.007, .006), (.012, .006), (.014, .0048)])
    shank_radius = .00635 / (2 * math.cos(math.pi / 6))
    solid("hex_shank", "prism", part="spindle", color=metal, sides=6,
          center_m=(0, .020, 0), rotation_rpy=AXIAL_ROTATION, radius_m=shank_radius, length_m=.020)
    solid("hex_working_tip", "lathe", part="spindle", color=metal, sides=6,
          rotation_rpy=AXIAL_ROTATION,
          profile_m=[(.028, shank_radius), (.032, .003 / math.sqrt(3)),
                     (.049, .003 / math.sqrt(3)), (.050, .0027 / math.sqrt(3))])
    recipe = GeometryRecipe.model_validate({
        "object_id": blueprint.object_id, "solids": solids,
        "joint_placements": [{"joint": "spindle_spin", "origin_parent_m": [0, .080, 0],
                              "source": "assumed", "evidence_refs": ["assumption:tool-v1"]}],
        "part_masses_kg": {"housing": .300, "spindle": .020},
        "notes": ["Only housing and spindle are independent rigid bodies.",
                  "Grip patches, switches, LED and charging-port mark are fixed exterior details.",
                  "Open nose has 1.5 mm clearance; assumed sleeve bearing reduces local radial clearance to 50 micrometres.",
                  "Reference packaging dimensions were not used as tool dimensions."],
    })
    recipe.check_against(blueprint, REFS)
    semantics = {
        "schema": "envcreator.power-tool.v1", "tool_id": blueprint.object_id,
        "units": "metres, kilograms, seconds, radians, newtons, newton-metres",
        "source": {"url": REFERENCE_URL, "image_url": REFERENCE_IMAGE,
                   "retrieved_date": "2026-10-28", "design_method": "agent-authored recipe using manufacturer reference",
                   "manufacturer_facts": {"bit_holder": "1/4 inch internal hexagon",
                                          "no_load_rpm_range": [306, 396],
                                          "max_hard_soft_torque_Nm": [5, 2.5],
                                          "listed_weight_excluding_battery_kg": .32},
                   "scope": "shape/interface reference; no claim of exact product dimensions or dynamics"},
        "frames": {"grasp": {"body": "housing", "position_m": [0, -.025, 0],
                              "axis": [0, 1, 0], "usable_axial_interval_m": [-.060, .015],
                              "approx_diameter_m": .040, "status": "candidate_not_grasp_tested"},
                   "tcp": {"body": "spindle", "position_m": [0, .050, 0],
                           "housing_rest_position_m": [0, .130, 0], "forward_axis": [0, 1, 0]},
                   "bearing": {"body": "housing", "position_m": [0, .080, 0]}},
        "bit": {"drive_shape": "external_hex", "tip_across_flats_m": .003,
                "shank_across_flats_m": .00635, "status": "assumed_not_cart_matched",
                "engagement": "requires matching socket; no magnetic or automatic attachment"},
        "motor": {"joint": "spindle_spin", "axis": [0, 1, 0],
                  "default_rpm": 30.0, "max_command_rpm": 180.0, "torque_limit_Nm": .20,
                  "usd_velocity_damping": .05,
                  "command": "set_motor(stage, tool_root, rpm, enabled=True)",
                  "positive_direction": "right-hand rotation about +Y, from handle toward tip",
                  "disabled": "coast: zero drive damping and zero drive maxForce",
                  "brake": "enabled=True and rpm=0: torque-limited active braking",
                  "initial_state": "disabled", "parameter_source": "assumed slow experimental tool",
                  "reaction": "joint drive applies internal equal-and-opposite torque; housing is free"},
        "materials": {"housing_static_dynamic_friction": [.6, .5],
                      "grip_static_dynamic_friction": [.9, .7],
                      "steel_static_dynamic_friction": [.4, .3], "restitution": 0.0,
                      "source": "unmeasured priors"},
        "limitations": blueprint.assumptions + blueprint.unresolved,
        "verification": {"robot_grasp": "not_run", "screw_engagement": "not_run",
                         "fastener_removal": "not_run", "motor_physics": "not_run"},
    }
    return blueprint, recipe, semantics


def build_tool(output: Path, artisan_python: Path, blender: Path | None = None) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    blueprint, recipe, semantics = specification()
    write_json(output / "tool_spec.json", semantics)
    candidate = build_recipe(blueprint, recipe, output / "geometry",
                             artisan_python=artisan_python, blender=blender)
    worker = Path(__file__).with_name("power_tool_usd.py")
    with (output / "tool_export.log").open("w") as log:
        result = subprocess.run([str(artisan_python), str(worker), "--source", candidate["usd"],
                                 "--spec", str(output / "tool_spec.json"), "--output", str(output / "asset")],
                                env=child_environment(), stdout=log, stderr=subprocess.STDOUT,
                                check=False, timeout=180)
    if result.returncode:
        raise RuntimeError(f"power tool export failed; see {output / 'tool_export.log'}")
    exported = read_json(output / "asset/tool.json")
    scene = output / "asset/electric_screwdriver.usda"
    delivery = {"scene_id": "electric-screwdriver-v1", "status": "unverified",
                "scene_usd": str(scene), "scene_sha256": sha256(scene),
                "tool_semantics": exported, "physics_status": "not_run",
                "source_candidate_sha256": candidate["candidate_sha256"],
                "scope": "reference-informed tool asset; no robot grasp or screw removal claim"}
    write_json(output / "delivery.json", delivery)
    return {"delivery": str(output / "delivery.json"), "scene_usd": str(scene),
            "tool_manifest": str(output / "asset/tool.json"),
            "previews": candidate["visual_previews"], "status": "built_unverified"}
