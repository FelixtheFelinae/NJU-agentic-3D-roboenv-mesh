"""P0 schema/migration/Intake/selfcheck checks, no asset generation or model API."""

import copy
from pathlib import Path

import pytest

from roboenv_perception.construction.assembly_blueprint import (
    AssemblyBlueprint,
    PartInstance,
    legacy_projection,
    migrate_artisan,
    migrate_envcreator,
    parse_blueprint,
)
from roboenv_perception.construction.assembly_design import check_assembly, selfcheck_assembly
from roboenv_perception.construction.contracts import Blueprint
from roboenv_perception.io import read_json, sha256, write_json

FIXTURES = Path(__file__).parent / "fixtures/assembly_blueprint"


def laptop():
    return migrate_envcreator(
        read_json(FIXTURES / "laptop-legacy.json"), read_json(FIXTURES / "laptop-intake.json")
    )


def test_saved_laptop_migration_preserves_semantics_unknowns_and_original():
    old, intake = (
        read_json(FIXTURES / "laptop-legacy.json"),
        read_json(FIXTURES / "laptop-intake.json"),
    )
    bp = migrate_envcreator(old, intake)
    assert [p.name for p in bp.parts] == ["base_chassis", "lid_assembly"]
    assert len(bp.interfaces) == 2 and len(bp.mates) == 1
    assert bp.joints[0].mate_id == bp.mates[0].id
    assert bp.asset_brief.model_dump(mode="json") == intake
    assert bp.joints[0].travel.value is None
    assert all(i.local_frame.origin is None for i in bp.interfaces)
    assert bp.migration["original"] == old
    # Migration does not quietly "fix" the legacy at_max convention.
    assert bp.joints[0].rest == "at_max"
    assert bp.assumptions == old["assumptions"]
    assert bp.unresolved == old["unresolved"]
    assert legacy_projection(bp).model_dump(mode="json") == Blueprint.model_validate(
        old
    ).model_dump(mode="json")
    assert isinstance(parse_blueprint(old), Blueprint)
    assert isinstance(parse_blueprint(bp.model_dump(mode="json")), AssemblyBlueprint)
    assert check_assembly(bp)["status"] == "ready_with_assumptions"


def test_unknown_rest_can_be_described_without_silent_build_default():
    data = laptop().model_dump(mode="json")
    data["joints"][0]["rest"] = None
    bp = AssemblyBlueprint.model_validate(data)
    assert check_assembly(bp)["status"] == "ready_with_assumptions"
    with pytest.raises(ValueError, match="reference/rest"):
        legacy_projection(bp)


@pytest.mark.parametrize(
    "fault",
    [
        "definition",
        "interface",
        "mate",
        "endpoint",
        "joint_type",
        "constraint",
        "state",
        "evidence",
        "axis",
        "duplicate",
        "parent_cycle",
        "observed_motion",
        "prior_as_observation",
        "measured_as_prior",
    ],
)
def test_bad_graphs_and_provenance_are_rejected(fault):
    data = laptop().model_dump(mode="json")
    if fault == "definition":
        data["part_instances"][0]["definition_id"] = "missing"
    elif fault == "interface":
        data["mates"][0]["interfaces"][0] = "missing"
    elif fault == "mate":
        data["joints"][0]["mate_id"] = "missing"
    elif fault == "endpoint":
        data["interfaces"][1]["part"] = "base_chassis"
    elif fault == "joint_type":
        data["mates"][0]["type"] = "fixed"
    elif fault == "constraint":
        data["geometric_constraints"] = [
            {
                "id": "c",
                "type": "parallel",
                "part_refs": ["missing"],
                "basis": "assumed",
                "description": "bad",
            }
        ]
    elif fault == "state":
        data["states"] = [
            {
                "id": "s",
                "role": "observed",
                "description": "bad",
                "joint_positions": {"missing": {"unit": "rad"}},
            }
        ]
    elif fault == "evidence":
        data["interfaces"][0]["evidence_refs"] = ["made-up-source"]
    elif fault == "axis":
        data["interfaces"][0]["axis_local"] = [2, 0, 0]
    elif fault == "duplicate":
        data["interfaces"].append(copy.deepcopy(data["interfaces"][0]))
    elif fault == "parent_cycle":
        data["part_instances"][0]["parent"] = "lid_assembly"
    elif fault == "observed_motion":
        data["joints"][0]["evidence_kind"] = "observed"
    elif fault == "prior_as_observation":
        data["evidence"].append(
            {"id": "prior:hinge", "origin": "prior", "description": "knowledge"}
        )
        data["mates"][0].update(basis="observed", evidence_refs=["prior:hinge"])
    else:
        data["evidence"].append({"id": "prior:size", "origin": "prior", "description": "guess"})
        data["part_definitions"][0]["mass_kg"] = {
            "value": 1,
            "source": "measured",
            "evidence_refs": ["prior:size"],
        }
    with pytest.raises(ValueError):
        AssemblyBlueprint.model_validate(data)


