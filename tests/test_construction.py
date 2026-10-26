"""Construction safety and synthetic geometry checks; never real-model acceptance."""

import hashlib
import json
import sys

import numpy as np
import pytest
from PIL import Image

from roboenv_perception.cli import main
from roboenv_perception.construction.background import prepare_background
from roboenv_perception.construction.common import new_run, verify_run
from roboenv_perception.construction.contracts import (
    BackgroundConfig,
    Blueprint,
    ConstructionConfig,
    EvidenceConfig,
    Quantity,
)
from roboenv_perception.construction.design import design_objects, validate_blueprint_evidence
from roboenv_perception.construction.evidence import prepare_objects, select_views
from roboenv_perception.construction.isaac_audio_scene_worker import table_geometry
from roboenv_perception.construction.reconstruction import read_colmap_text, run_process
from roboenv_perception.construction.supervision import projection_matrix, region_weights
from roboenv_perception.construction.table import complete_table
from roboenv_perception.contracts import VLMConfig
from roboenv_perception.demo import create_fixture
from roboenv_perception.io import load_config, read_json, write_json
from roboenv_perception.masks import save_mask
from roboenv_perception.pipeline import run_image


@pytest.fixture
def perception(tmp_path):
    image, config = create_fixture(tmp_path / "fixture")
    output = tmp_path / "perception"
    run_image(image, output, load_config(config), "construction-fixture")
    return output


def rigid(object_id):
    return {
        "object_id": object_id,
        "kind": "rigid",
        "frame_convention": "metres; Z-up; front=+Y; local object frame",
        "parts": [
            {
                "name": "body",
                "parent": None,
                "role": "body",
                "geometry": "shell",
                "hollow": True,
                "material_appearance": "red",
                "collision_intent": "hollow shell",
                "evidence_refs": ["view-001"],
            }
        ],
        "acceptance": ["Preserve cavity"],
        "unresolved": ["dimensions and unseen underside"],
    }


