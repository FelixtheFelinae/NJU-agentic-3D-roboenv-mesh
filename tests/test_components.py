"""Protocol and artifact fixtures; actual GPU/model evidence is recorded separately."""

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from roboenv_perception.artifacts.registry import artifact_type, publish
from roboenv_perception.components.perception import edit, write_frame
from roboenv_perception.components.sources import bind_source, source_path
from roboenv_perception.construction.common import new_run, read_mask, verify_run
from roboenv_perception.contracts import InstanceLabel
from roboenv_perception.io import read_json, sha256, write_json
from roboenv_perception.segmentation import Detection
from roboenv_perception.video_validation import validate_video_contents
from roboenv_perception.workbench.config import Settings
from roboenv_perception.workbench.jobs import Jobs
from roboenv_perception.workbench.presentation import workspace
from roboenv_perception.workbench.server import create_app
from roboenv_perception.workbench.store import Store


def perception_fixture(root):
    image = root.parent / "image.png"
    Image.new("RGB", (32, 24), "#abcabc").save(image)
    masks = [np.zeros((24, 32), dtype=bool) for _ in range(2)]
    masks[0][3:15, 4:15] = True
    masks[1][10:20, 10:25] = True
    ds = [
        Detection([f"c{i + 1:03}"], m, 0.9, "fixture", f"i{i + 1:04}") for i, m in enumerate(masks)
    ]
    labels = {
        d.instance_id: InstanceLabel(
            instance_id=d.instance_id,
            category="bottle",
            visual_route="mesh_candidate",
            physical_role="rigid_body_candidate",
        )
        for d in ds
    }
    with new_run(root, "fixture", {}):
        write_frame(
            root,
            "f000000",
            image,
            ds,
            [{}, {}],
            labels,
            object_ids={"i0001": "object-one", "i0002": "object-two"},
        )
    return masks


def test_mask_correction_is_actual_union_immutable_and_exclusion_routes(tmp_path):
    source = tmp_path / "source"
    masks = perception_fixture(source)
    before = sha256(source / "artifacts.json")
    edit(source, tmp_path / "merged", {"merge": ["i0001", "i0002"]}, "user")
    result = verify_run(tmp_path / "merged", "objects/index.json")
    assert len(result) == 1 and result[0]["object_id"] == "object-one"
    assert np.array_equal(
        read_mask(tmp_path / "merged" / result[0]["mask_ref"]), masks[0] | masks[1]
    )
    assert result[0]["annotation_source"] == "user"
    assert sha256(source / "artifacts.json") == before
    edit(source, tmp_path / "excluded", {"exclude": ["i0001"]}, "user")
    result = verify_run(tmp_path / "excluded", "objects/index.json")
    assert result[0]["visual_route"] == "exclude_transient"
    assert read_mask(tmp_path / "excluded/background/transient.png").sum() > 0
    with pytest.raises(ValueError, match="本次分割"):
        edit(source, tmp_path / "bad", {"merge": ["i9999", "i0001"]}, "user")
    with pytest.raises(ValueError, match="未支持字段"):
        edit(
            source,
            tmp_path / "bad-semantic",
            {"labels": {"i0001": {"physical_parameters": {"mass": 5}}}},
            "user",
        )


def test_multiframe_correction_owns_valid_track_refs_and_preserves_time(tmp_path):
    single = tmp_path / "single"
    perception_fixture(single)
    source = tmp_path / "multi"
    with new_run(source, "fixture", {}):
        frames = []
        tracks = {}
        import shutil

        for index in range(2):
            prefix = f"frames/f{index:06}/"
            dest = source / prefix
            shutil.copytree(single, dest)
            data = read_json(dest / "objects/index.json")
            for row in data:
                for key in ("mask_ref", "rgba_ref", "context_ref"):
                    row[key] = prefix + row[key]
                row["frame_id"] = f"f{index:06}"
                ref = prefix + f"objects/{row['instance_id']}/observation.json"
                write_json(source / ref, row)
                tracks.setdefault(
                    row["object_id"], {"object_id": row["object_id"], "observations": []}
                )["observations"].append(
                    {
                        "frame_id": row["frame_id"],
                        "timestamp_seconds": float(index),
                        "observation_ref": ref,
                    }
                )
            write_json(dest / "objects/index.json", data)
            bg = read_json(dest / "background/index.json")
            bg["masks"] = {k: prefix + v for k, v in bg["masks"].items()}
            write_json(dest / "background/index.json", bg)
            frames.append(
                {
                    "frame_id": f"f{index:06}",
                    "timestamp_seconds": float(index),
                    "image_ref": prefix + "inputs/frame.png",
                    "image_sha256": sha256(dest / "inputs/frame.png"),
                    "objects_ref": prefix + "objects/index.json",
                    "background_ref": prefix + "background/index.json",
                }
            )
        write_json(source / "tracks/index.json", list(tracks.values()))
        write_json(
            source / "scene.json",
            {"kind": "video", "frames": frames, "tracks_ref": "tracks/index.json"},
        )
    errors = []
    validate_video_contents(source, read_json(source / "scene.json"), errors)
    assert not errors
    edit(source, tmp_path / "edited", {"frame_id": "f000001", "merge": ["i0001", "i0002"]}, "user")
    scene = verify_run(tmp_path / "edited", "scene.json")
    errors = []
    validate_video_contents(tmp_path / "edited", scene, errors)
    assert not errors
    assert [f["timestamp_seconds"] for f in scene["frames"]] == [0.0, 1.0]
    assert len(read_json(tmp_path / "edited" / scene["frames"][0]["objects_ref"])) == 2
    assert len(read_json(tmp_path / "edited" / scene["frames"][1]["objects_ref"])) == 1