def test_definition_can_be_reused_and_mate_graph_need_not_be_a_tree():
    data = laptop().model_dump(mode="json")
    data["part_instances"].append(
        PartInstance(name="spare_lid", definition_id=data["part_definitions"][1]["id"]).model_dump()
    )
    data["interfaces"].append(dict(data["interfaces"][1], id="spare_interface", part="spare_lid"))
    data["mates"].append(
        dict(
            data["mates"][0],
            id="second_mate",
            interfaces=[data["interfaces"][0]["id"], "spare_interface"],
        )
    )
    # A graph relation need not be compiled to a simulation joint at description time.
    bp = AssemblyBlueprint.model_validate(data)
    assert len(bp.part_definitions) == 2 and len(bp.parts) == 3 and len(bp.mates) == 2
    with pytest.raises(ValueError, match="silently drop mates"):
        legacy_projection(bp)


def test_coordinate_and_joint_position_units_are_checked():
    data = laptop().model_dump(mode="json")
    data["states"] = [
        {
            "id": "observed",
            "role": "observed",
            "description": "current angle unknown",
            "evidence_refs": [data["evidence"][0]["id"]],
            "joint_positions": {"hinge_joint": {"unit": "m"}},
        }
    ]
    with pytest.raises(ValueError, match="position unit"):
        AssemblyBlueprint.model_validate(data)
    data["states"] = []
    data["interfaces"][0]["local_frame"]["position_unit"] = "relative_width"
    with pytest.raises(ValueError, match="units differ"):
        AssemblyBlueprint.model_validate(data)


def test_unprovided_source_and_intake_changes_require_revision():
    bp = laptop()
    allowed = {e.id for e in bp.evidence}
    assert (
        check_assembly(bp, brief=bp.asset_brief, allowed_refs=allowed)["status"]
        == "ready_with_assumptions"
    )
    assert check_assembly(bp, allowed_refs=set())["status"] == "needs_revision"
    brief = bp.asset_brief.model_copy(update={"summary": "changed intake"})
    assert check_assembly(bp, brief=brief)["status"] == "needs_revision"


def test_artisan_adapter_preserves_metadata_dynamics_and_marks_numbers_assumed():
    old = {
        "blueprint_meta": {"schema_version": "1.0"},
        "target_dimensions": {"x": 0.3, "y": 0.2, "z": 0.1},
        "parts": [
            {
                "name": "base",
                "parent": None,
                "role": "body",
                "geometry_hint": "box",
                "material": "metal",
            },
            {
                "name": "lid",
                "parent": "base",
                "role": "lid",
                "geometry_hint": "panel",
                "material": "metal",
            },
        ],
        "joints": [
            {
                "name": "hinge",
                "type": "revolute",
                "parent": "base",
                "child": "lid",
                "axis": [1, 0, 0],
                "origin_hint": "rear",
                "travel": 1.5,
                "rest": "at_min",
                "dynamics_intent": {"hold": "holds_anywhere"},
            }
        ],
        "tasks": ["open"],
        "acceptance": ["lid stays attached"],
        "version_history": [{"version": "v1"}],
    }
    bp = migrate_artisan(old, object_id="imported", evidence_refs=["reference-image"])
    assert bp.target_dimensions_m.x.source == "assumed"
    assert bp.joints[0].travel.source == "assumed"
    assert bp.asset_brief.key_interactions == ["open"]
    assert bp.migration["original"] == old
    assert bp.physics_intent.fidelity == "kinematic"
    assert check_assembly(bp)["status"] == "ready_with_assumptions"
    with pytest.raises(ValueError, match="coupling migration"):
        migrate_artisan(
            dict(old, couplings=[{"anything": "unknown"}]), object_id="x", evidence_refs=["ref"]
        )


