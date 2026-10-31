"""Isolated, local-files-only diffusion worker; exits to release model memory."""

import argparse
import importlib.metadata
import time
from pathlib import Path

from ..io import read_json, sha256, write_json
from .background_generation import BackgroundGenerationConfig


def run(root: Path):
    import torch
    from diffusers import StableDiffusionInpaintPipeline
    from PIL import Image

    config = BackgroundGenerationConfig.model_validate(read_json(root / "config.json"))
    model = config.model_path.resolve(strict=True)
    if not (model / "model_index.json").is_file():
        raise ValueError("model_path must be an installed local Diffusers model directory")
    if config.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable; no silent CPU fallback")
    torch.set_num_threads(4)
    dtype = torch.float16 if config.device.startswith("cuda") else torch.float32
    if config.device.startswith("cuda"):
        torch.cuda.set_device(torch.cuda.current_device() if config.device == "cuda" else config.device)
        torch.cuda.reset_peak_memory_stats()
    # Hash actual loaded local files, not a remote model alias. Never auto-download weights.
    model_files = {p.relative_to(model).as_posix(): sha256(p) for p in sorted(model.rglob("*"))
                   if p.is_file() and ".cache" not in p.relative_to(model).parts
                   and (p.suffix in {".json", ".safetensors", ".txt"})}
    write_json(root / "model_files.json", model_files)
    started = time.monotonic()
    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        str(model), torch_dtype=dtype, variant="fp16", use_safetensors=True, local_files_only=True,
    )
    if config.cpu_offload and config.device.startswith("cuda"):
        pipe.enable_model_cpu_offload(device=config.device)
    else:
        pipe.to(config.device)
    with torch.inference_mode():
        result = pipe(
            prompt=config.prompt, negative_prompt=config.negative_prompt,
            image=Image.open(root / "input.png").convert("RGB"),
            mask_image=Image.open(root / "generate_mask.png").convert("L"),
            width=config.width, height=config.height,
            num_inference_steps=config.steps, guidance_scale=config.guidance_scale,
            generator=torch.Generator(device="cpu").manual_seed(config.seed),
        )
    if result.nsfw_content_detected and any(result.nsfw_content_detected):
        raise RuntimeError("generation rejected by the model's content checker")
    result.images[0].save(root / "raw.png")
    gpu = config.device.startswith("cuda")
    report = {
        "status": "generated", "backend": "diffusers.StableDiffusionInpaintPipeline",
        "model_path": str(model), "model_files_sha256": sha256(root / "model_files.json"),
        "model_metadata": read_json(model / "envcreator-model.json") if (model / "envcreator-model.json").is_file() else None,
        "versions": {name: importlib.metadata.version(name) for name in
                     ("torch", "diffusers", "transformers", "accelerate", "safetensors")},
        "device": torch.cuda.get_device_name() if gpu else "cpu",
        "dtype": str(dtype), "seed": config.seed, "size_wh": [config.width, config.height],
        "steps": config.steps, "cpu_offload": config.cpu_offload,
        "inference_and_load_seconds": time.monotonic() - started,
        "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20 if gpu else None,
        "peak_reserved_mib": torch.cuda.max_memory_reserved() / 2**20 if gpu else None,
        "credentials": "none; local_files_only=True", "model_released": "worker exits after saving",
    }
    write_json(root / "generation.json", report)
    print("BACKGROUND_GENERATED", report["size_wh"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    run(parser.parse_args().run)
