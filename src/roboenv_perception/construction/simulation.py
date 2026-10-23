"""Prepare a portable tabletop simulation job; rendering never calibrates physical scale."""

from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from ..contracts import Contract
from ..io import read_json, write_json
from .common import copy_ref, new_run, source_identity, verify_run


class TableSimulationSpec(Contract):
    width_m: float = Field(default=0.8, gt=0.1, le=5)
    width_source: Literal["assumed", "estimated", "measured"] = "assumed"
    width_evidence: str = "Provisional workspace size; no reference-object calibration yet."
    top_height_m: float = Field(default=0.75, gt=0.1, le=2)
    height_source: Literal["task_design", "estimated", "measured"] = "task_design"
    thickness_m: float = Field(default=0.03, gt=0.001, le=0.2)
    near_edge_x_m: float = Field(default=0.20, ge=0.05, le=2)
    static_friction: float = Field(default=0.6, ge=0, le=2)
    dynamic_friction: float = Field(default=0.5, ge=0, le=2)
    restitution: float = Field(default=0, ge=0, le=1)
    contact_offset_m: float = Field(default=0.002, gt=0, le=0.02)
    rest_offset_m: float = Field(default=0, ge=0, le=0.01)
    dt_seconds: float = Field(default=1 / 240, gt=0, le=1 / 60)
    solver_position_iterations: int = Field(default=8, ge=4, le=64)
    solver_velocity_iterations: int = Field(default=2, ge=1, le=32)
    texture: Literal["simple", "patch"] = "simple"
    robot: Literal["unitree_g1"] = "unitree_g1"

    @model_validator(mode="after")
    def coherent(self):
        if self.thickness_m >= self.top_height_m:
            raise ValueError("table thickness must be smaller than top height")
        if self.dynamic_friction > self.static_friction:
            raise ValueError("dynamic friction must not exceed static friction")
        if self.rest_offset_m >= self.contact_offset_m:
            raise ValueError("rest offset must be below contact offset")
        if not self.width_evidence.strip():
            raise ValueError("width needs a provenance explanation")
        return self


def tabletop_geometry(plane: dict, spec: TableSimulationSpec):
    """Map the reconstructed plane to a right-handed G1 task frame with uniform scale."""
    u, v, n = (np.asarray(plane[k], dtype=np.float64) for k in ("u", "v", "normal"))
    rotation = np.stack([v, u, n])
    if not np.isfinite(rotation).all() or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-5):
        raise ValueError("table basis must be finite and orthonormal")
    if np.linalg.det(rotation) < 0:
        rotation[0] *= -1
    width_sfm, depth_sfm = plane["width_sfm"], plane["depth_sfm"]
    if not np.isfinite([width_sfm, depth_sfm]).all() or min(width_sfm, depth_sfm) <= 0:
        raise ValueError("table dimensions must be positive finite SfM lengths")
    scale = spec.width_m / width_sfm
    depth_m = depth_sfm * scale
    corners = np.asarray(plane["world_corners_sfm"], dtype=np.float64)
    if corners.shape != (4, 3) or not np.isfinite(corners).all():
        raise ValueError("four finite tabletop corners required")
    source_center = corners.mean(axis=0)
    local = (corners - source_center) @ rotation.T * scale
    expected_extents = np.array([depth_m, spec.width_m, 0])
    if not np.allclose(np.ptp(local, axis=0), expected_extents, atol=1e-5):
        raise ValueError("table corners disagree with fitted basis or dimensions")
    if not np.allclose(local[:, 2], 0, atol=1e-5):
        raise ValueError("tabletop corners must lie on the declared plane")
    center = np.array([spec.near_edge_x_m + depth_m / 2, 0, spec.top_height_m])
    transform = np.eye(4)
    transform[:3, :3] = scale * rotation
    transform[:3, 3] = center - scale * rotation @ source_center
    return {
        "vertices_local_m": local.tolist(),
        "uv": [[0, 1], [1, 1], [1, 0], [0, 0]],
        "width_m": spec.width_m,
        "depth_m": float(depth_m),
        "top_height_m": spec.top_height_m,
        "thickness_m": spec.thickness_m,
        "position_world_m": center.tolist(),
        "collision_size_m": [float(depth_m), spec.width_m, spec.thickness_m],
        "collision_center_local_m": [0, 0, -spec.thickness_m / 2],
        "sfm_to_sim": transform.tolist(),
        "metres_per_sfm_unit": float(scale),
        "scale_source": spec.width_source,
        "scale_evidence": spec.width_evidence,
        "height_source": spec.height_source,
        "world_frame": "metres; +Z up, +X forward from G1, +Y robot left; floor Z=0",
        "robot_pose_alignment": "task layout; not real-robot extrinsic calibration",
    }


def prepare_table_simulation(table: Path, spec_path: Path, output: Path):
    plane = verify_run(table, "table-plane.json")
    spec = TableSimulationSpec.model_validate(read_json(spec_path))
    geometry = tabletop_geometry(plane, spec)
    with new_run(output, "table_simulation_input", source_identity(table)):
        copy_ref(table, f"assets/{spec.texture}/table.png", output, "asset/table.png")
        write_json(output / "spec.json", spec.model_dump(mode="json"))
        write_json(output / "geometry.json", geometry)
        write_json(output / "blueprint.json", {
            "kind": "static_support_surface", "parts": ["tabletop", "four_assumed_legs"],
            "support_mode": "static", "joints": [], "mass_kg": None,
            "mass_rationale": "Static collider; tabletop mass/inertia do not enter dynamics.",
            "parameter_provenance": {
                "width": spec.width_source, "depth": "reconstructed aspect ratio times chosen width scale",
                "height": spec.height_source, "thickness": "assumed", "legs": "assumed",
                "friction": "assumed moderate pair coefficients; not inferred from RGB",
                "restitution": "assumed non-bouncy contact", "texture": "synthetic appearance",
            },
            "acceptance": [
                "Visual top and collision top coincide in the exported asset.",
                "Nine cubes dropped across the tabletop establish persistent contact and settle.",
                "A subthreshold horizontal force does not cause persistent sliding.",
                "A superthreshold horizontal force causes sliding without passing through the table.",
            ],
            "artisan_reference": "blueprint -> deterministic build -> simulation evidence -> review/repair",
            "independent_review": "not_run; no Artisan agents invoked",
        })
        write_json(output / "job.json", {
            "spec_ref": "spec.json", "geometry_ref": "geometry.json",
            "texture_ref": "asset/table.png", "asset_to_build": "asset/table.usda",
            "runtime": "Isaac Sim 5.1 / Isaac Lab 2.3",
            "scale_is_measured": spec.width_source == "measured",
            "robot_reachability": "requires separate kinematic check; total height alone is insufficient",
            "physics_validation": "not_run", "independent_accuracy": None,
        })
    return output