def component(
    store, task, operation, outputs, *, parent=None, sources=None, lineage=None, object_id=None
):
    row = store.new_run(task, operation, parent)
    root = Path(row["root"])
    out = root / "output"
    out.mkdir(exist_ok=True)
    for ref, data in outputs.values():
        write_json(out / ref, data)
    exports = {
        role: str(Path(ref).parent) if Path(ref).name != "blueprint.json" else ref
        for role, (ref, _) in outputs.items()
    }
    manifest = publish(out, operation, exports, sources=sources or {}, object_id=object_id)
    manifest["lineage"] = {**(lineage or {}), **{k: row["id"] for k in exports}}
    write_json(out / "component.json", manifest)
    write_json(
        root / "spec.json",
        {"operation": operation, "sources": sources or {}, "object_id": object_id},
    )
    store.status(row["id"], "completed")
    return store.run(row["id"])


def test_transitive_dependencies_reject_changed_models(tmp_path):
    store = Store(tmp_path / "state", None)
    task = store.bind("s")["id"]
    built = component(
        store, task, "build", {"built": ("built/candidate.json", {"usd": "model.usd"})}
    )
    binding = bind_source(built, "built")
    asset = component(
        store,
        task,
        "geometry_review",
        {"assets": ("assets/assets.json", {"objects": []})},
        sources={"built": binding},
    )
    selected = bind_source(asset, "assets")
    source_path(selected)
    write_json(Path(built["root"]) / "output/built/candidate.json", {"usd": "changed.usd"})
    with pytest.raises(ValueError, match="发生变化"):
        source_path(selected)


def test_projection_marks_prior_physics_stale_after_blueprint_change(tmp_path):
    store = Store(tmp_path / "state", None)
    task = store.bind("s")["id"]
    bp = component(
        store, task, "blueprint", {"blueprint": ("blueprint/blueprint.json", {"parts": []})}
    )
    scene = component(
        store,
        task,
        "scene",
        {"scene": ("scene/scene_result.json", {})},
        parent=bp["id"],
        lineage={"blueprint": bp["id"]},
    )
    physical = component(
        store,
        task,
        "physics",
        {"physics": ("physics/physics.json", {"status": "passed", "results": []})},
        parent=scene["id"],
        lineage={"blueprint": bp["id"], "scene": scene["id"]},
    )
    updated = component(
        store,
        task,
        "blueprint",
        {"blueprint": ("blueprint/blueprint.json", {"parts": []})},
        parent=physical["id"],
    )
    view = workspace(store, task, updated["id"])
    assert set(view["stale"]) >= {"scene", "physics"}
    assert view["validation"]["stale"] is True
    assert "physics" not in view["sources"] and view["sources"]["blueprint"] == updated["id"]
    old = workspace(store, task, physical["id"])
    assert not old["validation"]["stale"]


def test_ply_type_prevents_gaussians_being_rendered_as_triangle_mesh(tmp_path):
    for name, header, expected in [
        (
            "gaussian",
            "property float f_dc_0\nproperty float opacity\nproperty float scale_0",
            "gaussian_scene",
        ),
        ("points", "element vertex 4", "point_cloud"),
        ("mesh", "element vertex 4\nelement face 2", "mesh"),
    ]:
        path = tmp_path / (name + ".ply")
        path.write_text("ply\n" + header + "\nend_header\n")
        assert artifact_type(path) == expected