@pytest.mark.parametrize("decision", ["PASS", "REDO", "wrong_hash"])
def test_model_selfcheck_binds_candidate_and_retains_findings(tmp_path, decision):
    bp = laptop()

    class Reviewer:
        def infer(self, stage, prompt, images, schema, trace):
            assert "not independent QA" in prompt
            return {
                "blueprint_sha256": "0" * 64
                if decision == "wrong_hash"
                else sha256(tmp_path / "blueprint.json"),
                "decision": "REDO" if decision == "REDO" else "PASS",
                "findings": ["contradictory hinge position"] if decision == "REDO" else [],
                "summary": "fixture",
            }

    kwargs = dict(stage="selfcheck", brief=bp.asset_brief, allowed_refs={e.id for e in bp.evidence})
    if decision == "wrong_hash":
        with pytest.raises(ValueError, match="bind"):
            selfcheck_assembly(Reviewer(), bp, [], tmp_path, **kwargs)
    else:
        result = selfcheck_assembly(Reviewer(), bp, [], tmp_path, **kwargs)
        assert result["status"] == (
            "needs_revision" if decision == "REDO" else "ready_with_assumptions"
        )
        assert result["geometry_status"] == result["physics_status"] == "not_run"
        assert read_json(tmp_path / "selfcheck.json") == result


@pytest.mark.parametrize("reject_intake", [False, True])
@pytest.mark.parametrize("review_decision", ["PASS", "REDO"])
def test_component_executes_intake_author_selfcheck_and_publishes_one_blueprint(
    tmp_path, monkeypatch, reject_intake, review_decision
):
    from types import SimpleNamespace

    from roboenv_perception.components.operations import run
    from roboenv_perception.workbench.presentation import structure_view

    bp = laptop()
    calls = []
    evidence = {"object_id": bp.object_id, "views": [{"ref_id": e.id} for e in bp.evidence]}
    monkeypatch.setattr(
        "roboenv_perception.components.operations.observation",
        lambda *a: ({"asset_id": "asset-0002"}, evidence, []),
    )
    write_json(
        tmp_path / "scope/scope_plan.json",
        {"all_resolved": True, "objects": [{"asset_id": "asset-0002", "action": "build"}]},
    )
    # A real source binding; it is validated before and after the component.
    from roboenv_perception.components.sources import bind_source

    source_root = tmp_path / "source"
    write_json(
        source_root / "scope/scope_plan.json",
        {"all_resolved": True, "objects": [{"asset_id": "asset-0002", "action": "build"}]},
    )
    write_json(source_root / "evidence/objects.json", {"objects": []})
    sources = {
        role: bind_source({"id": "fixture-source", "root": str(source_root)}, role)
        for role in ["scope", "evidence"]
    }

    class Model:
        def infer(self, stage, prompt, images, schema, trace):
            calls.append(stage)
            if stage == "component-intake":
                assert bp.object_id in prompt
                return dict(
                    bp.asset_brief.model_dump(mode="json"),
                    object_id="wrong" if reject_intake else bp.object_id,
                )
            if stage == "component-intake-1":
                assert "Required correction:" in prompt
                return bp.asset_brief.model_dump(mode="json")
            if stage == "component-blueprint":
                return bp.model_dump(mode="json")
            return {
                "blueprint_sha256": sha256(tmp_path / "output/blueprint/blueprint.json"),
                "decision": review_decision,
                "findings": ["hinge contradicts evidence"] if review_decision == "REDO" else [],
                "summary": "description only",
            }

    from roboenv_perception.construction.contracts import ConstructionConfig

    monkeypatch.setattr(
        "roboenv_perception.components.operations.load_construction_config",
        lambda p: ConstructionConfig(),
    )
    monkeypatch.setattr(
        "roboenv_perception.components.operations.make_vlm", lambda *a, **k: Model()
    )

    def execute():
        return run(
            "blueprint",
            sources,
            tmp_path / "output",
            SimpleNamespace(construction_config=None),
            "describe laptop",
            object_id="asset-0002",
        )

    if review_decision == "REDO":
        with pytest.raises(ValueError, match="hinge contradicts evidence"):
            execute()
        assert read_json(tmp_path / "output/blueprint/selfcheck.json")["status"] == "needs_revision"
        assert not (tmp_path / "output/blueprint/identity.json").exists()
        return
    exports, status = execute()
    assert calls == ["component-intake"] + (["component-intake-1"] if reject_intake else []) + [
        "component-blueprint",
        "component-blueprint-selfcheck",
    ]
    if reject_intake:
        assert "object_id" in read_json(tmp_path / "output/blueprint/intake/rejected.json")["error"]
    assert status == "ready_with_assumptions"
    assert exports == {"blueprint": "blueprint/blueprint.json"}
    assert not (tmp_path / "output/blueprint/asset_brief.json").exists()
    view = structure_view(
        {"id": "fixture", "root": str(tmp_path)}, tmp_path / "output/blueprint/blueprint.json"
    )
    assert len(view["parts"]) == 2 and len(view["interfaces"]) == 2 and len(view["mates"]) == 1
    assert view["selfcheck"]["status"] == "ready_with_assumptions"


