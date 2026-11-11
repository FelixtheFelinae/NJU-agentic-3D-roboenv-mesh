"""Real analytic feature checks, including deliberately invalid assembly controls."""

import copy
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from roboenv_perception.components.operations import geometry_operation
from roboenv_perception.construction.assembly_blueprint import AssemblyBlueprint
from roboenv_perception.harness.geometric_verifier import (
    GeometryConstraintError,
    VerificationPolicy,
    bound_feedback,
    persist_verification,
    verify_geometry,
)
from roboenv_perception.harness.geometry import GeometryRecipe, build_recipe
from roboenv_perception.io import read_json, write_json

FIXTURE = Path(__file__).parent / "fixtures/mechanical_geometry"


def fixture():
    return AssemblyBlueprint.model_validate(
        read_json(FIXTURE / "blueprint.json")
    ), GeometryRecipe.model_validate(read_json(FIXTURE / "recipe.json"))


def test_correct_finite_fit_and_actual_plane_relations():
    bp, recipe = fixture()
    result = verify_geometry(bp, recipe)
    assert result["status"] == "passed" and len(result["checks"]) == 5
    assert result["physics_status"] == result["exported_mesh_status"] == "not_run"
    gap = next(r for r in result["checks"] if r["requirement_id"] == "gap")
    assert gap["metrics"]["nominal_radial_clearance_m"] == pytest.approx(0.0002)
    assert gap["metrics"]["minimum_radial_clearance_m"] == pytest.approx(0.0002)
    assert gap["metrics"]["axial_overlap_m"] == pytest.approx(0.02)
    assert result["feedback"] == []


@pytest.mark.parametrize(
    "fault", ["offset", "tilt", "interference", "no_insertion", "too_loose", "shallow"]
)
def test_negative_controls_fail_and_report_concrete_repairs(fault):
    bp, recipe = fixture()
    if fault == "offset":
        recipe.joint_placements[0].origin_parent_m = (0.001, 0, 0)
    elif fault == "tilt":
        recipe.solids[1].rotation_rpy = (0.1, 0, 0)
    elif fault == "interference":
        recipe.solids[0].inner_radius_m = 0.0048
    elif fault == "no_insertion":
        recipe.joint_placements[0].origin_parent_m = (0, 0, 0.1)
    elif fault == "shallow":
        recipe.joint_placements[0].origin_parent_m = (0, 0, 0.025)
    else:
        recipe.solids[0].inner_radius_m = 0.007
    result = verify_geometry(bp, recipe)
    assert result["status"] == "failed"
    assert any(r["requirement_id"] == "gap" for r in result["feedback"])
    assert all(r["repair_target"] == "geometry" for r in result["feedback"])
    assert result["input_sha256"] != verify_geometry(*fixture())["input_sha256"]


def test_rotation_and_antiparallel_axes_are_geometrically_equivalent():
    bp, recipe = fixture()
    # Rotate shaft and hole to +Y/-Y; use actual solid transforms, not declaration axes.
    recipe.solids[0].rotation_rpy = (math.pi / 2, 0, 0)
    recipe.solids[1].rotation_rpy = (-math.pi / 2, 0, 0)
    bp.joints[0].axis_child = (0, 1, 0)
    assert verify_geometry(bp, recipe)["status"] == "passed"


@pytest.mark.parametrize(
    "fault",
    [
        "unbound",
        "fake_hole",
        "candidate_hinge",
        "wrong_owner",
        "duplicate",
        "part_only",
        "unknown_parameter",
        "unit",
        "state_pose",
    ],
)
def test_unresolved_geometry_is_never_a_pass(fault):
    bp, recipe = fixture()
    if fault == "unbound":
        recipe.interface_bindings = []
    elif fault == "fake_hole":
        recipe.solids[0].shape = "cylinder"
        recipe.solids[0].inner_radius_m = None
    elif fault == "candidate_hinge":
        bp.interfaces[0].kind = "hinge_region"
    elif fault == "wrong_owner":
        recipe.interface_bindings[0].solid = "journal_solid"
    elif fault == "duplicate":
        recipe.interface_bindings.append(copy.deepcopy(recipe.interface_bindings[0]))
    elif fault == "part_only":
        bp.geometric_constraints[0].interface_refs = []
        bp.geometric_constraints[0].part_refs = ["housing", "shaft"]
    elif fault == "unknown_parameter":
        bp.geometric_constraints[1].parameters["minimum_radial_clearance"].quantity.value = None
        bp.geometric_constraints[1].parameters[
            "minimum_radial_clearance"
        ].quantity.source = "unknown"
    elif fault == "unit":
        bp.geometric_constraints[1].parameters["minimum_radial_clearance"].unit = "rad"
    else:
        bp.part_instances[1].parent = None
    result = verify_geometry(bp, recipe)
    assert result["status"] == "unresolved"
    assert result["feedback"]
    if fault == "candidate_hinge":
        assert any(r["repair_target"] == "blueprint" for r in result["feedback"])


