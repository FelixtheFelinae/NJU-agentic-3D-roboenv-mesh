"""Explicit evidence, design and reconstruction contracts; unknown values remain unknown."""

import math
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Contract, VLMConfig

VERSION = "roboenv.construction.v1"
Name = str


class EvidenceConfig(Contract):
    max_views: int = Field(default=6, ge=1, le=12)
    min_visible_pixels: int = Field(default=256, ge=1)
    min_time_gap_seconds: float = Field(default=0.5, ge=0)
    crop_padding: int = Field(default=24, ge=0)


class DesignConfig(Contract):
    max_revisions: int = Field(default=2, ge=0, le=5)
    measurements: str = ""


class BackgroundConfig(Contract):
    boundary_margin_pixels: int = Field(default=8, ge=0, le=128)
    sfm_mask_mode: Literal["conservative", "all"] = "conservative"
    holdout_every: int = Field(default=8, ge=2)
    synthetic_weight: float = Field(default=0.15, gt=0, lt=1)
    colmap: str = "colmap"
    matching: Literal["sequential", "exhaustive"] = "sequential"
    camera_model: Literal["SIMPLE_RADIAL", "PINHOLE", "OPENCV"] = "SIMPLE_RADIAL"
    single_camera: bool = True
    use_gpu_features: bool = True
    gaussian_python: str = "python"
    gaussian_repo: str = ""
    iterations: int = Field(default=10000, ge=1)
    training_mode: Literal["fixed", "adaptive"] = "adaptive"
    ssim_weight: float = Field(default=0.2, ge=0, le=1)
    densify_from_iter: int = Field(default=500, ge=0)
    densify_until_iter: int = Field(default=7000, ge=1)
    densification_interval: int = Field(default=100, ge=1)
    densify_grad_threshold: float = Field(default=0.0002, gt=0)
    opacity_reset_interval: int = Field(default=3000, ge=1)
    max_gaussians: int = Field(default=250000, ge=4)
    checkpoint_iterations: list[int] = Field(default_factory=lambda: [3000, 7000])
    timeout_seconds: int = Field(default=14400, ge=1)

    @model_validator(mode="after")
    def valid_training_schedule(self):
        if self.densify_until_iter <= self.densify_from_iter:
            raise ValueError("densify_until_iter must exceed densify_from_iter")
        if any(step < 1 for step in self.checkpoint_iterations):
            raise ValueError("checkpoint iterations must be positive")
        if len(set(self.checkpoint_iterations)) != len(self.checkpoint_iterations):
            raise ValueError("checkpoint iterations must be unique")
        return self


class ConstructionConfig(Contract):
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    evidence: EvidenceConfig = Field(default_factory=EvidenceConfig)
    design: DesignConfig = Field(default_factory=DesignConfig)
    background: BackgroundConfig = Field(default_factory=BackgroundConfig)


def load_construction_config(path: Path) -> ConstructionConfig:
    with path.open("rb") as handle:
        config = ConstructionConfig.model_validate(tomllib.load(handle))
    for obj, key in [
        (config.vlm, "replay_dir"),
        (config.design, "measurements"),
        (config.background, "gaussian_repo"),
    ]:
        value = getattr(obj, key)
        if value and not Path(value).is_absolute():
            setattr(obj, key, str((path.parent / value).resolve()))
    for key in ("colmap", "gaussian_python"):
        value = getattr(config.background, key)
        if value and "/" in value and not Path(value).is_absolute():
            setattr(config.background, key, str((path.parent / value).resolve()))
    return config


class Quantity(Contract):
    value: float | None = None
    source: Literal["measured", "estimated", "assumed", "unknown"] = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str = ""

    @model_validator(mode="after")
    def evidence_required(self):
        if (self.value is None) != (self.source == "unknown"):
            raise ValueError("null values require unknown source; numeric values need a source")
        if self.source != "unknown" and not self.evidence_refs:
            raise ValueError("numeric values must reference observations or measurements")
        return self


class Dimensions(Contract):
    x: Quantity = Field(default_factory=Quantity)
    y: Quantity = Field(default_factory=Quantity)
    z: Quantity = Field(default_factory=Quantity)

    @model_validator(mode="after")
    def positive(self):
        if any(q.value is not None and q.value <= 0 for q in (self.x, self.y, self.z)):
            raise ValueError("dimensions must be positive metres")
        return self


class AssetBrief(Contract):
    object_id: str
    category: str
    summary: str
    build_route: Literal["rigid", "articulated", "background", "exclude", "unresolved"]
    support_mode: Literal["grounded", "external_mount", "mobile", "unknown"]
    key_interactions: list[str] = Field(default_factory=list)
    observed_motion: list[str] = Field(default_factory=list)
    reference_interpretation: Literal["viewpoints", "motion_states", "mixed", "single"]
    assumptions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    evidence_refs: list[str]


class Part(Contract):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    parent: str | None
    role: str
    geometry: str
    hollow: bool = False
    dimensions_m: Dimensions = Field(default_factory=Dimensions)
    material_appearance: str
    collision_intent: str
    evidence_refs: list[str]


