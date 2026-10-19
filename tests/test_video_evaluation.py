import shutil

import numpy as np
import pytest
from PIL import Image

from roboenv_perception.contracts import VideoConfig
from roboenv_perception.evaluation import evaluate, freeze_dataset, match_instances
from roboenv_perception.io import load_config, read_json, write_json
from roboenv_perception.runtime import require_cuda
from roboenv_perception.tracking import TrackRegistry
from roboenv_perception.validation import validate_bundle
from roboenv_perception.video_demo import create_video_fixture
from roboenv_perception.video_io import decode_video
from roboenv_perception.video_pipeline import run_video


@pytest.fixture(scope="module")
def video_case(tmp_path_factory):
    root = tmp_path_factory.mktemp("video-case")
    source, config_path, manifest = create_video_fixture(root / "fixture")
    config = load_config(config_path)
    bundle = run_video(source, root / "run", config, "test-video")
    lock = freeze_dataset(manifest, root / "dataset.lock.json")
    predictions = root / "predictions.json"
    write_json(predictions, {"clip": str(bundle)})
    return {
        "root": root,
        "source": source,
        "config": config,
        "manifest": manifest,
        "lock": lock,
        "bundle": bundle,
        "predictions": predictions,
    }


def eval_case(case, output, predictions=None):
    return evaluate(
        case["manifest"], case["lock"], output, predictions or case["predictions"], fixture=True
    )


def altered_video(video_case, tmp_path, mutate):
    copied = tmp_path / "fixture"
    shutil.copytree(video_case["root"] / "fixture", copied)
    manifest = read_json(copied / "video_masks.json")
    mutate(manifest, copied)
    write_json(copied / "video_masks.json", manifest)
    bundle = run_video(
        copied / "scene.mkv", tmp_path / "run", load_config(copied / "replay.toml"), "altered"
    )
    prediction_map = tmp_path / "predictions.json"
    write_json(prediction_map, {"clip": str(bundle)})
    return bundle, prediction_map


def test_video_persisted_output_identity_occlusion_and_pts(video_case):
    root = video_case["bundle"]
    assert validate_bundle(root)["valid"]
    frames = read_json(root / "inputs/video.json")["frames"]
    assert [f["source_frame_index"] for f in frames] == list(range(6))
    assert all("source_pts" in f and f["source_time_base"] for f in frames)
    tracks = read_json(root / "tracks/index.json")
    red = next(t for t in tracks if "c002:7" in t["native_track_keys"])
    assert len(red["observations"]) == 5
    assert "frame-000002" not in {o["frame_id"] for o in red["observations"]}
    assert {read_json(root / o["observation_ref"])["object_id"] for o in red["observations"]} == {
        red["object_id"]
    }


def test_sampling_timestamp_quantization_and_budget(video_case, tmp_path):
    result = decode_video(
        video_case["source"], tmp_path / "sample", VideoConfig(sample_fps=3, max_frames=20)
    )
    assert [f["source_frame_index"] for f in result["frames"]] == [0, 2, 4]
    limited = decode_video(
        video_case["source"], tmp_path / "limited", VideoConfig(sample_fps=6, max_frames=2)
    )
    assert limited["frame_budget_reached"] and len(limited["frames"]) == 2


def test_unseen_track_is_unknown_and_not_background(video_case, tmp_path):
    def mutate(manifest, root):
        manifest["frames"][4]["detections"][1]["track_keys"] = ["c002:new"]

    bundle, _ = altered_video(video_case, tmp_path, mutate)
    scene = read_json(bundle / "scene.json")
    frame = scene["frames"][4]
    rows = read_json(bundle / frame["objects_ref"])
    new = next(r for r in rows if r["native_track_keys"] == ["c002:new"])
    assert new["visual_route"] == "unresolved"
    with (
        Image.open(bundle / new["mask_ref"]) as m,
        Image.open(bundle / frame["background_ref"].replace("index.json", "unknown.png")) as u,
    ):
        assert np.all(np.asarray(u)[np.asarray(m) > 0] == 255)


