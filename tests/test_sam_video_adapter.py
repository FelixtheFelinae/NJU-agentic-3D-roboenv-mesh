from types import SimpleNamespace

import numpy as np
import pytest

from roboenv_perception.contracts import Inventory, SAMConfig
from roboenv_perception.video_backend import SAM3VideoBackend


class ProtocolPredictor:
    """Protocol stub only; does not simulate or validate GPU model quality."""

    def __init__(self, omit_frame=False):
        self.sessions = 0
        self.closed = []
        self.shutdown_called = False
        self.omit_frame = omit_frame

    def handle_request(self, request):
        if request["type"] == "start_session":
            self.sessions += 1
            return {"session_id": str(self.sessions)}
        if request["type"] == "close_session":
            self.closed.append(request["session_id"])
        if request["type"] == "add_prompt":
            assert request["output_prob_thresh"] == 0.3
        return {}

    def handle_stream_request(self, request):
        assert request["propagation_direction"] == "both"
        assert request["output_prob_thresh"] == 0.3
        for index in range(1 if self.omit_frame else 2):
            yield {
                "frame_index": index,
                "outputs": {
                    "out_obj_ids": np.array([7]),
                    "out_probs": np.array([0.8]),
                    "out_binary_masks": np.ones((1, 8, 8), dtype=bool),
                },
            }

    def shutdown(self):
        self.shutdown_called = True


def adapter(tmp_path, omit_frame=False):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"protocol-test-not-real-weights")
    instance = SAM3VideoBackend.__new__(SAM3VideoBackend)
    instance.config = SAMConfig(checkpoint=str(checkpoint), confidence_threshold=0.3)
    instance.predictor = ProtocolPredictor(omit_frame)
    instance.torch = SimpleNamespace(
        __version__="protocol-stub", cuda=SimpleNamespace(synchronize=lambda: None)
    )
    return instance


def test_per_concept_native_ids_are_namespaced_and_sessions_closed(tmp_path):
    instance = adapter(tmp_path)
    inventory = Inventory(
        scene_description="",
        objects=[
            {"candidate_id": "c001", "category": "cup", "prompt": "cup"},
            {"candidate_id": "c002", "category": "table", "prompt": "table"},
        ],
    )
    frames = [{"image_sha256": "0" * 64}, {"image_sha256": "1" * 64}]
    record = instance.track(tmp_path / "run", frames, inventory, 0)
    assert {tuple(r["track_keys"]) for r in record["frames"][0]["detections"]} == {
        ("c001:7",),
        ("c002:7",),
    }
    assert instance.predictor.closed == ["1", "2"] and instance.predictor.shutdown_called


def test_missing_native_frame_is_failure_and_cleanup_still_runs(tmp_path):
    instance = adapter(tmp_path, omit_frame=True)
    inventory = Inventory(
        scene_description="", objects=[{"candidate_id": "c001", "category": "cup", "prompt": "cup"}]
    )
    with pytest.raises(ValueError, match="omitted input frames"):
        instance.track(tmp_path / "run", [{"image_sha256": "0" * 64}] * 2, inventory, 0)
    assert instance.predictor.closed == ["1"] and instance.predictor.shutdown_called
