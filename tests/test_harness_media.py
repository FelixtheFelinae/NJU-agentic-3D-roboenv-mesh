"""Offline image-set integration tests; replay output is not model accuracy evidence."""

import pytest

from roboenv_perception.construction.common import verify_run
from roboenv_perception.construction.contracts import EvidenceConfig
from roboenv_perception.construction.evidence import prepare_objects
from roboenv_perception.demo import create_fixture
from roboenv_perception.harness.media import _group_observations, run_images
from roboenv_perception.io import load_config, read_json
from roboenv_perception.validation import validate_bundle


def test_image_set_preserves_inputs_and_prepares_multi_view_evidence(tmp_path):
    image, config_path = create_fixture(tmp_path / "fixture")
    second = tmp_path / "another-view.png"
    second.write_bytes(image.read_bytes())
    output = tmp_path / "images"
    run_images([image, second], output, load_config(config_path), "fixture-scene", "move box")

    scene = verify_run(output, "scene.json")
    assert scene["kind"] == "image_set"
    assert scene["temporal_semantics"] == "none"
    assert [frame["source_filename"] for frame in scene["frames"]] == [
        image.name,
        second.name,
    ]
    assert all(frame["capture_timestamp_seconds"] is None for frame in scene["frames"])
    assert all(
        (output / frame["source_ref"]).read_bytes() == image.read_bytes()
        for frame in scene["frames"]
    )
    assert validate_bundle(output)["valid"]

    tracks = read_json(output / scene["tracks_ref"])
    # The two cups are distinguished only by a tentative, recorded color cue.
    assert len(tracks) == 4
    assert all(len(track["observations"]) == 2 for track in tracks)
    assert all(track["identity_status"] == "unverified" for track in tracks)
    assert all(track["reference_interpretation"] == "unresolved" for track in tracks)
    assert read_json(output / "quality.json")["checks_status"] == "review"

    evidence = tmp_path / "evidence"
    prepare_objects(output, evidence, EvidenceConfig())
    inventory = verify_run(evidence, "objects.json")
    assert len(inventory["objects"]) == 4
    assert all(
        len(read_json(evidence / entry["evidence_ref"])["views"]) == 2
        for entry in inventory["objects"]
    )


def test_ambiguous_same_category_instances_are_not_merged():
    rows = [
        [
            {"instance_id": "i0001", "category": "cup", "attributes": {"color": "red"}},
            {"instance_id": "i0002", "category": "cup", "attributes": {"color": "red"}},
        ],
        [{"instance_id": "i0001", "category": "cup", "attributes": {"color": "red"}}],
    ]
    groups = _group_observations(rows, None)
    assert len(groups) == 3
    assert all(len(group["observations"]) == 1 for group in groups)
    with pytest.raises(ValueError, match="nonexistent"):
        _group_observations(rows, {"image-0000:i9999": "cup"})
    groups = _group_observations(
        rows,
        {"image-0000:i0001": "same", "image-0000:i0002": "same"},
    )
    assert all(group["identity_status"] == "ambiguous" for group in groups[:2])


def test_explicit_link_with_conflicting_identity_cues_requires_review():
    frames = [
        [{"instance_id": "i0001", "category": "bike", "attributes": {"color": "red"}}],
        [{"instance_id": "i0001", "category": "bike", "attributes": {"color": "blue"}}],
    ]
    groups = _group_observations(
        frames, {"image-0000:i0001": "same", "image-0001:i0001": "same"}
    )
    assert len(groups) == 2
    assert all(group["identity_status"] == "ambiguous" for group in groups)


def test_image_set_output_is_immutable(tmp_path):
    image, config_path = create_fixture(tmp_path / "fixture")
    output = tmp_path / "images"
    output.mkdir()
    marker = output / "untouched"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_images([image], output, load_config(config_path))
    assert marker.read_text(encoding="utf-8") == "keep"


def test_image_set_detects_changed_source_photo(tmp_path):
    image, config_path = create_fixture(tmp_path / "fixture")
    output = tmp_path / "images"
    run_images([image], output, load_config(config_path))
    scene = read_json(output / "scene.json")
    (output / scene["frames"][0]["source_ref"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="source artifact changed"):
        verify_run(output, "scene.json")