def test_replay_wrong_frame_hash_fails_instead_of_misaligned_masks(video_case, tmp_path):
    copied = tmp_path / "fixture"
    shutil.copytree(video_case["root"] / "fixture", copied)
    manifest = read_json(copied / "video_masks.json")
    manifest["frames"][1]["image_sha256"] = "0" * 64
    write_json(copied / "video_masks.json", manifest)
    with pytest.raises(ValueError, match="hash mismatch"):
        run_video(copied / "scene.mkv", tmp_path / "run", load_config(copied / "replay.toml"))
    assert read_json(tmp_path / "run/run.json")["status"] == "failed"


def test_duplicate_native_key_in_frame_is_rejected(video_case, tmp_path):
    copied = tmp_path / "fixture"
    shutil.copytree(video_case["root"] / "fixture", copied)
    manifest = read_json(copied / "video_masks.json")
    manifest["frames"][0]["detections"][2]["track_keys"] = ["c002:7"]
    write_json(copied / "video_masks.json", manifest)
    with pytest.raises(ValueError, match="track key"):
        run_video(copied / "scene.mkv", tmp_path / "run", load_config(copied / "replay.toml"))


def test_registry_never_silently_merges_two_existing_objects():
    registry = TrackRegistry("clip")
    first, _ = registry.assign(["a"], set())
    second, _ = registry.assign(["b"], set())
    third, ambiguous = registry.assign(["a", "b"], set())
    assert ambiguous and third not in {first, second}
    assert registry.by_key == {"a": first, "b": second}


def test_fixture_perfect_metrics_are_not_formal_acceptance(video_case, tmp_path):
    result = eval_case(video_case, tmp_path / "eval")
    assert result["counts"]["gt_visible"] == 17
    assert result["metrics"] == {
        "object_recall": 1.0,
        "critical_recall": 1.0,
        "precision": 1.0,
        "mean_iou_misses_zero": 1.0,
        "foreground_leakage": 0.0,
        "id_switches": 0,
        "reacquisition_rate": 1.0,
    }
    assert result["metric_gates_passed"]
    assert not result["eligible_for_acceptance"] and not result["acceptance_passed"]


def test_missing_prediction_counts_all_objects_in_denominator(video_case, tmp_path):
    mapping = tmp_path / "missing.json"
    write_json(mapping, {"clip": None})
    result = eval_case(video_case, tmp_path / "eval", mapping)
    assert result["counts"]["gt_visible"] == 17 and result["counts"]["critical_gt"] == 5
    assert (
        result["metrics"]["object_recall"] == 0 and result["metrics"]["mean_iou_misses_zero"] == 0
    )
    assert result["counts"]["failed_samples"] == 1 and result["counts"]["failed_frames"] == 6
    assert result["counts"]["foreground_pixels_unavailable"] > 0
    assert result["metrics"]["foreground_leakage"] is None and not result["metric_gates_passed"]


def test_ground_truth_id_swap_is_detected_despite_perfect_masks(video_case, tmp_path):
    def mutate(manifest, root):
        for frame in manifest["frames"][3:]:
            for row in frame["detections"]:
                if row["track_keys"] == ["c002:7"]:
                    row["track_keys"] = ["c002:12"]
                elif row["track_keys"] == ["c002:12"]:
                    row["track_keys"] = ["c002:7"]

    _, mapping = altered_video(video_case, tmp_path, mutate)
    result = eval_case(video_case, tmp_path / "eval", mapping)
    assert result["metrics"]["mean_iou_misses_zero"] == 1
    assert result["metrics"]["id_switches"] == 2 and result["metrics"]["reacquisition_rate"] == 0
    assert not result["gates"]["id_switches"]


def test_background_leakage_measured_against_independent_foreground(video_case, tmp_path):
    def mutate(manifest, root):
        frame = manifest["frames"][4]
        with (
            Image.open(root / frame["detections"][0]["mask_ref"]) as table,
            Image.open(root / frame["detections"][1]["mask_ref"]) as red,
        ):
            combined = np.maximum(np.asarray(table), np.asarray(red))
        Image.fromarray(combined).save(root / "leaky.png")
        frame["detections"][0]["mask_ref"] = "leaky.png"
        frame["detections"].pop(1)

    _, mapping = altered_video(video_case, tmp_path, mutate)
    result = eval_case(video_case, tmp_path / "eval", mapping)
    assert result["counts"]["leaked_pixels"] == 600
    assert result["metrics"]["foreground_leakage"] > 0.02
    assert not result["gates"]["foreground_leakage"]
    assert result["counts"]["matched"] == 16


