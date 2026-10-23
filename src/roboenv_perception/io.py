import hashlib
import json
import tomllib
from pathlib import Path

from PIL import Image, ImageOps

from .contracts import PipelineConfig


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(path: Path) -> PipelineConfig:
    with path.open("rb") as handle:
        config = PipelineConfig.model_validate(tomllib.load(handle))
    for obj, name in [
        (config.vlm, "replay_dir"),
        (config.sam, "replay_manifest"),
        (config.sam, "checkpoint"),
        (config.sam, "grounding_model_path"),
        (config.video, "replay_manifest"),
    ]:
        value = getattr(obj, name)
        if value and not Path(value).is_absolute():
            setattr(obj, name, str((path.parent / value).resolve()))
    return config


def normalize_image(source: Path, destination: Path):
    with Image.open(source) as raw:
        raw_size = list(raw.size)
        orientation = raw.getexif().get(274, 1)
        image = ImageOps.exif_transpose(raw).convert("RGB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
    return image, {
        "raw_size": raw_size,
        "exif_orientation": orientation,
        "operation": "PIL.ImageOps.exif_transpose + RGB",
        "normalized_size": list(image.size),
        "mask_coordinate_space": "inputs/frame.png",
        "bbox_convention": "xyxy pixel-edge coordinates; x1/y1 exclusive",
    }


def relative_file(root: Path, reference: str) -> Path:
    """Artifact references may not escape their bundle."""
    path = (root / reference).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"artifact path escapes bundle: {reference}")
    return path