def test_new_description_cannot_silently_build_unknown_travel(tmp_path):
    from roboenv_perception.harness.geometry import _model_source, build_recipe

    bp = laptop()
    # Rest exists in this historical description, but travel is unobservable.
    assert bp.joints[0].rest is not None and bp.joints[0].travel.value is None
    with pytest.raises(ValueError, match="no silent default"):
        _model_source(bp, None)
    with pytest.raises(ValueError, match="no silent default"):
        build_recipe(bp, None, tmp_path / "must-not-exist", artisan_python=Path("/not-used"))
    assert not (tmp_path / "must-not-exist").exists()


def test_offline_migration_cli_writes_new_description_without_model_or_assets(tmp_path):
    from roboenv_perception.construction.assembly_blueprint import main

    output = tmp_path / "migration"
    args = [
        "--input",
        str(FIXTURES / "laptop-legacy.json"),
        "--source-format",
        "envcreator",
        "--intake",
        str(FIXTURES / "laptop-intake.json"),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    bp = AssemblyBlueprint.model_validate(read_json(output / "blueprint.json"))
    assert bp.migration["original"] == read_json(FIXTURES / "laptop-legacy.json")
    assert read_json(output / "selfcheck.json")["model_selfcheck_status"] == "not_run"
    assert read_json(output / "selfcheck.json")["blueprint_sha256"] == sha256(
        output / "blueprint.json"
    )
    assert set(p.name for p in output.iterdir()) == {"blueprint.json", "selfcheck.json"}
    with pytest.raises(FileExistsError):
        main(args)


def test_observed_state_requires_an_actual_observation_reference():
    data = laptop().model_dump(mode="json")
    data["states"] = [{"id": "open", "role": "observed", "description": "static open state"}]
    with pytest.raises(ValueError, match="observed state requires"):
        AssemblyBlueprint.model_validate(data)


def test_relative_description_can_be_saved_but_not_sent_to_metric_backend():
    data = laptop().model_dump(mode="json")
    data["coordinate_units"] = "relative_width"
    for interface in data["interfaces"]:
        interface["local_frame"]["position_unit"] = "relative_width"
    bp = AssemblyBlueprint.model_validate(data)
    with pytest.raises(ValueError, match="metric scale"):
        legacy_projection(bp)
    data["target_dimensions_m"]["x"] = {
        "value": 1,
        "source": "assumed",
        "evidence_refs": [data["evidence"][0]["id"]],
    }
    with pytest.raises(ValueError, match="relative coordinates cannot claim"):
        AssemblyBlueprint.model_validate(data)
