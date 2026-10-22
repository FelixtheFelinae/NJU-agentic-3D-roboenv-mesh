"""Synthetic persisted A-C example; no learned model or camera reconstruction."""

from pathlib import Path

from ..io import load_config, read_json, sha256, write_json
from ..video_demo import create_video_fixture
from ..video_pipeline import run_video
from .background import prepare_background
from .common import verify_run
from .contracts import ConstructionConfig
from .design import design_objects
from .evidence import prepare_objects
from .table import complete_table


def run_demo(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    video, fixture_config, _ = create_video_fixture(output / "fixture")
    perception = output / "perception"
    run_video(video, perception, load_config(fixture_config), "synthetic-construction")
    config = ConstructionConfig()
    evidence = output / "evidence"
    prepare_objects(perception, evidence, config.evidence)
    replay = output / "design-replay"
    replay.mkdir()
    config.vlm.backend = "replay"
    config.vlm.replay_dir = str(replay.resolve())
    for row in verify_run(evidence, "objects.json")["objects"]:
        asset = row["asset_id"]
        record = read_json(evidence / row["evidence_ref"])
        refs = [v["ref_id"] for v in record["views"]]
        background = record["category"] == "table"
        write_json(
            replay / f"{asset}-intake.json",
            {
                "object_id": row["object_id"],
                "category": record["category"],
                "summary": "Synthetic fixture response; no model inference",
                "build_route": "background" if background else "rigid",
                "support_mode": "grounded",
                "reference_interpretation": "viewpoints",
                "evidence_refs": refs,
                "unresolved": ["Real shape and dimensions not measured"],
            },
        )
        if background:
            continue
        blueprint = {
            "object_id": row["object_id"],
            "kind": "rigid",
            "frame_convention": "metres; Z-up; front=+Y; local object frame",
            "parts": [
                {
                    "name": "body",
                    "parent": None,
                    "role": "container body",
                    "geometry": "Synthetic hollow cup-like shell, not recovered geometry",
                    "hollow": True,
                    "material_appearance": "fixture colour",
                    "collision_intent": "Future hollow shell; not implemented",
                    "evidence_refs": refs,
                }
            ],
            "acceptance": ["Retain unknown metric scale", "Preserve intended cavity"],
            "unresolved": ["Dimensions, underside and scene pose unknown"],
        }
        from .contracts import Blueprint

        blueprint = Blueprint.model_validate(blueprint).model_dump(mode="json")
        write_json(replay / f"{asset}-blueprint-0.json", blueprint)
        write_json(
            replay / f"{asset}-review-0.json",
            {
                "blueprint_sha256": sha256(replay / f"{asset}-blueprint-0.json"),
                "decision": "PASS",
                "findings": [],
                "summary": "Synthetic fixture self-review only",
            },
        )
    designs = output / "design"
    design_objects(evidence, designs, config)
    design_index = verify_run(designs, "designs.json")
    if not design_index["all_designs_ready"]:
        raise RuntimeError("synthetic construction design failed; inspect designs.json")
    background = output / "background"
    prepare_background(perception, background, config.background, designs)
    frames = verify_run(background, "background.json")["frames"]
    spec_path = output / "table-spec.json"
    write_json(
        spec_path,
        {
            "width_m": 1.59,
            "depth_m": 0.71,
            "pixels_per_metre": 100,
            "fill": "telea",
            "max_fill_fraction": 0.25,
            "views": [
                {
                    "frame_id": f["frame_id"],
                    "corners_xy": [[0, 40], [159, 40], [159, 111], [0, 111]],
                }
                for f in frames
            ],
        },
    )
    complete_table(background, spec_path, output / "table")
    completion = verify_run(output / "table", "completion.json")
    result = {
        "output": str(output.resolve()),
        "fixture": "synthetic_only",
        "frames": len(frames),
        "objects": design_index["objects"],
        "atlas_unobserved_fraction": completion["unobserved_fraction_before_fill"],
        "holdout_used_for_fusion": completion["holdout_pixels_used_for_fusion"],
        "model_inference": "not_run",
        "camera_reconstruction": "not_run",
        "gaussian_training": "not_run",
        "independent_acceptance": False,
    }
    write_json(output / "summary.json", result)
    return result
