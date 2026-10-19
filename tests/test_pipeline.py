from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from roboenv_perception.contracts import InstanceLabel, Inventory, PipelineConfig, ProcessingConfig
from roboenv_perception.demo import create_fixture
from roboenv_perception.io import load_config, read_json, write_json
from roboenv_perception.masks import clean_detections, route_masks
from roboenv_perception.pipeline import run_image
from roboenv_perception.segmentation import Detection
from roboenv_perception.validation import validate_bundle


@pytest.fixture
def fixture_scene(tmp_path):
    image, config_path = create_fixture(tmp_path / "fixture")
    return image, load_config(config_path), tmp_path / "run"


def test_persisted_bundle_crops_ids_and_unknown_background(fixture_scene):
    image, config, out = fixture_scene
    run_image(image, out, config, "test-scene")
    checked = validate_bundle(out)
    assert checked["valid"] and checked["checks_status"] == "review"
    objects = read_json(out / "objects/index.json")
    assert len(objects) == 4 and len({o["object_id"] for o in objects}) == 4
    table = objects[0]
    assert table["visual_route"] == "gs_background" and table["physical_role"] == "support"
    quality = read_json(out / "quality.json")
    assert quality["ground_truth_metrics"]["mean_iou"] is None
    assert not quality["reconstruction_ready"]
    with (
        Image.open(out / "background/unknown.png") as unknown,
        Image.open(out / "background/background.png") as bg,
    ):
        assert np.asarray(unknown)[20, 20] == 255  # Unsegmented wall stays unknown.
        assert np.asarray(bg)[20, 20] == 0
        assert np.asarray(bg)[180, 140] == 0  # Red cup is excluded from background.
    run = read_json(out / "run.json")
    assert run["model_execution"] == {"vlm": "replayed", "sam3": "replayed"}
    assert [s["name"] for s in run["stages"]] == [
        "ingest",
        "backend_setup",
        "inventory",
        "segment",
        "labels",
        "route_export",
    ]


def test_existing_run_is_not_overwritten(fixture_scene):
    image, config, out = fixture_scene
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        run_image(image, out, config)
    assert sentinel.read_text() == "keep"


def test_validator_detects_changed_final_mask(fixture_scene):
    image, config, out = fixture_scene
    run_image(image, out, config)
    Image.new("L", (640, 400), 255).save(out / "background/background.png")
    result = validate_bundle(out)
    assert not result["valid"]
    assert any("changed artifact" in e for e in result["errors"])
    assert any("partition" in e for e in result["errors"])


def test_missing_critical_candidate_is_fail_not_silent_success(fixture_scene):
    image, config, out = fixture_scene
    root = Path(config.vlm.replay_dir)
    inventory = read_json(root / "inventory.json")
    inventory["objects"][2]["critical"] = True
    write_json(root / "inventory.json", inventory)
    masks = read_json(root / "masks.json")
    masks["detections"] = masks["detections"][:3]
    write_json(root / "masks.json", masks)
    labels = read_json(root / "labels.json")
    labels["instances"] = labels["instances"][:3]
    write_json(root / "labels.json", labels)
    run_image(image, out, config, task="pick up the box")
    quality = read_json(out / "quality.json")
    assert quality["checks_status"] == "fail"
    assert any(
        i["code"] == "candidate_missing" and i["severity"] == "fail" for i in quality["issues"]
    )


def test_missing_instance_label_becomes_unresolved(fixture_scene):
    image, config, out = fixture_scene
    path = Path(config.vlm.replay_dir) / "labels.json"
    labels = read_json(path)
    labels["instances"] = labels["instances"][:3]
    write_json(path, labels)
    run_image(image, out, config)
    last = read_json(out / "objects/index.json")[-1]
    assert last["visual_route"] == "unresolved"
    assert any(i["code"] == "unresolved_route" for i in read_json(out / "quality.json")["issues"])


def test_fabricated_id_fails_and_keeps_stage_trace(fixture_scene):
    image, config, out = fixture_scene
    path = Path(config.vlm.replay_dir) / "labels.json"
    labels = read_json(path)
    labels["instances"][0]["instance_id"] = "i0999"
    write_json(path, labels)
    with pytest.raises(ValueError, match="nonexistent"):
        run_image(image, out, config)
    run = read_json(out / "run.json")
    assert run["status"] == "failed"
    assert run["stages"][-1]["name"] == "labels"
    assert (out / "stages/labels/response.json").exists()
    assert not (out / "scene.json").exists()


def test_cyclic_parent_relation_is_rejected(fixture_scene):
    image, config, out = fixture_scene
    path = Path(config.vlm.replay_dir) / "labels.json"
    labels = read_json(path)
    labels["instances"][1]["parent_instance_id"] = "i0003"
    labels["instances"][2]["parent_instance_id"] = "i0002"
    write_json(path, labels)
    with pytest.raises(ValueError, match="cyclic"):
        run_image(image, out, config)


