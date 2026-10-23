"""Local-only Grounding DINO + official SAM 2 adapters (optional CUDA dependencies)."""

from pathlib import Path

import numpy as np
from PIL import Image

from .io import sha256, write_json
from .masks import save_mask
from .runtime import require_cuda
from .segmentation import Detection


class GroundedSAM2Segmenter:
    def __init__(self, config, *, video=False):
        self.torch = require_cuda()
        if not config.checkpoint or not Path(config.checkpoint).is_file():
            raise ValueError("sam.checkpoint must point to a local SAM 2.1 checkpoint")
        model_path = Path(config.grounding_model_path)
        if not config.grounding_model_path or not model_path.is_dir():
            raise ValueError(
                "sam.grounding_model_path must point to a local Grounding DINO snapshot"
            )
        try:
            from sam2.build_sam import build_sam2, build_sam2_video_predictor
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
        except ImportError:
            raise RuntimeError(
                "Install official SAM 2 and the grounded-sam2 optional dependencies"
            ) from None
        self.config = config
        builder = build_sam2_video_predictor if video else build_sam2
        model = builder(config.sam2_model_config, config.checkpoint, device="cuda")
        self.video_predictor = model if video else None
        self.predictor = SAM2ImagePredictor(model)
        self.processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
        self.detector = (
            AutoModelForZeroShotObjectDetection.from_pretrained(model_path, local_files_only=True)
            .to("cuda")
            .eval()
        )
        self.metadata = {
            "backend": "grounded_sam2",
            "model_executed": True,
            "torch_version": self.torch.__version__,
            "device": "cuda",
            "checkpoint_sha256": sha256(Path(config.checkpoint)),
            "sam2_model_config": config.sam2_model_config,
            "grounding_snapshot_sha256": {
                str(p.relative_to(model_path)): sha256(p)
                for p in sorted(model_path.rglob("*"))
                if p.is_file() and ".cache" not in p.relative_to(model_path).parts
            },
            "score_semantics": "minimum of detector score and SAM predicted mask IoU; not calibrated",
        }

    def segment(self, image, inventory):
        image = image.convert("RGB")
        detections = []
        with self.torch.inference_mode():
            self.predictor.set_image(np.array(image))
            for candidate in inventory.objects:
                text = candidate.prompt.strip().lower().rstrip(".") + "."
                inputs = self.processor(images=image, text=text, return_tensors="pt").to("cuda")
                output = self.detector(**inputs)
                result = self.processor.post_process_grounded_object_detection(
                    output,
                    inputs.input_ids,
                    threshold=self.config.grounding_threshold,
                    text_threshold=self.config.grounding_text_threshold,
                    target_sizes=[(image.height, image.width)],
                )[0]
                boxes = result["boxes"].detach().cpu().numpy()
                scores = result["scores"].detach().cpu().numpy()
                method = "grounding_dino_box"
                if len(boxes) == 0 and self.config.box_fallback and candidate.bbox_xyxy_norm:
                    boxes = np.array([candidate.bbox_xyxy_norm]) * np.array(
                        [image.width, image.height, image.width, image.height]
                    )
                    scores = np.array([1.0])
                    method = "vlm_box_fallback"
                for box, detection_score in zip(boxes, scores, strict=True):
                    box = np.clip(box, [0, 0, 0, 0], [image.width, image.height] * 2)
                    if not np.isfinite(box).all() or box[0] >= box[2] or box[1] >= box[3]:
                        raise ValueError("Grounding DINO returned an invalid box")
                    masks, quality, _ = self.predictor.predict(box=box, multimask_output=False)
                    mask = np.asarray(masks)
                    if mask.shape == (image.height, image.width):
                        mask = mask[None]
                    if mask.shape != (1, image.height, image.width) or not np.isfinite(mask).all():
                        raise ValueError("SAM 2 returned an invalid image mask")
                    quality = np.asarray(quality).reshape(-1)
                    if len(mask) != 1 or len(quality) != 1:
                        raise ValueError("Expected one SAM 2 mask per box")
                    score = min(float(detection_score), float(quality[0]))
                    if not np.isfinite(score) or not 0 <= score <= 1:
                        raise ValueError("Invalid Grounded SAM 2 score")
                    # Keep low-score masks for the shared uncertainty routing stage.
                    detections.append(
                        Detection([candidate.candidate_id], mask[0] > 0, score, method)
                    )
        return detections, dict(self.metadata)


