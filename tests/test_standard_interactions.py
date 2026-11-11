"""Boundary and migration tests; no physical acceptance is inferred from these tests."""

import json
from pathlib import Path

import pytest

from roboenv_perception.harness.assembly import AssemblyPlan
from roboenv_perception.harness.repair_policy import route_physics_failures
from roboenv_perception.standard_interactions.catalog import (
    StandardComponentError,
    digest,
    load_catalog,
)
from roboenv_perception.standard_interactions.compiler import (
    FASTENER_REF,
    PROTOCOL_REF,
    RUNTIME_REF,
    TOOL_REF,
    compile_connections,
    verify_compiled,
)


def binding(index="0"):
    return {
        "id": index,
        "standard_ref": FASTENER_REF,
        "tool_ref": TOOL_REF,
        "runtime_ref": RUNTIME_REF,
        "protocol_ref": PROTOCOL_REF,
        "screw": f"/World/screw_{index}",
        "support": "/World/body",
        "clamped_member": "/World/lid",
        "tool": "/World/WristTool",
        "origin_m": [0.01 * int(index), 0.0, 0.088],
        "engagement_m": 0.008,
        "removal_distance_m": 0.024,
        "evidence_refs": ["assumption:layout"],
        "assumptions": ["ideal bench and exact layout"],
    }


def test_layout_changes_only_binding_not_standard_components():
    a = compile_connections([binding()])
    b = compile_connections([binding("0"), binding("1"), binding("2"), binding("3")])
    verify_compiled(a)
    verify_compiled(b)
    assert a["connections"][0]["components_sha256"] == b["connections"][3]["components_sha256"]
    assert a["compiled_sha256"] != b["compiled_sha256"]
    assert b["physics_status"] == "not_run"
    # Engagement is an instance condition, not a different thread standard.
    c = binding()
    c["engagement_m"] = 0.006
    assert compile_connections([c])["connections"][0]["fastener_spec"]["lead_m"] == 0.0007


@pytest.mark.parametrize(
    "field,value",
    [
        ("standard_ref", "M5@unregistered"),
        ("axis", [1, 0, 0]),
        ("mate_kind", "through_nut"),
        ("tool_ref", "drill@1"),
    ],
)
def test_unregistered_or_unsupported_standard_cannot_compile(field, value):
    b = binding()
    b[field] = value
    with pytest.raises(StandardComponentError):
        compile_connections([b])


@pytest.mark.parametrize(
    "update",
    [
        {"support": "/World/screw_0"},
        {"origin_m": [float("nan"), 0, 0]},
        {"engagement_m": 0.02},
        {"removal_distance_m": 0.004},
        {"screw": "/World/../escape"},
        {"screw": "/World/nested/screw_0"},
        {"predecessors": ["missing"]},
        {"lead_m": 0.001},
    ],
)
def test_invalid_binding_and_parameter_injection_fail(update):
    with pytest.raises(ValueError):
        compile_connections([dict(binding(), **update)])


def test_duplicate_and_cyclic_layouts_fail():
    with pytest.raises(ValueError, match="duplicate"):
        compile_connections([binding(), binding()])
    with pytest.raises(ValueError, match="cyclic"):
        compile_connections(
            [dict(binding(), predecessors=["1"]), dict(binding("1"), predecessors=["0"])]
        )


def test_parameters_cannot_be_changed_even_with_recomputed_document_hash():
    compiled = compile_connections([binding()])
    compiled["connections"][0]["fastener_spec"]["lead_m"] = 0.002
    with pytest.raises(StandardComponentError, match="hash mismatch"):
        verify_compiled(compiled)
    compiled.pop("compiled_sha256")
    compiled["compiled_sha256"] = digest(compiled)
    with pytest.raises(StandardComponentError, match="registered components"):
        verify_compiled(compiled)


def test_loaded_catalog_is_not_a_mutable_global_and_profiles_are_explicit():
    kit = load_catalog()
    kit["fasteners"][FASTENER_REF]["lead_m"] = 0.001
    base = compile_connections([binding()])
    fast = compile_connections([binding()], tool_profile="accelerated_bench_120rpm")
    assert base["connections"][0]["fastener_spec"]["lead_m"] == 0.0007
    assert base["connections"][0]["components"]["tool"]["motor"]["max_command_rpm"] == 30
    assert fast["connections"][0]["components"]["tool"]["motor"]["max_command_rpm"] == 120
    with pytest.raises(StandardComponentError):
        compile_connections([binding()], tool_profile="silent_speed_increase")