class Joint(Contract):
    name: str
    parent: str
    child: str
    type: Literal["fixed", "revolute", "prismatic", "continuous"]
    axis_child: tuple[float, float, float] | None = None
    origin_hint: str
    travel: Quantity = Field(default_factory=Quantity)
    rest: Literal["at_min", "centered", "at_max"] | None = None
    evidence_kind: Literal["observed", "inferred", "assumed"]
    evidence_refs: list[str]

    @model_validator(mode="after")
    def legal_motion(self):
        if self.type != "fixed":
            if self.axis_child is None or not math.isclose(
                sum(v * v for v in self.axis_child), 1.0, abs_tol=1e-4
            ):
                raise ValueError("moving joint needs a unit axis in child coordinates")
        if self.type in {"revolute", "prismatic"}:
            if self.rest is None:
                raise ValueError("bounded motion needs a rest convention")
            if self.travel.value is not None and self.travel.value <= 0:
                raise ValueError("travel must be positive radians or metres")
        elif self.travel.value is not None or self.rest is not None:
            raise ValueError("fixed/continuous joints do not have bounded travel/rest")
        if self.evidence_kind == "observed" and not self.evidence_refs:
            raise ValueError("observed motion needs reference evidence")
        return self


class Blueprint(Contract):
    schema_version: Literal["roboenv.construction.v1"] = VERSION
    object_id: str
    kind: Literal["rigid", "articulated"]
    frame_convention: Literal["metres; Z-up; front=+Y; local object frame"]
    target_dimensions_m: Dimensions = Field(default_factory=Dimensions)
    parts: list[Part] = Field(min_length=1)
    joints: list[Joint] = Field(default_factory=list)
    acceptance: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_tree(self):
        names = [p.name for p in self.parts]
        if len(set(names)) != len(names):
            raise ValueError("part names must be unique")
        parents = {p.name: p.parent for p in self.parts}
        if sum(p is None for p in parents.values()) != 1:
            raise ValueError("exactly one root part is required")
        for name in names:
            seen = set()
            current = name
            while current is not None:
                if current in seen or current not in parents:
                    raise ValueError("invalid or cyclic part hierarchy")
                seen.add(current)
                current = parents[current]
        joint_names, children = set(), set()
        for joint in self.joints:
            if joint.name in joint_names or joint.child in children:
                raise ValueError("duplicate joint name or multiple joints for one child")
            if joint.child not in parents or parents[joint.child] != joint.parent:
                raise ValueError("joint must follow the declared part hierarchy")
            joint_names.add(joint.name)
            children.add(joint.child)
        if children != {p.name for p in self.parts if p.parent is not None}:
            raise ValueError("every non-root part requires one explicit joint")
        moving = any(j.type != "fixed" for j in self.joints)
        if (self.kind == "articulated") != moving:
            raise ValueError("rigid assets allow no moving joints; articulated assets need one")
        return self


class DesignReview(Contract):
    blueprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["PASS", "REDO"]
    findings: list[str]
    summary: str

    @model_validator(mode="after")
    def consistent(self):
        if (self.decision == "PASS") != (not self.findings):
            raise ValueError("PASS requires empty findings; REDO requires concrete findings")
        return self


class Measurement(Contract):
    object_id: str
    dimensions_m: tuple[float, float, float]
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def positive(self):
        if any(v <= 0 for v in self.dimensions_m):
            raise ValueError("measured dimensions must be positive")
        return self


class Measurements(Contract):
    objects: list[Measurement]

    @model_validator(mode="after")
    def unique(self):
        if len({m.object_id for m in self.objects}) != len(self.objects):
            raise ValueError("duplicate measured object")
        return self


class TableView(Contract):
    frame_id: str
    # Ordered corners of the SAME planar patch: (0,0), (W,0), (W,H), (0,H).
    corners_xy: list[tuple[float, float]] = Field(min_length=4, max_length=4)


class TableSpec(Contract):
    width_m: float = Field(gt=0)
    depth_m: float = Field(gt=0)
    pixels_per_metre: int = Field(default=1000, ge=16, le=8192)
    views: list[TableView] = Field(min_length=1)
    # Optional, additional exclusion masks in distorted/original frame coordinates.
    extra_exclusions: dict[str, str] = Field(default_factory=dict)
    fill: Literal["none", "telea", "patch"] = "none"
    fusion: Literal["mean", "median"] = "mean"
    patch_size: int = Field(default=41, ge=3, le=129)
    max_fill_fraction: float = Field(default=0.25, ge=0, le=1)

    @model_validator(mode="after")
    def unique(self):
        if self.patch_size % 2 != 1:
            raise ValueError("patch_size must be odd")
        if len({v.frame_id for v in self.views}) != len(self.views):
            raise ValueError("duplicate table frame")
        if self.width_m * self.depth_m * self.pixels_per_metre**2 > 32_000_000:
            raise ValueError("table atlas exceeds the 32 megapixel memory limit")
        return self
