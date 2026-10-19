"""Official SAM 3 video adapter with per-concept sessions and disk-backed mask storage."""

import math
from pathlib import Path

import numpy as np
from PIL import Image

from .io import read_json, relative_file, sha256, write_json
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


class ReplayVideoBackend:
    def __init__(self, path: str):
        self.path = Path(path)
        if not path or not self.path.is_file():
            raise ValueError("video replay requires video.replay_manifest")

    def track(self, root, frames, inventory, reference_frame):
        source = read_json(self.path)
        if len(source["frames"]) != len(frames):
            raise ValueError("video replay does not cover exactly the selected frame set")
        ids = {c.candidate_id for c in inventory.objects}
        destination = {
            "backend": "replay",
            "model_executed": False,
            "manifest_sha256": sha256(self.path),
            "frames": [],
        }
        for index, (raw, frame) in enumerate(zip(source["frames"], frames, strict=True)):
            if raw["image_sha256"] != frame["image_sha256"]:
                raise ValueError("replay video frame hash mismatch")
            rows, used = [], set()
            for item_index, row in enumerate(raw["detections"]):
                if not row["candidate_ids"] or not set(row["candidate_ids"]) <= ids:
                    raise ValueError("unknown video candidate reference")
                keys = row["track_keys"]
                if (
                    not keys
                    or len(keys) != len(set(keys))
                    or any(not isinstance(k, str) or not k for k in keys)
                    or used.intersection(keys)
                ):
                    raise ValueError("duplicate or invalid native track key within frame")
                used.update(keys)
                score = float(row["score"])
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("invalid video score")
                ref = f"stages/tracking/masks/{index:06d}/{item_index:04d}.png"
                with Image.open(relative_file(self.path.parent, row["mask_ref"])) as mask:
                    mask.load()
                    if mask.mode != "L" or not np.isin(np.array(mask), [0, 255]).all():
                        raise ValueError("replay video mask must be binary L PNG")
                    (root / ref).parent.mkdir(parents=True, exist_ok=True)
                    mask.save(root / ref)
                rows.append({**row, "score": score, "mask_ref": ref})
            destination["frames"].append(
                {"image_sha256": frame["image_sha256"], "detections": rows}
            )
        write_json(root / "stages/tracking/manifest.json", destination)
        return destination


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
