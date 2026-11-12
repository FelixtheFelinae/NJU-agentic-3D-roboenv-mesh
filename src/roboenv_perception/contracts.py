"""Validated contracts shared by API adapters, stages and exported JSON schemas."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "0.2.0"
PROMPT_VERSION = "single-image-v1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Candidate(Contract):
    candidate_id: str = Field(pattern=r"^c[0-9]{3}$")
    category: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=120)
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    expected_count: int | None = Field(default=None, ge=0, le=100)
    critical: bool = False

    @field_validator("bbox_xyxy_norm")
    @classmethod
    def valid_box(cls, value):
        if value is not None:
            x0, y0, x1, y1 = value
            if not all(math.isfinite(x) and 0 <= x <= 1 for x in value):
                raise ValueError("bbox coordinates must be finite and within [0, 1]")
            if x0 >= x1 or y0 >= y1:
                raise ValueError("bbox must have positive width and height")
        return value


class Inventory(Contract):
    scene_description: str
    objects: list[Candidate] = Field(max_length=32)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_candidates(self):
        ids = [c.candidate_id for c in self.objects]
        prompts = [c.prompt.strip().casefold() for c in self.objects]
        if len(ids) != len(set(ids)) or len(prompts) != len(set(prompts)):
            raise ValueError("candidate IDs and normalized concept prompts must be unique")
        if any(not p for p in prompts):
            raise ValueError("empty concept prompt")
        return self


TaskRole = Literal[
    "manipulation_candidate",
    "container",
    "support",
    "obstacle",
    "decoration",
    "transient",
    "unknown",
]
VisualRoute = Literal["mesh_candidate", "gs_background", "exclude_transient", "unresolved"]
PhysicalRole = Literal[
    "rigid_body_candidate", "articulated_part_candidate", "support", "obstacle", "none", "unknown"
]


class InstanceLabel(Contract):
    instance_id: str = Field(pattern=r"^i[0-9]{4}$")
    category: str = Field(min_length=1, max_length=120)
    attributes: dict[str, str] = Field(default_factory=dict)
    task_role: TaskRole = "unknown"
    visual_route: VisualRoute = "unresolved"
    physical_role: PhysicalRole = "unknown"
    parent_instance_id: str | None = Field(default=None, pattern=r"^i[0-9]{4}$")
    occluded: bool = False
    truncated: bool = False
    rationale: str = ""


class Labels(Contract):
    instances: list[InstanceLabel] = Field(max_length=256)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_labels(self):
        ids = [x.instance_id for x in self.instances]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate instance labels")
        return self


class VLMConfig(Contract):
    backend: Literal["http"] = "http"
    style: Literal["chat_completions", "responses", "generic"] = "chat_completions"
    endpoint: str = ""
    model: str = ""
    headers: dict[str, str] = Field(
        default_factory=lambda: {"Authorization": "Bearer ${VLM_API_KEY}"}
    )
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    retries: int = Field(default=1, ge=0, le=3)
    json_mode: bool = False
    extra_body: dict = Field(default_factory=dict)
    request_template: dict = Field(default_factory=dict)
    response_path: str = ""


class SAMConfig(Contract):
    backend: Literal["sam3", "grounded_sam2"] = "sam3"
    checkpoint: str = ""
    device: Literal["cuda"] = "cuda"
    confidence_threshold: float = Field(default=0.5, ge=0, le=1)
    box_fallback: bool = True
    sam2_model_config: str = "configs/sam2.1/sam2.1_hiera_s.yaml"
    grounding_model_path: str = ""  # local Transformers snapshot; never downloads implicitly
    grounding_threshold: float = Field(default=0.3, ge=0, le=1)
    grounding_text_threshold: float = Field(default=0.25, ge=0, le=1)


class ProcessingConfig(Contract):
    min_mask_pixels: int = Field(default=16, ge=1)
    duplicate_iou: float = Field(default=0.95, gt=0, le=1)
    crop_padding: int = Field(default=12, ge=0, le=512)


class VideoConfig(Contract):
    sample_fps: float = Field(default=5.0, gt=0, le=120)
    max_frames: int = Field(default=300, ge=1, le=10000)
    start_seconds: float = Field(default=0, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    reference_frame: int = Field(default=0, ge=0)
    preview_fps: int = Field(default=5, ge=1, le=60)

    @model_validator(mode="after")
    def valid_window(self):
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must exceed start_seconds")
        return self


class PipelineConfig(Contract):
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    sam: SAMConfig = Field(default_factory=SAMConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
