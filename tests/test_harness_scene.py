"""Offline scene-composition checks; Isaac physics validation is a separate stage."""

from pathlib import Path

import pytest

from roboenv_perception.construction.common import verify_run
from roboenv_perception.harness.scene import (
    SceneAsset,
    SupportSpec,
    assemble_scene,
    scene_asset_from_evidence,
)
from roboenv_perception.io import read_json


def _usd(folder: Path, name: str) -> Path:
    export = folder / name
    export.mkdir(parents=True)
    (export / "texture.png").write_bytes(b"fixture texture")
    path = export / "object.usda"
    path.write_text(
        '#usda 1.0\n(\n    defaultPrim = "Asset"\n    metersPerUnit = 1\n'
        '    upAxis = "Z"\n)\ndef Xform "Asset" {}\n',
        encoding="utf-8",
    )
    return path


def _support() -> SupportSpec:
    return SupportSpec(
        frame_id="image-0000",
        image_size_xy=(640, 400),
        image_bbox_xyxy=(40, 80, 600, 360),
        size_x_m=0.8,
        size_y_m=1.2,
        top_z_m=0.75,
        dimension_source="assumed",
        evidence_refs=["support-view-001"],
    )


def _asset(path: Path, asset_id: str = "asset-0001") -> SceneAsset:
    return SceneAsset(
        asset_id=asset_id,
        kind="rigid",
        usd_path=path,
        frame_id="image-0000",
        bbox_xyxy=(260, 150, 380, 250),
        image_size_xy=(640, 400),
        local_bounds_min_m=(-0.1, -0.08, -0.02),
        local_bounds_max_m=(0.1, 0.08, 0.18),
        bounds_source="estimated",
        evidence_refs=["view-001"],
    )


def test_assembly_copies_dependencies_and_authors_support_collider(tmp_path):
    source = _usd(tmp_path / "input", "asset")
    output = tmp_path / "scene"
    assemble_scene([_asset(source)], output, _support())
    layout = verify_run(output, "layout.json")
    text = (output / "scene.usda").read_text(encoding="utf-8")
    assert 'prepend apiSchemas = ["PhysicsCollisionAPI", "MaterialBindingAPI"]' in text
    assert "rel material:binding:physics = </World/Support/Contact>" in text
    assert "float physics:gravityMagnitude = 9.81" in text
    assert "prepend references = @./assets/asset-0001/object.usda@" in text
    assert 'delete apiSchemas = ["PhysicsArticulationRootAPI"]' in text
    assert layout["assets"][0]["root_schema_adaptation"] == (
        "scene_layer_deletes_PhysicsArticulationRootAPI"
    )
    assert (output / "assets/asset-0001/object.usda").read_bytes() == source.read_bytes()
    assert (output / "assets/asset-0001/texture.png").is_file()
    pose = layout["assets"][0]["pose"]
    assert pose["position_world_m"] == pytest.approx([-(250 - 220) / 280 * 0.8, 0.0, 0.775])
    assert pose["contact_projection_inside_support"]
    assert layout["physics_validation"] == "not_run"
    assert layout["image_mapping"].endswith("not camera calibration")


def test_scene_asset_helper_uses_one_source_view_and_blender_bounds(tmp_path):
    source = _usd(tmp_path / "input", "asset")
    evidence = {
        "asset_id": "asset-0001",
        "views": [
            {"frame_id": "image-0000", "ref_id": "view-001", "bbox_xyxy": [20, 30, 50, 90]},
            {"frame_id": "image-0001", "ref_id": "view-002", "bbox_xyxy": [5, 6, 70, 80]},
        ],
    }
    blender = {"bounds_m": [[-0.1, -0.2, 0], [0.1, 0.2, 0.3]]}
    asset = scene_asset_from_evidence(
        "asset-0001", source, evidence, blender, "image-0000", (640, 400), kind="rigid"
    )
    assert asset.bbox_xyxy == (20, 30, 50, 90)
    assert asset.evidence_refs == ["view-001"]
    assert asset.local_bounds_max_m == (0.1, 0.2, 0.3)
    with pytest.raises(ValueError, match="exactly one"):
        scene_asset_from_evidence(
            "asset-0001", source, evidence, blender, "missing", (640, 400), kind="rigid"
        )


def test_articulated_asset_preserves_articulation_schema(tmp_path):
    source = _usd(tmp_path / "input", "articulated")
    output = tmp_path / "scene"
    asset = _asset(source).model_copy(update={"kind": "articulated"})
    assemble_scene([asset], output, _support())
    text = (output / "scene.usda").read_text(encoding="utf-8")
    assert 'delete apiSchemas = ["PhysicsArticulationRootAPI"]' not in text
    layout = verify_run(output, "layout.json")
    assert layout["assets"][0]["root_schema_adaptation"] == "preserve_articulation_root_api"


def test_assembly_rejects_mixed_frames_and_unsafe_identifiers(tmp_path):
    source = _usd(tmp_path / "input", "asset")
    asset = _asset(source)
    with pytest.raises(ValueError, match="same image frame"):
        assemble_scene(
            [asset.model_copy(update={"frame_id": "image-0001"})],
            tmp_path / "mixed",
            _support(),
        )
    with pytest.raises(ValueError, match="asset_id"):
        SceneAsset.model_validate({**asset.model_dump(), "asset_id": "../escape"})
    with pytest.raises(ValueError, match="asset bbox"):
        assemble_scene(
            [asset.model_copy(update={"bbox_xyxy": (-1, 0, 20, 30)})],
            tmp_path / "invalid-box",
            _support(),
        )
    with pytest.raises(ValueError, match="unique"):
        assemble_scene([asset, asset], tmp_path / "duplicate", _support())


def test_assembly_keeps_outside_projection_for_review(tmp_path):
    source = _usd(tmp_path / "input", "asset")
    output = tmp_path / "outside"
    asset = _asset(source).model_copy(update={"bbox_xyxy": (0, 0, 20, 30)})
    assemble_scene([asset], output, _support())
    layout = read_json(output / "layout.json")
    assert not layout["assets"][0]["pose"]["contact_projection_inside_support"]
    assert layout["issues"] == [
        {"code": "contact_projection_outside_support", "asset_id": "asset-0001"}
    ]


def test_assembly_rejects_symlinked_dependency(tmp_path):
    source = _usd(tmp_path / "input", "asset")
    (source.parent / "outside.obj").symlink_to(tmp_path / "outside.obj")
    with pytest.raises(ValueError, match="symlink"):
        assemble_scene([_asset(source)], tmp_path / "scene", _support())
    assert read_json(tmp_path / "scene/run.json")["status"] == "failed"
