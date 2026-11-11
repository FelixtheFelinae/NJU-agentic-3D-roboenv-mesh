"""Optional local 2D background completion. No credentials, depth or physics inference."""

import subprocess
import sys
import time
import tomllib
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from pydantic import Field, model_validator

from ..contracts import Contract
from ..io import read_json, relative_file, sha256, write_json
from .common import new_run, read_mask


class BackgroundGenerationConfig(Contract):
    model_path: Path
    prompt: str = Field(min_length=3, max_length=3000)
    negative_prompt: str = "laptop, computer, bottle, mouse, tools, people, text, watermark"
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    width: int = Field(default=768, ge=256, le=1536)
    height: int = Field(default=512, ge=256, le=1024)
    padding_fraction: float = Field(default=0.15, ge=0, le=0.4)
    steps: int = Field(default=30, ge=1, le=100)
    guidance_scale: float = Field(default=7.5, ge=1, le=20)
    seam_pixels: int = Field(default=8, ge=0, le=64)
    cpu_offload: bool = False
    device: str = Field(default="cuda", pattern=r"^(cuda(:[0-9]+)?|cpu)$")
    timeout_seconds: int = Field(default=600, ge=10, le=3600)

    @model_validator(mode="after")
    def compatible_size(self):
        if self.width % 8 or self.height % 8:
            raise ValueError("background dimensions must be multiples of 8")
        return self


def load_background_generation_config(path: Path) -> BackgroundGenerationConfig:
    with path.open("rb") as stream:
        config = BackgroundGenerationConfig.model_validate(tomllib.load(stream))
    if not config.model_path.is_absolute():
        config.model_path = (path.parent / config.model_path).resolve()
    return config