def test_repair_routes_fail_closed_and_do_not_parse_issue_text():
    def row(asset, domain):
        return {
            "asset_id": asset,
            "status": "failed",
            "failure_domain": domain,
            "issues": ["a screw failed; wording does not control routing"],
            "report": "report.json",
        }

    report = {
        "results": [
            row("geometry", "object_geometry"),
            row("binding", "instance_binding"),
            row("standard", "standard_primitive"),
            row("sampler", "test_harness"),
            row("numeric", "numerical_diagnostic"),
            row("unknown", "unclassified"),
        ]
    }
    feedback, routes = route_physics_failures(report)
    assert set(feedback) == {"geometry", "binding"}
    assert sum(r["action"] == "stop_and_record_defect" for r in routes) == 4
    mixed = row("mixed", "object_geometry")
    mixed["tests"] = [
        {"status": "failed", "failure_domain": "object_geometry"},
        {"status": "failed", "failure_domain": "standard_primitive"},
    ]
    assert route_physics_failures({"results": [mixed]})[0] == {}


def standard_plan():
    b = binding()
    return AssemblyPlan.model_validate(
        {
            "schema_version": "assembly-hypotheses/2",
            "connections": [
                {
                    "id": "0",
                    "fastener": "screw_0",
                    "head_solid": "head",
                    "shaft_solid": "shaft",
                    "stack": ["lid"],
                    "mate": "body",
                    "mate_kind": "tapped_support",
                    "retention": "fixed_support",
                    "retention_basis": "ideal bench",
                    "evidence_refs": ["assumption:layout"],
                    "observed": ["existing layout"],
                    "assumptions": ["reference specimen"],
                    "alternatives": ["unidentified hardware"],
                    "selection_reason": "explicit migration",
                    **{
                        k: b[k]
                        for k in (
                            "standard_ref",
                            "tool_ref",
                            "runtime_ref",
                            "protocol_ref",
                            "origin_m",
                            "engagement_m",
                        )
                    },
                    "removal_distance_m": b["removal_distance_m"],
                }
            ],
        }
    )


def test_standard_plan_expands_pitch_and_rejects_override():
    plan = standard_plan()
    assert plan.connections[0].pitch_m == 0.0007
    raw = plan.model_dump(mode="json")
    raw["connections"][0]["pitch_m"] = 0.001
    with pytest.raises(ValueError, match="pitch override"):
        AssemblyPlan.model_validate(raw)
    raw = plan.model_dump(mode="json")
    raw["connections"][0]["tool_ref"] = None
    with pytest.raises(ValueError, match="all references"):
        AssemblyPlan.model_validate(raw)


def test_standard_geometry_freeze_allows_placement_but_not_shape_or_mass():
    from roboenv_perception.harness.geometry import GeometryRecipe
    from roboenv_perception.harness.repair_policy import enforce_standard_recipe

    recipe = GeometryRecipe(
        object_id="box",
        assembly=standard_plan(),
        solids=[
            {
                "name": "head",
                "part": "screw_0",
                "shape": "cylinder",
                "radius_m": 0.004,
                "length_m": 0.004,
                "source": "assumed",
                "evidence_refs": ["assumption:layout"],
            }
        ],
        part_masses_kg={"screw_0": 0.002},
    )
    frozen = enforce_standard_recipe(recipe, {})
    recipe.solids[0].center_m = (0.1, 0.2, 0.3)
    enforce_standard_recipe(recipe, frozen)
    changed = recipe.model_copy(deep=True)
    changed.solids[0].radius_m = 0.003
    with pytest.raises(StandardComponentError, match="frozen standard"):
        enforce_standard_recipe(changed, frozen)
    recipe.part_masses_kg["screw_0"] = 0.0001
    with pytest.raises(StandardComponentError):
        enforce_standard_recipe(recipe, frozen)


def test_real_v9_usd_recompilation_preserves_physics(tmp_path):
    pytest.importorskip("pxr.Usd")
    from roboenv_perception.standard_interactions.migrate_v9 import migrate

    source = (
        Path(__file__).resolve().parents[1]
        / "outputs/triangular-box-20261030-v9/delivery/triangular_box.usda"
    )
    if not source.exists():
        pytest.skip("local asset deliberately excluded from GitHub")
    output = tmp_path / "migrated"
    report = migrate(source, output)
    assert report["status"] == "equivalent"
    assert report["changed_prims"] == []
    assert report["physics_status"] == "not_run"
    verify_compiled(json.loads((output / "compiled_assembly.json").read_text()))