def test_recipe_rejects_fake_cross_part_and_duplicate_bindings():
    bp, recipe = fixture()
    recipe.interface_bindings[0].solid = "journal_solid"
    with pytest.raises(ValueError, match="different part"):
        recipe.check_against(bp, {bp.evidence[0].id})


def test_unresolved_part_only_constraint_routes_back_to_description(tmp_path):
    bp, recipe = fixture()
    bp.geometric_constraints[0].interface_refs = []
    bp.geometric_constraints[0].part_refs = ["housing", "shaft"]
    with pytest.raises(GeometryConstraintError) as error:
        persist_verification(tmp_path, bp, recipe, require_pass=True)
    assert any(r["repair_target"] == "blueprint" for r in error.value.report["feedback"])
    assert read_json(tmp_path / "geometric_verification.json")["status"] == "unresolved"


def test_build_rejects_bad_actual_geometry_before_renderer_or_artisan(tmp_path, monkeypatch):
    bp, recipe = fixture()
    recipe.solids[0].inner_radius_m = 0.0048
    monkeypatch.setattr(
        "roboenv_perception.harness.geometry.build_blender_meshes",
        lambda *a, **kw: pytest.fail("invalid fit reached renderer"),
    )
    with pytest.raises(GeometryConstraintError):
        build_recipe(bp, recipe, tmp_path / "built", artisan_python=Path("/not-used"))
    assert not (tmp_path / "built").exists()
    assert read_json(tmp_path / "geometric_check/geometric_verification.json")["status"] == "failed"


def test_native_component_audit_and_build_recompute_instead_of_trusting_status(
    tmp_path, monkeypatch
):
    bp, recipe = fixture()
    paths = {k: tmp_path / (k + ".json") for k in ["blueprint", "recipe"]}
    write_json(paths["blueprint"], bp.model_dump(mode="json"))
    write_json(paths["recipe"], recipe.model_dump(mode="json"))
    write_json(tmp_path / "evidence_refs.json", {"refs": [bp.evidence[0].id]})
    exports, status = geometry_operation(
        "geometry_audit", paths, tmp_path / "audit", None, "fit", None, None
    )
    assert status == "passed" and exports == {"geometric_verification": "geometric_check"}
    # A stale passed report must not permit changed bore dimensions.
    recipe.solids[0].inner_radius_m = 0.0048
    write_json(paths["recipe"], recipe.model_dump(mode="json"))
    monkeypatch.setattr(
        "roboenv_perception.harness.geometry.build_recipe",
        lambda *a, **kw: pytest.fail("stale audit trusted"),
    )
    with pytest.raises(GeometryConstraintError):
        geometry_operation("build", paths, tmp_path / "build", SimpleNamespace(), "fit", None, None)
    assert (
        read_json(tmp_path / "build/geometric_check/geometric_verification.json")["status"]
        == "failed"
    )


def test_invalid_policy_is_rejected():
    with pytest.raises(ValueError):
        VerificationPolicy(axis_tolerance_m=-1)


def test_self_identical_plane_cannot_satisfy_orthogonal_requirement():
    bp, recipe = fixture()
    bp.geometric_constraints[2].interface_refs = ["housing_top", "housing_top"]
    result = verify_geometry(bp, recipe)
    assert result["status"] == "unresolved"
    assert any("repeats" in r["message"] for r in result["feedback"])


@pytest.mark.parametrize("fault", ["content", "blueprint", "recipe"])
def test_feedback_rejects_unrelated_or_modified_candidates(fault):
    bp, recipe = fixture()
    recipe.solids[0].inner_radius_m = 0.0048
    report = verify_geometry(bp, recipe)
    assert bound_feedback(report, bp, recipe)
    if fault == "content":
        report["feedback"][0]["message"] = "altered finding"
    elif fault == "blueprint":
        bp.assumptions.append("changed interpretation")
    else:
        recipe.solids[0].inner_radius_m = 0.0052
    with pytest.raises(ValueError, match="altered|different"):
        bound_feedback(report, bp, recipe)