def prepare_canvas(image: Image.Image, observed: np.ndarray,
                   config: BackgroundGenerationConfig) -> dict:
    """Black is generated; an eroded observed interior is protected pixel-for-pixel."""
    if observed.shape != (image.height, image.width) or not observed.any():
        raise ValueError("observed mask must match the image and contain real background")
    size = (config.width, config.height)
    padding = config.padding_fraction
    fit = ImageOps.contain(image.convert("RGB"),
                           (max(1, int(size[0] * (1 - 2 * padding))),
                            max(1, int(size[1] * (1 - 2 * padding)))))
    offset = ((size[0] - fit.width) // 2, (size[1] - fit.height) // 2)
    mask = Image.fromarray(observed.astype(np.uint8) * 255).resize(fit.size, Image.Resampling.NEAREST)
    canvas = Image.new("RGB", size, (127, 127, 127))
    # Do not leak excluded foreground appearance into the model's context.
    canvas.paste(fit, offset, mask)
    known = Image.new("L", size, 0)
    known.paste(mask, offset)
    interior = known.filter(ImageFilter.MinFilter(config.seam_pixels * 2 + 1)) if config.seam_pixels else known
    known_arr = np.asarray(known) > 0
    protected = np.asarray(interior) > 0
    if not protected.any():
        raise ValueError("no protected background remains; reduce seam_pixels or choose another frame")
    generate = ~known_arr
    if not generate.any():
        raise ValueError("nothing to generate; supply missing pixels or nonzero padding")
    seam = known_arr & ~protected
    alpha = known.filter(ImageFilter.GaussianBlur(max(1, config.seam_pixels / 2)))
    weight = np.asarray(alpha, dtype=np.float32) / 255
    weight[protected] = 1
    weight[generate] = 0
    # Provenance: 0=generated, 1=observed/protected, 2=blended seam.
    provenance = np.zeros((size[1], size[0]), dtype=np.uint8)
    provenance[protected] = 1
    provenance[seam] = 2
    return {"image": canvas, "mask": Image.fromarray((~protected).astype(np.uint8) * 255),
            "protected": protected, "blend_weight": weight, "provenance": provenance,
            "placement": {"offset_xy": list(offset), "resized_source_wh": list(fit.size),
                          "original_source_wh": list(image.size)}}


def select_background_frame(prepared: Path, frame_id: str | None = None) -> tuple[dict, Path, Path]:
    frames = read_json(prepared / "background.json")["frames"]
    if frame_id:
        selected = [frame for frame in frames if frame["frame_id"] == frame_id]
        if len(selected) != 1:
            raise ValueError(f"expected one background frame: {frame_id}")
        frame = selected[0]
    else:
        frame = max(frames, key=lambda row: row.get("observed_pixels", 0))
    return (frame, relative_file(prepared, frame["image_ref"]),
            relative_file(prepared, frame["observed_mask_ref"]))


def generate_background(image_path: Path, observed_path: Path, output: Path,
                        config: BackgroundGenerationConfig) -> dict:
    """Run diffusion in a separate process; the parent never imports generation dependencies."""
    sources = {"image": {"path": str(image_path.resolve()), "sha256": sha256(image_path)},
               "observed_mask": {"path": str(observed_path.resolve()), "sha256": sha256(observed_path)}}
    with new_run(output, "local_background_generation", sources):
        write_json(output / "config.json", config.model_dump(mode="json"))
        with Image.open(image_path) as image:
            canvas = prepare_canvas(image, read_mask(observed_path, image.size), config)
        canvas["image"].save(output / "input.png")
        canvas["mask"].save(output / "generate_mask.png")
        Image.fromarray(canvas["provenance"]).save(output / "provenance.png")
        np.save(output / "blend_weight.npy", canvas["blend_weight"])
        write_json(output / "placement.json", canvas["placement"])
        started = time.monotonic()
        try:
            with (output / "generation.log").open("w") as log:
                process = subprocess.run(
                    [sys.executable, "-m", "roboenv_perception.construction.background_generation_worker",
                     "--run", str(output.resolve())], stdout=log, stderr=subprocess.STDOUT,
                    timeout=config.timeout_seconds, check=False,
                )
            if process.returncode:
                raise RuntimeError(f"local background worker exited {process.returncode}; see generation.log")
            report = read_json(output / "generation.json")
            if report.get("status") != "generated":
                raise RuntimeError("background worker did not report a generated result")
            raw = np.asarray(Image.open(output / "raw.png").convert("RGB"))
            original = np.asarray(canvas["image"])
            if raw.shape != original.shape:
                raise ValueError("generated image size differs from the requested canvas")
            alpha = canvas["blend_weight"][..., None]
            combined = np.rint(original * alpha + raw * (1 - alpha)).astype(np.uint8)
            Image.fromarray(combined).save(output / "background.png")
            protected = canvas["protected"]
            report.update(
                background="background.png", provenance="provenance.png",
                provenance_labels={"0": "generated", "1": "observed_resized_protected", "2": "blended_seam"},
                protected_pixels=int(protected.sum()),
                protected_pixels_unchanged=bool(np.array_equal(combined[protected], original[protected])),
                input_sha256=sources["image"]["sha256"], mask_sha256=sources["observed_mask"]["sha256"],
                output_sha256=sha256(output / "background.png"), elapsed_seconds=time.monotonic() - started,
                sources=sources, appearance_only=True, projection="perspective_image_not_panorama",
                physics="unchanged; no depth, mesh, collision or lighting generated",
                visual_review="pending", scene_loading="not_run",
            )
            if not report["protected_pixels_unchanged"]:
                raise RuntimeError("background generation modified protected pixels")
            write_json(output / "generation.json", report)
        except BaseException as error:
            write_json(output / "generation.json", {"status": "failed", "error": f"{type(error).__name__}: {error}",
                                                      "sources": sources, "appearance_only": True})
            raise
    return report


def complete_prepared_background(prepared: Path, output: Path, config: BackgroundGenerationConfig,
                                 frame_id: str | None = None) -> dict:
    frame, image, mask = select_background_frame(prepared, frame_id)
    report = generate_background(image, mask, output, config)
    # Frame selection is part of the source snapshot; do not mutate the immutable generation run.
    return {**report, "output": str(output.resolve()), "source_frame_id": frame["frame_id"]}