def test_cancellation_before_start_survives_restart(tmp_path):
    cfg = Settings(dsh_root=tmp_path, state_root=tmp_path / "state")
    store = Store(cfg.state_root, None)
    task = store.bind("s")
    jobs = Jobs(store, cfg)
    jobs.cancel_invocation("s", "cancelled-call")
    jobs.close()
    restarted = Jobs(Store(cfg.state_root, None), cfg)
    with pytest.raises(ValueError, match="已取消"):
        restarted.submit(task["id"], "task", [], "cancelled-call", "s", operation="ingest")
    restarted.close()


def test_event_stream_resume_skips_malformed_and_oversized_records(tmp_path):
    app = create_app(
        Settings(dsh_root=tmp_path, state_root=tmp_path / "state", import_root=None), "token"
    )
    store = app.state.store
    task = store.bind("s")
    run = store.new_run(task["id"], "fixture")
    journal = Path(run["root"]) / "events.jsonl"
    first = b'{"event":"first"}\n'
    journal.write_bytes(
        first + b"invalid\n" + b"x" * (1024 * 1024 + 5) + b"\n" + b'{"event":"last"}\n'
    )
    store.status(run["id"], "completed")
    with TestClient(app) as client:
        r = client.get(
            "/api/runs/" + run["id"] + "/events",
            headers={"Authorization": "Bearer token", "Last-Event-ID": str(len(first))},
        )
        assert r.status_code == 200
        assert '"last"' in r.text and '"first"' not in r.text
        assert "event: status" in r.text


def test_reused_source_graph_does_not_duplicate_nested_snapshots(tmp_path):
    """The real laptop retry exceeded 4 MiB from repeated nested dependencies."""
    store = Store(tmp_path / "state", None)
    task = store.bind("s")["id"]
    previous = component(
        store, task, "geometry", {"recipe": ("geometry/recipe.json", {"version": 0})}
    )
    for index in range(18):
        bound = bind_source(previous, "recipe")
        previous = component(
            store,
            task,
            "geometry",
            {"recipe": ("geometry/recipe.json", {"version": index + 1})},
            sources={"previous": bound, "same_dependency": bound},
        )
    bound = bind_source(previous, "recipe")
    import json

    assert len(json.dumps(bound)) < 60000
    source_path(bound)


def test_native_observation_returns_review_findings_and_redacts_failure_tail(tmp_path):
    from roboenv_perception.workbench.observations import agent_observation

    store = Store(tmp_path / "state", None)
    task = store.bind("s")["id"]
    row = component(
        store,
        task,
        "blueprint_review",
        {
            "blueprint_review": (
                "review/review.json",
                {
                    "decision": "REDO",
                    "findings": ["screen hinge is missing"],
                    "api_key": "private-value",
                },
            )
        },
    )
    value = agent_observation(row)
    assert value["output_sources"]["blueprint_review"] == row["id"]
    assert value["data"]["blueprint_review"]["findings"] == ["screen hinge is missing"]
    assert value["data"]["blueprint_review"]["api_key"] == "[redacted]"
    store.status(row["id"], "failed")
    (Path(row["root"]) / "worker.log").write_text(
        "TypeError: execution record missing\nBearer dont-expose-this-key"
    )
    value = agent_observation(store.run(row["id"]))
    assert "execution record missing" in value["failure"]["worker_log_tail"]
    assert "dont-expose" not in value["failure"]["worker_log_tail"]


def test_private_execution_spec_is_not_limited_by_browser_preview_size(tmp_path):
    import json

    from roboenv_perception.components.sources import execution_record

    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"run_id": "actual-run", "description": "x" * (4 * 1024 * 1024)}))
    assert execution_record(path)["run_id"] == "actual-run"


def test_partial_failed_build_is_marked_stale_after_recipe_revision(tmp_path):

    store = Store(tmp_path / "state", None)
    task = store.bind("s")["id"]
    recipe = component(store, task, "geometry", {"recipe": ("geometry/recipe.json", {})})
    failed = store.new_run(task, "failed build", recipe["id"])
    root = Path(failed["root"])
    write_json(root / "spec.json", {"operation": "build", "lineage": {"recipe": recipe["id"]}})
    write_json(
        root / "output/built/visual/result.json", {"meshes": [], "previews": ["preview.png"]}
    )
    Image.new("RGB", (4, 4)).save(root / "output/built/visual/preview.png")
    store.status(failed["id"], "failed")
    updated = component(
        store,
        task,
        "geometry",
        {"recipe": ("geometry/recipe.json", {"new": True})},
        parent=failed["id"],
    )
    view = workspace(store, task, updated["id"])
    assert view["model"]["partial"] is True
    assert view["model"]["stale"] is True
    assert "中间预览" in view["model"]["message"]
    assert "built" not in view["sources"]