def test_frozen_dataset_change_is_rejected(video_case, tmp_path):
    fixture = tmp_path / "fixture"
    shutil.copytree(video_case["root"] / "fixture", fixture)
    lock = freeze_dataset(fixture / "dataset.json", tmp_path / "lock.json")
    with (fixture / "truth/truth.json").open("a") as handle:
        handle.write(" ")
    with pytest.raises(ValueError, match="frozen dataset changed"):
        evaluate(fixture / "dataset.json", lock, tmp_path / "eval", fixture=True)


def test_human_dataset_cannot_use_fixture_cpu_oracle(video_case, tmp_path):
    fixture = tmp_path / "fixture"
    shutil.copytree(video_case["root"] / "fixture", fixture)
    manifest = read_json(fixture / "dataset.json")
    manifest["annotation_source"] = "human"
    write_json(fixture / "dataset.json", manifest)
    lock = freeze_dataset(fixture / "dataset.json", tmp_path / "lock.json")
    with pytest.raises(ValueError, match="forbidden"):
        evaluate(fixture / "dataset.json", lock, tmp_path / "eval", fixture=True)


def test_matching_is_global_one_to_one_and_category_constrained():
    result = match_instances(
        np.array([[0.9, 0.8], [0.85, 0.0]]), ["cup", "cup"], ["cup", "cup"], 0.5
    )
    assert {(g, p) for g, p, _ in result} == {(0, 1), (1, 0)}
    assert match_instances(np.array([[1.0]]), ["cup"], ["table"], 0.5) == []


def test_cuda_guard_fails_on_mac_before_loading_models(monkeypatch):
    monkeypatch.setattr("roboenv_perception.runtime.platform.system", lambda: "Darwin")
    with pytest.raises(RuntimeError, match="Linux/NVIDIA CUDA"):
        require_cuda()


def test_malformed_prediction_metadata_is_a_failed_sample_not_omitted(video_case, tmp_path):
    copied = tmp_path / "broken"
    shutil.copytree(video_case["bundle"], copied)
    (copied / "run.json").write_text("not json")
    mapping = tmp_path / "predictions.json"
    write_json(mapping, {"clip": str(copied)})
    result = eval_case(video_case, tmp_path / "eval", mapping)
    assert result["counts"]["failed_samples"] == 1 and result["counts"]["gt_visible"] == 17
    assert result["metrics"]["object_recall"] == 0


def test_prepare_truth_copies_only_images_and_empty_objects(video_case, tmp_path):
    from roboenv_perception.evaluation import prepare_truth

    prepared = prepare_truth(video_case["bundle"], tmp_path / "annotations")
    truth = read_json(prepared / "truth.json")
    assert len(truth["frames"]) == 6 and all(not frame["objects"] for frame in truth["frames"])
    assert not list(prepared.rglob("*mask*.png"))
    with pytest.raises(ValueError, match="no visible annotated objects"):
        freeze_dataset(prepared / "dataset.json", tmp_path / "empty.lock.json")


def test_unprocessed_truth_frame_stays_in_denominator(video_case, tmp_path):
    copied = tmp_path / "fixture"
    shutil.copytree(video_case["root"] / "fixture", copied)
    truth = read_json(copied / "truth/truth.json")
    extra = dict(truth["frames"][0])
    extra["frame_id"] = "frame-999999"
    truth["frames"].append(extra)
    write_json(copied / "truth/truth.json", truth)
    lock = freeze_dataset(copied / "dataset.json", tmp_path / "lock.json")
    result = evaluate(
        copied / "dataset.json", lock, tmp_path / "eval", video_case["predictions"], fixture=True
    )
    assert result["counts"]["gt_visible"] == 20 and result["counts"]["matched"] == 17
    assert result["counts"]["failed_frames"] == 1 and not result["gates"]["all_samples_available"]