def test_harness_stops_before_exporting_modified_standard_geometry(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    import roboenv_perception.harness.run as run
    from roboenv_perception.harness.geometry import GeometryRecipe
    from roboenv_perception.io import read_json, write_json

    design, evidence, research = [tmp_path / n for n in ("design", "evidence", "research")]
    blueprint = {
        "object_id": "box",
        "kind": "rigid",
        "frame_convention": "metres; Z-up; front=+Y; local object frame",
        "parts": [
            {
                "name": name,
                "parent": None if name == "body" else "body",
                "role": "body",
                "geometry": "box",
                "material_appearance": "gray",
                "collision_intent": "solid",
                "evidence_refs": ["assumption:layout"],
            }
            for name in ("body", "lid", "screw_0")
        ],
        "joints": [
            {
                "name": "fixed_" + name,
                "parent": "body",
                "child": name,
                "type": "fixed",
                "origin_hint": "same object frame",
                "evidence_kind": "assumed",
                "evidence_refs": [],
            }
            for name in ("lid", "screw_0")
        ],
        "acceptance": ["push"],
    }
    write_json(
        design / "designs.json",
        {"objects": [{"asset_id": "a", "object_id": "box", "status": "design_ready"}]},
    )
    write_json(design / "objects/a/blueprint.json", blueprint)
    write_json(evidence / "objects/a/evidence.json", {"views": []})
    write_json(
        research / "research.json",
        {
            "objects": {
                "box": {"claims": [{"status": "candidate", "source_id": "assumption:layout"}]}
            }
        },
    )
    common = {"source": "assumed", "evidence_refs": ["assumption:layout"]}
    recipe = GeometryRecipe(
        object_id="box",
        assembly=standard_plan(),
        solids=[
            dict(
                common,
                name="head",
                part="screw_0",
                shape="cylinder",
                radius_m=0.004,
                length_m=0.004,
                center_m=[0, 0, 0.086],
            ),
            dict(
                common,
                name="shaft",
                part="screw_0",
                shape="cylinder",
                radius_m=0.002,
                length_m=0.0161,
                center_m=[0, 0, 0.07605],
            ),
            dict(common, name="body", part="body", shape="box", size_m=[0.1, 0.1, 0.08]),
            dict(common, name="lid", part="lid", shape="box", size_m=[0.1, 0.1, 0.004]),
        ],
        part_masses_kg={"body": 0.2, "lid": 0.1, "screw_0": 0.002},
        joint_placements=[
            {
                "joint": "fixed_" + name,
                "origin_parent_m": [0, 0, 0],
                "source": "assumed",
                "evidence_refs": ["assumption:layout"],
            }
            for name in ("lid", "screw_0")
        ],
    )
    seen, exports = [], []

    def infer(*args):
        seen.append(args)
        current = recipe.model_copy(deep=True)
        if len(seen) > 1:
            current.solids[0].radius_m = 0.003
        return current

    monkeypatch.setattr(run, "infer_recipe", infer)
    monkeypatch.setattr(
        run, "persist_audit", lambda *a, **kw: {"status": "geometrically_plausible"}
    )
    monkeypatch.setattr(run, "build_recipe", lambda *a, **kw: exports.append(a) or {})
    monkeypatch.setattr(
        run,
        "review_geometry",
        lambda *a: SimpleNamespace(
            decision="REDO", issues=["object hole is wrong"], model_dump=lambda **kw: {}
        ),
    )
    settings = run.HarnessSettings(
        perception_config=tmp_path / "p",
        construction_config=tmp_path / "c",
        artisan_python=tmp_path / "a",
        max_geometry_revisions=3,
    )
    out = tmp_path / "out"
    run._build_assets(
        out, design, evidence, research, settings, SimpleNamespace(vlm=None), time.monotonic() + 30
    )
    row = read_json(out / "assets.json")["objects"][0]
    assert len(seen) == 2 and len(exports) == 1
    assert row["failure_domain"] == "standard_primitive"
    assert row["attempts"][-1]["status"] == "blocked"
    assert (out / "a/candidate-001/assembly/standard_defect.json").is_file()


def test_generic_physics_does_not_certify_assembly_asset(tmp_path, monkeypatch):
    from test_harness_run import _rigid_disk_fixture

    import roboenv_perception.harness.physics as physics
    from roboenv_perception.harness.run import _verify
    from roboenv_perception.io import read_json, write_json

    assets, scene, settings, _, row = _rigid_disk_fixture(tmp_path)
    recipe = read_json(Path(row["recipe_ref"]))
    recipe["assembly"] = standard_plan().model_dump(mode="json")
    write_json(Path(row["recipe_ref"]), recipe)
    monkeypatch.setattr(
        physics,
        "run_physics_task",
        lambda *a: pytest.fail("generic rigid test cannot certify a screw"),
    )
    out = tmp_path / "physics"
    _verify(out, scene, assets, settings)
    result = read_json(out / "physics.json")
    assert result["status"] == "unverified"
    assert result["results"][0]["status"] == "unsupported"
    assert result["results"][0]["failure_domain"] == "test_harness"


def test_pydantic_wrapped_standard_defect_keeps_its_domain():
    from roboenv_perception.harness.repair_policy import is_standard_defect

    raw = standard_plan().model_dump(mode="json")
    raw["connections"][0]["pitch_m"] = 0.001
    with pytest.raises(ValueError) as caught:
        AssemblyPlan.model_validate(raw)
    assert is_standard_defect(caught.value)
