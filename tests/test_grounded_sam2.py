"""Adapter protocol checks; these stubs do not validate model accuracy or CUDA execution."""

from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from roboenv_perception.contracts import Inventory, SAMConfig
from roboenv_perception.grounded_sam2 import GroundedSAM2Segmenter, GroundedSAM2VideoBackend
from roboenv_perception.io import load_config


class Tensor:
    def __init__(self, data):
        self.data = np.asarray(data)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.data


class Inputs(dict):
    input_ids = "ids"

    def to(self, device):
        assert device == "cuda"
        return self


class Processor:
    def __init__(self):
        self.texts = []

    def __call__(self, **kwargs):
        self.texts.append(kwargs["text"])
        return Inputs()

    def post_process_grounded_object_detection(self, output, ids, **kwargs):
        assert kwargs["target_sizes"] == [(8, 10)]
        return [
            {
                "boxes": Tensor([[1, 2, 4, 6]] if len(self.texts) == 1 else []),
                "scores": Tensor([0.7] if len(self.texts) == 1 else []),
            }
        ]


class ImagePredictor:
    def set_image(self, image):
        assert image.shape == (8, 10, 3)

    def predict(self, *, box, multimask_output):
        assert not multimask_output
        return np.ones((1, 8, 10)), np.array([0.8]), None


def image_adapter():
    adapter = GroundedSAM2Segmenter.__new__(GroundedSAM2Segmenter)
    adapter.config = SAMConfig(backend="grounded_sam2")
    adapter.torch = SimpleNamespace(inference_mode=nullcontext)
    adapter.processor = Processor()
    adapter.detector = lambda **kwargs: None
    adapter.predictor = ImagePredictor()
    adapter.metadata = {"backend": "protocol_stub"}
    return adapter


def test_text_grounding_fallback_and_candidate_identity():
    adapter = image_adapter()
    inventory = Inventory(
        scene_description="",
        objects=[
            {"candidate_id": "c001", "category": "cup", "prompt": "Cup"},
            {
                "candidate_id": "c002",
                "category": "table",
                "prompt": "Table",
                "bbox_xyxy_norm": [0.1, 0.1, 0.9, 0.9],
            },
            {"candidate_id": "c003", "category": "chair", "prompt": "Chair"},
        ],
    )
    detections, _ = adapter.segment(Image.new("RGB", (10, 8)), inventory)
    assert adapter.processor.texts == ["cup.", "table.", "chair."]
    assert [d.candidate_ids for d in detections] == [["c001"], ["c002"]]
    assert [d.method for d in detections] == ["grounding_dino_box", "vlm_box_fallback"]
    assert [d.score for d in detections] == [0.7, 0.8]
    adapter.config.box_fallback = False
    adapter.processor = Processor()
    detections, _ = adapter.segment(Image.new("RGB", (10, 8)), inventory)
    assert len(detections) == 1


class VideoPredictor:
    def __init__(self, omit=False):
        self.omit = omit
        self.reset = False
        self.directions = []

    def init_state(self, **kwargs):
        return {}

    def add_new_mask(self, state, **kwargs):
        assert kwargs["frame_idx"] == 1

    def propagate_in_video(self, state, *, start_frame_idx, reverse):
        self.directions.append(reverse)
        for index in [1, 0] if reverse else [1, 2]:
            if self.omit and index == 0:
                continue
            # Reordered native IDs must still map to the correct candidate.
            yield index, [2, 1], Tensor(np.ones((2, 1, 8, 10)))

    def reset_state(self, state):
        self.reset = True


@pytest.mark.parametrize("omit", [False, True])
def test_video_bidirectional_coverage_identity_and_cleanup(tmp_path, omit):
    segmenter = image_adapter()
    inventory = Inventory(
        scene_description="",
        objects=[
            {"candidate_id": "c001", "category": "cup", "prompt": "cup"},
            {
                "candidate_id": "c002",
                "category": "table",
                "prompt": "table",
                "bbox_xyxy_norm": [0.1, 0.1, 0.9, 0.9],
            },
        ],
    )
    frames = []
    for i in range(3):
        ref = f"{i}.png"
        Image.new("RGB", (10, 8)).save(tmp_path / ref)
        frames.append({"image_ref": ref, "image_sha256": str(i) * 64})
    backend = GroundedSAM2VideoBackend.__new__(GroundedSAM2VideoBackend)
    backend.segmenter = segmenter
    backend.predictor = VideoPredictor(omit)
    backend.torch = SimpleNamespace(
        inference_mode=nullcontext, cuda=SimpleNamespace(synchronize=lambda: None)
    )
    if omit:
        with pytest.raises(ValueError, match="omitted input frames"):
            backend.track(tmp_path, frames, inventory, 1)
        assert not (tmp_path / "stages/tracking/manifest.json").exists()
    else:
        record = backend.track(tmp_path, frames, inventory, 1)
        assert all(len(f["detections"]) == 2 for f in record["frames"])
        assert record["frames"][0]["detections"][0]["track_keys"] == ["c002:2"]
        assert record["frames"][0]["detections"][0]["score"] == 0.8
    assert backend.predictor.directions == [False, True]
    assert backend.predictor.reset


def test_local_paths_and_hydra_config_are_distinct(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[sam]\nbackend="grounded_sam2"\ncheckpoint="sam.pt"\ngrounding_model_path="dino"\n'
    )
    config = load_config(config_path)
    assert config.sam.checkpoint == str(tmp_path / "sam.pt")
    assert config.sam.grounding_model_path == str(tmp_path / "dino")
    assert config.sam.sam2_model_config == "configs/sam2.1/sam2.1_hiera_s.yaml"
