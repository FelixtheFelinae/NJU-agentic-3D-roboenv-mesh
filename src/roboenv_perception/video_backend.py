"""Official SAM 3 video adapter with per-concept sessions and disk-backed mask storage."""

from pathlib import Path

import numpy as np
from PIL import Image

from .io import relative_file, sha256, write_json
from .masks import save_mask
from .runtime import require_cuda
from .segmentation import Detection


def load_frame_detections(manifest: dict, index: int, root: Path):
    detections = []
    for row in manifest["frames"][index]["detections"]:
        with Image.open(relative_file(root, row["mask_ref"])) as image:
            array = np.array(image)
        if array.ndim != 2 or not np.isin(array, [0, 255]).all():
            raise ValueError("video masks must be single-channel binary PNGs (0/255)")
        detections.append(
            Detection(
                row["candidate_ids"],
                array > 0,
                row["score"],
                "video_track",
                track_keys=row["track_keys"],
            )
        )
    return detections


class SAM3VideoBackend:
    def __init__(self, config):
        torch = require_cuda()
        if not config.checkpoint or not Path(config.checkpoint).is_file():
            raise ValueError("sam.checkpoint must point to a local SAM 3 checkpoint")
        try:
            from sam3.model.sam3_video_predictor import Sam3VideoPredictor
        except ImportError:
            raise RuntimeError("Install official SAM 3 in the remote CUDA environment") from None
        self.torch = torch
        self.config = config
        # Single GPU class avoids implicit multi-GPU process spawning.
        self.predictor = Sam3VideoPredictor(checkpoint_path=config.checkpoint)

    def track(self, root, frames, inventory, reference_frame):
        record = {
            "backend": "sam3_video",
            "model_executed": True,
            "checkpoint_sha256": sha256(Path(self.config.checkpoint)),
            "torch_version": self.torch.__version__,
            "sessions": [],
            "frames": [{"image_sha256": f["image_sha256"], "detections": []} for f in frames],
        }
        try:
            for candidate in inventory.objects:
                session = self.predictor.handle_request(
                    {
                        "type": "start_session",
                        "resource_path": str((root / "inputs/sam_frames").resolve()),
                        "offload_video_to_cpu": True,
                    }
                )["session_id"]
                record["sessions"].append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "session_id": session,
                        "prompt": candidate.prompt,
                    }
                )
                try:
                    self.predictor.handle_request(
                        {
                            "type": "add_prompt",
                            "session_id": session,
                            "frame_index": reference_frame,
                            "text": candidate.prompt,
                            "output_prob_thresh": self.config.confidence_threshold,
                        }
                    )
                    seen = set()
                    self.torch.cuda.synchronize()
                    for response in self.predictor.handle_stream_request(
                        {
                            "type": "propagate_in_video",
                            "session_id": session,
                            "start_frame_index": reference_frame,
                            "propagation_direction": "both",
                            "output_prob_thresh": self.config.confidence_threshold,
                        }
                    ):
                        index = int(response["frame_index"])
                        if not 0 <= index < len(frames):
                            raise ValueError("SAM returned a frame outside the input range")
                        seen.add(index)
                        outputs = response["outputs"]
                        rows = []
                        for native_id, mask, score in zip(
                            outputs["out_obj_ids"],
                            outputs["out_binary_masks"],
                            outputs["out_probs"],
                            strict=True,
                        ):
                            native_id = int(native_id)
                            ref = f"stages/tracking/masks/{index:06d}/{candidate.candidate_id}-{native_id}.png"
                            save_mask(root / ref, np.asarray(mask, dtype=bool))
                            rows.append(
                                {
                                    "candidate_ids": [candidate.candidate_id],
                                    "track_keys": [f"{candidate.candidate_id}:{native_id}"],
                                    "mask_ref": ref,
                                    "score": float(score),
                                }
                            )
                        # Bidirectional propagation may emit the reference frame twice.
                        existing = record["frames"][index]["detections"]
                        record["frames"][index]["detections"] = [
                            r for r in existing if candidate.candidate_id not in r["candidate_ids"]
                        ] + rows
                    self.torch.cuda.synchronize()
                    if seen != set(range(len(frames))):
                        raise ValueError(
                            "SAM propagation omitted input frames; not interpreting missing output as an empty scene"
                        )
                finally:
                    self.predictor.handle_request({"type": "close_session", "session_id": session})
        finally:
            self.predictor.shutdown()
        write_json(root / "stages/tracking/manifest.json", record)
        return record


def make_video_backend(config):
    if config.sam.backend == "grounded_sam2":
        from .grounded_sam2 import GroundedSAM2VideoBackend

        return GroundedSAM2VideoBackend(config.sam)
    return SAM3VideoBackend(config.sam)