def test_evidence_integrity_and_background_exclusion(perception, tmp_path):
    evidence = tmp_path / "evidence"
    prepare_objects(perception, evidence, EvidenceConfig())
    inventory = verify_run(evidence, "objects.json")
    assert len(inventory["objects"]) == 4
    record = read_json(evidence / inventory["objects"][1]["evidence_ref"])
    assert len(record["views"]) == 1 and record["camera_and_scale"] == "not_estimated"
    assert record["parent_object_ids"] == []
    background = tmp_path / "background"
    prepare_background(perception, background, BackgroundConfig(boundary_margin_pixels=2))
    frame = verify_run(background, "background.json")["frames"][0]
    with Image.open(background / frame["observed_mask_ref"]) as image:
        mask = np.asarray(image)
    assert mask[180, 140] == 0 and mask[20, 20] == 0  # cup and unknown wall
    source_frame = read_json(perception / "scene.json")["frame"]["image_ref"]
    assert (background / frame["image_ref"]).read_bytes() == (
        perception / source_frame
    ).read_bytes()
    (evidence / record["views"][0]["mask_ref"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="changed or missing"):
        verify_run(evidence, "objects.json")


def test_static_scene_sfm_mode_only_changes_feature_mask(perception, tmp_path):
    conservative = tmp_path / "conservative"
    unmasked = tmp_path / "unmasked"
    prepare_background(perception, conservative, BackgroundConfig())
    prepare_background(perception, unmasked, BackgroundConfig(sfm_mask_mode="all"))
    frame_a = verify_run(conservative, "background.json")["frames"][0]
    frame_b = verify_run(unmasked, "background.json")["frames"][0]
    with Image.open(conservative / frame_a["sfm_mask_ref"]) as image:
        assert np.asarray(image).min() == 0
    with Image.open(unmasked / frame_b["sfm_mask_ref"]) as image:
        assert np.asarray(image).min() == 255
    assert (conservative / frame_a["observed_mask_ref"]).read_bytes() == (
        unmasked / frame_b["observed_mask_ref"]
    ).read_bytes()


def test_audio_table_visual_and_collision_extents_agree():
    geometry = table_geometry(
        {"width_m": 0.65, "depth_m": 0.62, "top_height_m": 0.75, "thickness_m": 0.03}
    )
    vertices = np.asarray(geometry["vertices_local_m"])
    assert np.allclose(np.ptp(vertices[:, :2], axis=0), geometry["collision_size_m"][:2])


def test_time_separation_is_not_claimed_as_camera_diversity():
    observations = [
        {
            "frame_id": str(i),
            "timestamp_seconds": i / 10,
            "quality": {"visible_pixels": 100, "sharpness": i, "edge_touching": False},
        }
        for i in range(20)
    ]
    selected = select_views(
        observations, EvidenceConfig(min_visible_pixels=1, max_views=3, min_time_gap_seconds=0.5)
    )
    assert len(selected) == 3
    assert all(
        b["timestamp_seconds"] - a["timestamp_seconds"] >= 0.5
        for a, b in zip(selected, selected[1:])
    )


def test_blueprint_zero_joints_unknowns_and_evidence_constraints():
    blueprint = Blueprint.model_validate(rigid("cup"))
    assert blueprint.joints == [] and blueprint.target_dimensions_m.x.value is None
    for value in [float("nan"), -1]:
        data = rigid("cup")
        data["target_dimensions_m"] = {
            "x": {"value": value, "source": "estimated", "evidence_refs": ["view-001"]}
        }
        with pytest.raises(ValueError):
            Blueprint.model_validate(data)
    with pytest.raises(ValueError):
        Quantity(value=1, source="measured")
    brief = type("Brief", (), {"build_route": "rigid", "observed_motion": []})()
    blueprint.parts[0].evidence_refs = ["fabricated"]
    with pytest.raises(ValueError, match="unknown blueprint evidence"):
        validate_blueprint_evidence(
            blueprint, brief, {"object_id": "cup", "views": [{"ref_id": "view-001"}]}, {}
        )


def test_design_binds_self_review_and_preserves_handoff(perception, tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    prepare_objects(perception, evidence, EvidenceConfig())
    calls = []

    class FixtureVLM:
        def infer(self, stage, prompt, images, schema, trace):
            calls.append(stage)
            assert images and "JSON schema" in prompt
            if "-intake" in stage:
                context = json.loads(prompt.split("\nJSON schema:")[0].split("\n", 1)[1])
                oid = context["evidence"]["object_id"]
                return {
                    "object_id": oid,
                    "category": "fixture",
                    "summary": "fixture",
                    "build_route": "rigid",
                    "support_mode": "unknown",
                    "reference_interpretation": "single",
                    "evidence_refs": ["view-001"],
                }
            if "-blueprint-" in stage:
                # Intake JSON follows the explicitly labelled section.
                brief = json.loads(prompt.split("\nIntake:\n")[1].split("\nPrevious blueprint:")[0])
                return rigid(brief["object_id"])
            blueprint = rigid(
                json.loads(prompt.split("\nJSON schema:")[0].rsplit("\n", 1)[1])["object_id"]
            )
            # Bind to the persisted JSON exactly, as an HTTP model must echo the supplied digest.
            path = trace.parent / "blueprint.json"
            assert read_json(path)["object_id"] == blueprint["object_id"]
            return {
                "decision": "PASS",
                "findings": [],
                "summary": "synthetic review",
                "blueprint_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

    monkeypatch.setattr(
        "roboenv_perception.construction.design.make_vlm", lambda *a, **k: FixtureVLM()
    )
    output = tmp_path / "design"
    design_objects(evidence, output, ConstructionConfig(vlm=VLMConfig(backend="replay")))
    inventory = verify_run(output, "designs.json")
    assert inventory["all_designs_ready"] and not inventory["independent_acceptance"]
    handoff = read_json(output / inventory["objects"][0]["handoff_ref"])
    assert handoff["coding_status"] == "not_run" and not handoff["metric_dimensions_available"]
    assert (
        handoff["blueprint_sha256"]
        == hashlib.sha256((output / handoff["blueprint_ref"]).read_bytes()).hexdigest()
    )
    assert len(calls) == 12


def make_planar_background(root):
    with new_run(root, "fixture", {}):
        frames = []
        for i in range(3):
            # Held-out red RGB must never contaminate the green training atlas.
            Image.new("RGB", (16, 16), "red" if i == 0 else "green").save(root / f"{i}.png")
            valid = np.ones((16, 16), bool)
            valid[5:9, 5:9] = False
            save_mask(root / f"mask{i}.png", valid)
            frames.append(
                {
                    "frame_id": str(i),
                    "image_name": f"{i}.png",
                    "image_ref": f"{i}.png",
                    "size": [16, 16],
                    "observed_mask_ref": f"mask{i}.png",
                    "split": "holdout" if i == 0 else "train",
                }
            )
        write_json(root / "background.json", {"frames": frames})


def test_table_atlas_holdout_isolation_and_weak_completion(tmp_path):
    background = tmp_path / "background"
    make_planar_background(background)
    spec = tmp_path / "table.json"
    write_json(
        spec,
        {
            "width_m": 1,
            "depth_m": 1,
            "pixels_per_metre": 16,
            "fill": "telea",
            "views": [
                {"frame_id": str(i), "corners_xy": [[0, 0], [15, 0], [15, 15], [0, 15]]}
                for i in range(3)
            ],
        },
    )
    output = tmp_path / "table"
    complete_table(background, spec, output)
    result = verify_run(output, "completion.json")
    assert result["holdout_pixels_used_for_fusion"] is False
    with Image.open(output / "atlas.png") as image:
        atlas = np.asarray(image)
    assert atlas[..., 0].max() == 0 and atlas[..., 1].min() > 0
    with Image.open(output / "provenance.png") as image:
        provenance = np.asarray(image)
    assert provenance[6, 6] == 2 and provenance[0, 0] == 1
    with Image.open(output / "frames/0/completed.png") as image:
        completed = np.asarray(image)
    assert completed[0, 0, 0] == 255 and completed[6, 6, 1] > 0


def test_region_loss_weights_and_offcentre_camera_projection():
    observed = np.array([[True, False, False]])
    synthetic = np.array([[False, True, False]])
    np.testing.assert_allclose(region_weights(observed, synthetic, 0.15), [[1, 0.15, 0]])
    with pytest.raises(ValueError, match="disjoint"):
        region_weights(observed, observed, 0.15)
    projection = projection_matrix([[100, 0, 30], [0, 110, 20], [0, 0, 1]], (80, 60))
    point = np.array([0.1, 0.2, 2, 1])
    clip = projection @ point
    np.testing.assert_allclose((clip[:2] / clip[3] + 1) * [40, 30], [35, 31], atol=1e-5)


def test_colmap_empty_observation_lines_and_process_failure_receipt(tmp_path):
    (tmp_path / "cameras.txt").write_text("1 PINHOLE 16 16 10 10 8 8\n")
    (tmp_path / "images.txt").write_text(
        "# header\n1 1 0 0 0 0 0 0 1 a.png\n\n2 1 0 0 0 0 0 0 1 b.png\n1 2 -1\n"
    )
    (tmp_path / "points3D.txt").write_text("# none\n")
    _, images, _ = read_colmap_text(tmp_path)
    assert images[1]["points"] == [] and images[2]["points"] == [(1, 2, -1)]
    with pytest.raises(RuntimeError, match="exited 7"):
        run_process(
            [sys.executable, "-c", "raise SystemExit(7)"],
            tmp_path,
            tmp_path / "logs/failure.log",
            10,
        )
    assert read_json(tmp_path / "logs/failure.json")["returncode"] == 7


def test_cli_preparation_and_existing_run_protection(perception, tmp_path):
    output = tmp_path / "evidence"
    assert main(["prepare-objects", "--bundle", str(perception), "--output", str(output)]) == 0
    assert main(["prepare-objects", "--bundle", str(perception), "--output", str(output)]) == 1


def test_bad_review_hash_never_produces_handoff(perception, tmp_path, monkeypatch):
    import shutil

    evidence = tmp_path / "evidence"
    prepare_objects(perception, evidence, EvidenceConfig())
    inventory = read_json(evidence / "objects.json")
    first = read_json(evidence / inventory["objects"][0]["evidence_ref"])
    only = tmp_path / "only"
    with new_run(only, "fixture", {}):
        shutil.copytree(evidence / "objects", only / "objects")
        write_json(only / "objects.json", {**inventory, "objects": inventory["objects"][:1]})

    class WrongReview:
        def infer(self, stage, *args):
            if "-intake" in stage:
                return {
                    "object_id": first["object_id"],
                    "category": "fixture",
                    "summary": "fixture",
                    "build_route": "rigid",
                    "support_mode": "unknown",
                    "reference_interpretation": "single",
                    "evidence_refs": ["view-001"],
                }
            if "-blueprint-" in stage:
                return rigid(first["object_id"])
            return {
                "decision": "PASS",
                "findings": [],
                "summary": "stale",
                "blueprint_sha256": "0" * 64,
            }

    monkeypatch.setattr(
        "roboenv_perception.construction.design.make_vlm", lambda *a, **k: WrongReview()
    )
    output = tmp_path / "bad-design"
    design_objects(only, output, ConstructionConfig(vlm=VLMConfig(backend="replay")))
    result = verify_run(output, "designs.json")
    assert not result["all_designs_ready"]
    assert not list(output.rglob("coding_handoff.json"))
    assert len(list(output.rglob("rejected.json"))) == 3


def test_failed_run_receipt_and_nested_run_files_are_hash_bound(tmp_path):
    root = tmp_path / "nested"
    with new_run(root, "fixture", {}):
        write_json(root / "entry.json", {})
        write_json(root / "nested/run.json", {"important": True})
    verify_run(root, "entry.json")
    write_json(root / "nested/run.json", {})
    with pytest.raises(ValueError, match="changed or missing"):
        verify_run(root, "entry.json")
    failed = tmp_path / "failed"
    with pytest.raises(ValueError, match="fixture failure"):
        with new_run(failed, "fixture", {}):
            raise ValueError("fixture failure")
    assert read_json(failed / "run.json")["status"] == "failed"


def test_custom_design_system_does_not_replace_perception_default():
    from roboenv_perception.prompts import SYSTEM
    from roboenv_perception.vlm import HTTPVLM

    config = VLMConfig(endpoint="http://localhost/infer", model="fixture", headers={})
    default = HTTPVLM(config).build_payload("x", "prompt", [], {})
    custom = HTTPVLM(config, system="design-only").build_payload("x", "prompt", [], {})
    assert default["messages"][0]["content"] == SYSTEM
    assert custom["messages"][0]["content"] == "design-only"


def test_construction_demo_exports_handoffs_and_table_without_models(tmp_path):
    from roboenv_perception.construction.demo import run_demo

    result = run_demo(tmp_path / "demo")
    assert result["frames"] == 6
    assert result["model_inference"] == "not_run"
    assert result["gaussian_training"] == "not_run"
    assert not result["holdout_used_for_fusion"]
    assert sum(row["status"] == "design_ready" for row in result["objects"]) == 2
    assert verify_run(tmp_path / "demo/table", "completion.json")["physics_geometry"] is None


def test_readiness_reports_missing_fields_without_disclosing_credentials(tmp_path, monkeypatch):
    from roboenv_perception.construction.contracts import load_construction_config
    from roboenv_perception.construction.preflight import check_readiness

    monkeypatch.delenv("VLM_API_KEY", raising=False)
    monkeypatch.setenv("PRIVATE_TEST_SECRET", "not-for-reports")
    config = tmp_path / "config.toml"
    config.write_text(
        '[vlm.headers]\nAuthorization = "Bearer ${VLM_API_KEY}"\n'
        'Custom = "${PRIVATE_TEST_SECRET}"\n'
        '[background]\ngaussian_python = "./missing-python"\n'
        'colmap = "./missing-colmap"\n'
    )
    parsed = load_construction_config(config)
    assert parsed.background.gaussian_python == str(tmp_path / "missing-python")
    result = check_readiness(config)
    assert not result["ready"] and not result["stage_readiness"]["live_perception"]
    assert "not-for-reports" not in json.dumps(result)
    missing = {row["name"] for row in result["checks"] if not row["ready"]}
    assert {
        "design.vlm.endpoint_and_model",
        "design.vlm.env.VLM_API_KEY",
        "input.media",
        "cameras.colmap",
        "training.python_dependencies",
    } <= missing


def test_intake_identity_is_corrected_by_bounded_retry_without_rewriting_model_output(
    perception, tmp_path, monkeypatch
):
    evidence = tmp_path / "evidence"
    prepare_objects(perception, evidence, EvidenceConfig())
    inventory = verify_run(evidence, "objects.json")
    ids = {entry["asset_id"]: entry["object_id"] for entry in inventory["objects"]}
    calls = []

    class ConfusedIntake:
        def infer(self, stage, *args):
            calls.append(stage)
            asset = stage.split("-intake")[0]
            return {
                "object_id": ids[asset] if stage.endswith("-1") else asset,
                "category": "fixture",
                "summary": "fixture",
                "build_route": "background",
                "support_mode": "unknown",
                "reference_interpretation": "single",
                "evidence_refs": ["view-001"],
            }

    monkeypatch.setattr(
        "roboenv_perception.construction.design.make_vlm", lambda *a, **k: ConfusedIntake()
    )
    config = ConstructionConfig(vlm=VLMConfig(backend="replay"))
    config.design.max_revisions = 1
    output = tmp_path / "design"
    design_objects(evidence, output, config)
    assert verify_run(output, "designs.json")["all_designs_ready"]
    assert len(calls) == len(ids) * 2
    assert len(list(output.rglob("rejected.json"))) == len(ids)
    for asset, object_id in ids.items():
        assert read_json(output / "objects" / asset / "asset_brief.json")["object_id"] == object_id


def test_seed_visibility_uses_actual_tracks_and_never_holdout_appearance():
    from roboenv_perception.construction.reconstruction import filter_background_seeds

    records = [
        {
            "image_name": name,
            "split": split,
            "world_to_camera": np.eye(4).tolist(),
            "intrinsics": np.eye(3).tolist(),
        }
        for name, split in [
            ("a", "train"),
            ("b", "train"),
            ("occluder", "train"),
            ("holdout", "holdout"),
        ]
    ]
    points = {1: {"xyz": [1, 1, 1]}, 2: {"xyz": [2, 2, 1]}, 3: {"xyz": [3, 3, 1]}}
    images = {
        1: {"name": "a", "points": [(1, 1, 1), (2, 2, 2), (3, 3, 3)]},
        2: {"name": "b", "points": [(1, 1, 1), (2, 2, 2)]},
        3: {"name": "occluder", "points": []},
        4: {"name": "holdout", "points": [(3, 3, 3)]},
    }
    masks = {r["image_name"]: np.ones((5, 5), dtype=bool) for r in records}
    masks["occluder"][:] = False  # Unobserved projection is occluded, not a false background point.
    masks["b"][2, 2] = False  # An actual foreground/unknown observation must reject the track.
    masks["holdout"][:] = False
    colors = {r["image_name"]: np.full((5, 5, 3), 80, dtype=np.uint8) for r in records}
    colors["holdout"][:] = 255
    result = filter_background_seeds(points, images, records, masks, colors)
    assert result == [{"xyz": [1, 1, 1], "rgb": [80, 80, 80]}]