class GroundedSAM2VideoBackend:
    def __init__(self, config):
        self.segmenter = GroundedSAM2Segmenter(config, video=True)
        self.config = config
        self.predictor = self.segmenter.video_predictor
        self.torch = self.segmenter.torch

    def track(self, root, frames, inventory, reference_frame):
        if not 0 <= reference_frame < len(frames):
            raise ValueError("reference_frame outside selected video frames")
        with Image.open(root / frames[reference_frame]["image_ref"]) as image:
            seeds, metadata = self.segmenter.segment(image, inventory)
        record = {
            **metadata,
            "backend": "grounded_sam2_video",
            "reference_frame": reference_frame,
            "score_semantics": "reference seed score only; propagation confidence is not provided",
            "discovery_scope": "objects detected in reference frame only; no new-object discovery",
            "frames": [{"image_sha256": f["image_sha256"], "detections": []} for f in frames],
        }
        if not seeds:
            write_json(root / "stages/tracking/manifest.json", record)
            return record
        with self.torch.inference_mode():
            state = self.predictor.init_state(
                video_path=str((root / "inputs/sam_frames").resolve()),
                offload_video_to_cpu=True,
                offload_state_to_cpu=True,
            )
            try:
                for native_id, seed in enumerate(seeds, 1):
                    self.predictor.add_new_mask(
                        state, frame_idx=reference_frame, obj_id=native_id, mask=seed.mask
                    )
                seen = set()
                for reverse in (False, True):
                    if reverse and reference_frame == 0:
                        continue
                    for index, ids, logits in self.predictor.propagate_in_video(
                        state, start_frame_idx=reference_frame, reverse=reverse
                    ):
                        index = int(index)
                        if not 0 <= index < len(frames):
                            raise ValueError("SAM 2 returned a frame outside the input range")
                        if len(ids) != len(seeds) or set(map(int, ids)) != set(
                            range(1, len(seeds) + 1)
                        ):
                            raise ValueError("SAM 2 propagation omitted or changed seed identities")
                        masks = logits.detach().cpu().numpy()
                        rows = []
                        with Image.open(root / frames[index]["image_ref"]) as frame:
                            expected_shape = (frame.height, frame.width)
                        for native_id, mask in zip(ids, masks, strict=True):
                            native_id = int(native_id)
                            seed = seeds[native_id - 1]
                            mask = np.asarray(mask)
                            if mask.shape == (1, *expected_shape):
                                mask = mask[0]
                            if mask.shape != expected_shape or not np.isfinite(mask).all():
                                raise ValueError(
                                    "SAM 2 propagated mask has invalid shape or values"
                                )
                            ref = f"stages/tracking/masks/{index:06d}/{native_id:04d}.png"
                            save_mask(root / ref, mask > 0)
                            rows.append(
                                {
                                    "candidate_ids": seed.candidate_ids,
                                    "track_keys": [f"{seed.candidate_ids[0]}:{native_id}"],
                                    "mask_ref": ref,
                                    "score": seed.score,
                                }
                            )
                        record["frames"][index]["detections"] = rows
                        seen.add(index)
                self.torch.cuda.synchronize()
                if seen != set(range(len(frames))):
                    raise ValueError("SAM 2 propagation omitted input frames")
            finally:
                self.predictor.reset_state(state)
        write_json(root / "stages/tracking/manifest.json", record)
        return record