@pytest.mark.parametrize("description_error", [False, True])
def test_full_harness_routes_verifier_feedback_to_the_correct_stage(
    tmp_path, monkeypatch, description_error
):
    import time

    import roboenv_perception.harness.run as harness

    bp, recipe = fixture()
    if description_error:
        bp.geometric_constraints[0].interface_refs = []
        bp.geometric_constraints[0].part_refs = ["housing", "shaft"]
    else:
        recipe.solids[0].inner_radius_m = 0.0048
    designs, evidence, research = [tmp_path / name for name in ["designs", "evidence", "research"]]
    write_json(
        designs / "designs.json",
        {
            "objects": [
                {"asset_id": "asset-0001", "object_id": bp.object_id, "status": "design_ready"}
            ]
        },
    )
    write_json(designs / "objects/asset-0001/blueprint.json", bp.model_dump(mode="json"))
    write_json(evidence / "objects/asset-0001/evidence.json", {"views": []})
    write_json(research / "research.json", {"objects": {}})
    seen = []

    def infer(*args, **kwargs):
        seen.append(kwargs.get("geometric_feedback", []))
        return recipe

    def build(blueprint, candidate, output, **kwargs):
        persist_verification(
            output.parent / "geometric_check", blueprint, candidate, require_pass=True
        )
        pytest.fail("all candidates intentionally fail before construction")

    monkeypatch.setattr(harness, "infer_recipe", infer)
    monkeypatch.setattr(harness, "build_recipe", build)
    settings = SimpleNamespace(
        max_geometry_revisions=2,
        artisan_python=Path("/unused"),
        blender=None,
        build_timeout_seconds=10,
        standard_component_root=None,
    )
    out = tmp_path / "assets"
    harness._build_assets(
        out, designs, evidence, research, settings, SimpleNamespace(vlm=None), time.monotonic() + 30
    )
    row = read_json(out / "assets.json")["objects"][0]
    if description_error:
        assert len(seen) == 1 and row["repair_target"] == "blueprint_or_verifier"
        assert (out / "asset-0001/candidate-000/geometric_check/repair_request.json").is_file()
    else:
        assert len(seen) == 2 and seen[1]
        assert all(r["repair_target"] == "geometry" for r in seen[1])
        assert row["attempts"][0]["geometric_feedback"] == seen[1]


def test_native_recipe_revision_receives_hash_bound_structured_feedback(tmp_path, monkeypatch):
    import json

    from roboenv_perception.components.operations import run
    from roboenv_perception.components.sources import bind_source
    from roboenv_perception.construction.contracts import ConstructionConfig

    bp, candidate = fixture()
    candidate.solids[0].inner_radius_m = 0.0048
    source = tmp_path / "source"
    write_json(source / "blueprint.json", bp.model_dump(mode="json"))
    write_json(source / "recipe.json", candidate.model_dump(mode="json"))
    write_json(source / "evidence/objects.json", {"objects": []})
    persist_verification(source / "check", bp, candidate)
    write_json(
        source / "component.json",
        {
            "exports": {
                "blueprint": "blueprint.json",
                "recipe": "recipe.json",
                "evidence": "evidence",
                "geometric_verification": "check",
            }
        },
    )
    record = {"id": "controlled", "root": str(source)}
    bindings = {
        role: bind_source(record, "recipe" if role == "previous" else role)
        for role in ["blueprint", "evidence", "previous", "geometric_verification"]
    }
    received = []

    class Model:
        def infer(self, stage, prompt, images, schema, trace):
            context = json.loads(prompt.split("\nJSON schema:\n")[0])
            received.append(context["geometric_feedback"])
            repaired = candidate.model_copy(deep=True)
            repaired.solids[0].inner_radius_m = 0.0052
            return repaired.model_dump(mode="json")

    monkeypatch.setattr(
        "roboenv_perception.components.operations.observation",
        lambda *a: ({}, {"object_id": bp.object_id, "views": [{"ref_id": bp.evidence[0].id}]}, []),
    )
    monkeypatch.setattr(
        "roboenv_perception.components.operations.load_construction_config",
        lambda p: ConstructionConfig(),
    )
    monkeypatch.setattr("roboenv_perception.harness.geometry.make_vlm", lambda *a, **kw: Model())
    exports, status = run(
        "geometry",
        bindings,
        tmp_path / "repaired",
        SimpleNamespace(construction_config=None),
        "repair hole only",
        object_id=bp.object_id,
    )
    repaired = GeometryRecipe.model_validate(read_json(tmp_path / "repaired" / exports["recipe"]))
    assert received and received[0]
    assert verify_geometry(bp, repaired)["status"] == "passed"
    assert candidate.solids[0].inner_radius_m == 0.0048