def test_wrong_shape_and_nonfinite_scores_rejected():
    with pytest.raises(ValueError, match="shape"):
        clean_detections(
            [Detection(["c001"], np.ones((2, 3), bool), 0.8)], (4, 4), ProcessingConfig(), 0.5
        )
    with pytest.raises(ValueError, match="finite"):
        clean_detections(
            [Detection(["c001"], np.ones((4, 4), bool), float("nan"))],
            (4, 4),
            ProcessingConfig(),
            0.5,
        )


def test_low_confidence_pixels_cannot_enter_background():
    raw = [
        Detection(["c001"], np.ones((8, 8), bool), 0.99),
        Detection(["c002"], np.eye(8, dtype=bool), 0.2),
    ]
    detections, uncertain, _ = clean_detections(
        raw, (8, 8), ProcessingConfig(min_mask_pixels=1), 0.5
    )
    labels = {
        "i0001": InstanceLabel(
            instance_id="i0001",
            category="table",
            visual_route="gs_background",
            physical_role="support",
        )
    }
    routes, _, _ = route_masks(detections, labels, (8, 8), uncertain)
    assert np.all(routes["unknown"][np.eye(8, dtype=bool)])
    assert not np.any(routes["background"] & routes["unknown"])


def test_duplicate_merge_preserves_all_prompt_sources():
    mask = np.ones((8, 8), bool)
    ds, _, issues = clean_detections(
        [Detection(["c001"], mask, 0.9), Detection(["c002"], mask, 0.8)],
        (8, 8),
        ProcessingConfig(),
        0.5,
    )
    assert len(ds) == 1 and ds[0].candidate_ids == ["c001", "c002"]
    assert ds[0].raw_ids == ["r0001", "r0002"]
    assert issues[0]["code"] == "duplicate_merged"


def test_unrelated_overlap_is_unknown_but_parent_parts_are_preserved():
    a, b = np.zeros((8, 8), bool), np.zeros((8, 8), bool)
    a[1:6, 1:6] = True
    b[3:7, 3:7] = True
    ds = [
        Detection(["c001"], a, 0.9, instance_id="i0001"),
        Detection(["c002"], b, 0.8, instance_id="i0002"),
    ]
    labels = {
        identifier: InstanceLabel(
            instance_id=identifier, category="object", visual_route="mesh_candidate"
        )
        for identifier in ["i0001", "i0002"]
    }
    routes, _, issues = route_masks(ds, labels, (8, 8))
    assert routes["unknown"][4, 4] and issues[0]["code"] == "instance_overlap"
    labels["i0002"].parent_instance_id = "i0001"
    routes, _, issues = route_masks(ds, labels, (8, 8))
    assert routes["mesh"][4, 4] and not issues


def test_interaction_background_conflict_stays_unknown():
    ds = [Detection(["c001"], np.ones((8, 8), bool), 0.9, instance_id="i0001")]
    labels = {
        "i0001": InstanceLabel(
            instance_id="i0001", category="cup", task_role="container", visual_route="gs_background"
        )
    }
    routes, effective, issues = route_masks(ds, labels, (8, 8))
    assert routes["unknown"].all() and not routes["background"].any()
    assert effective["i0001"] == "unresolved" and issues[0]["code"] == "interaction_route_conflict"


def test_exif_normalization_declares_mask_coordinate_space(tmp_path):
    from roboenv_perception.io import normalize_image

    raw = Image.new("RGB", (20, 10), "red")
    exif = Image.Exif()
    exif[274] = 6
    raw.save(tmp_path / "phone.jpg", exif=exif)
    image, geometry = normalize_image(tmp_path / "phone.jpg", tmp_path / "frame.png")
    assert image.size == (10, 20)
    assert geometry["raw_size"] == [20, 10] and geometry["exif_orientation"] == 6
    assert geometry["mask_coordinate_space"] == "inputs/frame.png"


def test_live_missing_sam_does_not_call_vlm(tmp_path):
    from roboenv_perception import pipeline

    image = tmp_path / "scene.png"
    Image.new("RGB", (10, 10)).save(image)

    class ForbiddenVLM:
        def infer(self, *args):
            pytest.fail("VLM must not be called when SAM backend is unavailable")

    if __import__("importlib").util.find_spec("sam3") is not None:
        pytest.skip("SAM installed; unavailable backend scenario not applicable")
    with pytest.raises(RuntimeError, match="CUDA|Live SAM"):
        pipeline.run_image(image, tmp_path / "run", PipelineConfig(), vlm=ForbiddenVLM())
    assert read_json(tmp_path / "run/run.json")["status"] == "failed"


@pytest.mark.parametrize("box", [[0, 0, 0, 1], [-1, 0, 1, 1], [0, 0, float("nan"), 1]])
def test_invalid_candidate_boxes_rejected(box):
    with pytest.raises(ValueError):
        Inventory.model_validate(
            {
                "scene_description": "",
                "objects": [
                    {
                        "candidate_id": "c001",
                        "category": "cup",
                        "prompt": "cup",
                        "bbox_xyxy_norm": box,
                    }
                ],
            }
        )
