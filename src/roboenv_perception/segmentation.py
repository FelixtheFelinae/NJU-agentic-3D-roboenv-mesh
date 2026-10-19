"""SAM is optional and imported only for live CUDA inference."""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .contracts import Inventory, SAMConfig
from .io import read_json, relative_file, sha256


@dataclass
class Detection:
    candidate_ids: list[str]
    mask: np.ndarray
    score: float
    method: str = "text"
    instance_id: str = ""
    raw_ids: list[str] = field(default_factory=list)
    track_keys: list[str] = field(default_factory=list)


class ReplaySegmenter:
    def __init__(self, config: SAMConfig):
        self.path = Path(config.replay_manifest)
        if not config.replay_manifest or not self.path.is_file():
            raise ValueError("replay SAM requires an existing replay_manifest")

    def segment(self, image: Image.Image, inventory: Inventory):
        manifest = read_json(self.path)
        if manifest.get("image_sha256"):
            import hashlib
            import io

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            if hashlib.sha256(buffer.getvalue()).hexdigest() != manifest["image_sha256"]:
                raise ValueError("replayed masks belong to another normalized image")
        ids = {item.candidate_id for item in inventory.objects}
        detections = []
        for row in manifest["detections"]:
            if row["candidate_id"] not in ids:
                raise ValueError("replay detection references an unknown candidate")
            path = relative_file(self.path.parent, row["mask"])
            with Image.open(path) as mask_image:
                mask = np.array(mask_image.convert("L")) > 0
            detections.append(Detection([row["candidate_id"]], mask, float(row["score"]), "replay"))
        return detections, {
            "backend": "replay",
            "model_executed": False,
            "manifest_sha256": sha256(self.path),
        }


class SAM3Segmenter:
    def __init__(self, config: SAMConfig):
        try:
            import torch
            from sam3.model.sam3_image_processor import Sam3Processor
            from sam3.model_builder import build_sam3_image_model
        except ImportError:
            raise RuntimeError(
                "Live SAM requires PyTorch and official SAM 3 in a CUDA environment; see README"
            ) from None
        if not torch.cuda.is_available():
            raise RuntimeError("Live SAM 3 backend requires CUDA; no fallback to synthetic masks")
        if not config.checkpoint or not Path(config.checkpoint).is_file():
            raise ValueError("sam.checkpoint must point to your downloaded SAM 3 image checkpoint")
        self.config = config
        self.torch = torch
        model = build_sam3_image_model(
            device="cuda", checkpoint_path=config.checkpoint, load_from_HF=False
        )
        self.processor = Sam3Processor(
            model, device="cuda", confidence_threshold=config.confidence_threshold
        )

    def segment(self, image: Image.Image, inventory: Inventory):
        detections = []
        with self.torch.inference_mode():
            state = self.processor.set_image(image)
            for candidate in inventory.objects:
                # The official processor mutates state; clear geometric/text prompts per concept.
                self.processor.reset_all_prompts(state)
                result = self.processor.set_text_prompt(prompt=candidate.prompt, state=state)
                method = "text"
                if (
                    len(result["scores"]) == 0
                    and self.config.box_fallback
                    and candidate.bbox_xyxy_norm
                ):
                    x0, y0, x1, y1 = candidate.bbox_xyxy_norm
                    self.processor.reset_all_prompts(state)
                    result = self.processor.add_geometric_prompt(
                        box=[(x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0],
                        label=True,
                        state=state,
                    )
                    method = "box_fallback"
                for mask, score in zip(result["masks"], result["scores"], strict=True):
                    array = mask.detach().cpu().numpy()
                    if array.ndim == 3 and array.shape[0] == 1:
                        array = array[0]
                    detections.append(
                        Detection(
                            [candidate.candidate_id],
                            array.astype(bool),
                            float(score.item()),
                            method,
                        )
                    )
        import importlib.metadata

        try:
            version = importlib.metadata.version("sam3")
        except importlib.metadata.PackageNotFoundError:
            version = "source-checkout (record your upstream revision separately)"
        return detections, {
            "backend": "sam3",
            "model_executed": True,
            "sam3_version": version,
            "torch_version": self.torch.__version__,
            "device": "cuda",
            "checkpoint_sha256": sha256(Path(self.config.checkpoint)),
        }


def make_segmenter(config: SAMConfig):
    return ReplaySegmenter(config) if config.backend == "replay" else SAM3Segmenter(config)
