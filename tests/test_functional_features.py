"""Check real builder features against ideal joint axes, including false-PASS controls."""

import math

import numpy as np
import pytest
from pydantic import ValidationError
from test_geometric_verifier import fixture

from roboenv_perception.construction.assembly_blueprint import GeometricConstraint, Interface
from roboenv_perception.construction.assembly_readiness import analyze_readiness, revision_findings
from roboenv_perception.harness.geometric_verifier import rotation, verify_geometry
from roboenv_perception.harness.geometry import InterfaceBinding


def case(relation="parallel", target="edge", edge_axis="z"):
    bp, recipe = fixture()
    ref = bp.evidence[0].id
    for name, kind in [("hinge_axis", "axis"), ("border", target)]:
        bp.interfaces.append(
            Interface(
                id=name,
                part="housing",
                kind=kind,
                basis="assumed",
                description="controlled test feature",
                evidence_refs=[ref],
            )
        )
    recipe.interface_bindings.append(
        InterfaceBinding(interface_id="hinge_axis", feature="joint_axis", joint=bp.joints[0].name)
    )
    binding = {"feature": "box_edge", "edge_axis": edge_axis, "edge_sides": ["max", "max"]}
    if target == "plane":
        binding = {"feature": "box_z_max"}
    elif target == "axis":
        binding = {"feature": "cylinder_axis"}
    recipe.interface_bindings.append(
        InterfaceBinding(
            interface_id="border", solid="bushing" if target == "axis" else "datum", **binding
        )
    )
    bp.geometric_constraints.append(
        GeometricConstraint(
            id="axis_border",
            type=relation,
            interface_refs=["hinge_axis", "border"],
            basis="assumed",
            evidence_refs=[ref],
            description="explicit feature relation",
        )
    )
    return bp, recipe


def result(bp, recipe):
    report = verify_geometry(bp, recipe)
    return next(r for r in report["checks"] if r["requirement_id"] == "axis_border")


@pytest.mark.parametrize(
    "relation,target,direction,status",
    [
        ("parallel", "edge", "z", "passed"),
        ("parallel", "edge", "x", "failed"),
        ("orthogonal", "edge", "x", "passed"),
        ("orthogonal", "edge", "z", "failed"),
        ("parallel", "plane", "z", "failed"),
        ("orthogonal", "plane", "z", "passed"),
        ("coaxial", "axis", "z", "passed"),
        ("coaxial", "plane", "z", "unresolved"),
        ("clearance", "axis", "z", "unresolved"),
    ],
)
def test_roles_have_consistent_preflight_and_geometric_meaning(relation, target, direction, status):
    bp, recipe = case(relation, target, direction)
    assert result(bp, recipe)["status"] == status
    assert analyze_readiness(bp)["status"] == (
        "blocked" if status == "unresolved" else "compatible"
    )


def test_line_along_plane_is_parallel_instead_of_normal_parallelism():
    bp, recipe = case("parallel", "plane")
    # Move all actual axial geometry and the actual joint axis together to X.
    for solid in recipe.solids[:2]:
        solid.rotation_rpy = (0, math.pi / 2, 0)
    bp.joints[0].axis_child = (1, 0, 0)
    assert verify_geometry(bp, recipe)["status"] == "passed"
    assert result(bp, recipe)["metrics"]["relation_angle_rad"] == pytest.approx(0)


@pytest.mark.parametrize("fault", ["offset", "tilt"])
def test_parallel_axes_alone_do_not_prove_coaxiality(fault):
    bp, recipe = case("coaxial", "axis")
    if fault == "offset":
        recipe.joint_placements[0].origin_parent_m = (0.001, 0, 0)
    else:
        bp.joints[0].axis_child = (math.sin(0.1), 0, math.cos(0.1))
    row = result(bp, recipe)
    assert row["status"] == "failed"
    bp.geometric_constraints[-1].interface_refs.reverse()
    assert result(bp, recipe)["status"] == "failed"


def test_box_edge_is_located_on_real_corner_and_rotates_with_solid():
    bp, recipe = case("coaxial", "edge")
    # datum XY half-width is .02: edge at (+.02,+.02), axis through (0,0) fails.
    assert result(bp, recipe)["metrics"]["axis_offset_over_feature_m"] == pytest.approx(
        math.sqrt(2) * 0.02
    )
    recipe.joint_placements[0].origin_parent_m = (0.02, 0.02, 0)
    assert result(bp, recipe)["status"] == "passed"
    recipe.solids[-1].rotation_rpy = (0, 0, math.pi / 2)
    assert result(bp, recipe)["status"] == "failed"


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("sides", [("min", "min"), ("min", "max"), ("max", "min"), ("max", "max")])
def test_all_twelve_box_edges_follow_rotation_and_corner_selection(axis, sides):
    bp, recipe = case("coaxial", "edge", axis)
    binding, solid = recipe.interface_bindings[-1], recipe.solids[-1]
    binding.edge_sides = sides
    solid.rotation_rpy = (0.31, -0.42, 0.17)
    matrix = rotation(solid.rotation_rpy)
    index = "xyz".index(axis)
    position = np.array(solid.center_m)
    for other, side in zip([i for i in range(3) if i != index], sides):
        position += matrix[:, other] * solid.size_m[other] / 2 * (-1 if side == "min" else 1)
    recipe.joint_placements[0].origin_parent_m = tuple(position)
    bp.joints[0].axis_child = tuple(matrix[:, index])
    assert result(bp, recipe)["status"] == "passed"


def test_native_geometry_audit_reports_feature_results_with_no_physics_claim(tmp_path):
    from roboenv_perception.components.operations import geometry_operation
    from roboenv_perception.io import read_json, write_json

    bp, recipe = case()
    paths = {key: tmp_path / f"{key}.json" for key in ["blueprint", "recipe"]}
    write_json(paths["blueprint"], bp.model_dump(mode="json"))
    write_json(paths["recipe"], recipe.model_dump(mode="json"))
    write_json(tmp_path / "evidence_refs.json", {"refs": [bp.evidence[0].id]})
    exports, status = geometry_operation(
        "geometry_audit", paths, tmp_path / "audit", None, "", None, None
    )
    report = read_json(
        tmp_path / "audit" / exports["geometric_verification"] / "geometric_verification.json"
    )
    assert status == "passed"
    assert report["physics_status"] == report["exported_mesh_status"] == "not_run"
    assert any(r["requirement_id"] == "axis_border" for r in report["checks"])


@pytest.mark.parametrize("fault", ["angle", "offset", "long_tilt"])
def test_valid_shaft_hole_fit_cannot_hide_misaligned_exported_joint(fault):
    bp, recipe = fixture()
    if fault == "angle":
        bp.joints[0].axis_child = (1, 0, 0)
    elif fault == "long_tilt":
        # Within angular tolerance, yet finite ends miss the joint axis.
        bp.joints[0].axis_child = (math.sin(0.0005), 0, math.cos(0.0005))
        recipe.solids[0].length_m = recipe.solids[1].length_m = 2
    else:
        # Both surfaces remain aligned, but their shared line misses joint origin.
        for s in recipe.solids[:2]:
            s.center_m = (0.002, 0, 0)
    report = verify_geometry(bp, recipe)
    assert report["status"] == "failed"
    row = next(r for r in report["checks"] if r["source"] == "mate")
    assert row["status"] == "failed"
    assert row["metrics"]["minimum_radial_clearance_m"] == pytest.approx(0.0002)


@pytest.mark.parametrize(
    "fault", ["surface_as_axis", "edge_as_face", "fake_edge", "wrong_owner", "unknown_joint"]
)
def test_bindings_cannot_certify_decorative_or_cross_part_features(fault):
    bp, recipe = case()
    if fault == "surface_as_axis":
        bp.interfaces[-2].kind = "shaft"
    elif fault == "edge_as_face":
        recipe.interface_bindings[-1] = InterfaceBinding(
            interface_id="border", solid="datum", feature="box_z_max"
        )
    elif fault == "fake_edge":
        recipe.interface_bindings[-1].solid = "bushing"
    elif fault == "wrong_owner":
        bp.interfaces[-2].part = "unrelated"
    else:
        recipe.interface_bindings[-2].joint = "not_a_joint"
    assert result(bp, recipe)["status"] == "unresolved"


@pytest.mark.parametrize(
    "data",
    [
        {"feature": "joint_axis", "solid": "datum"},
        {"feature": "joint_axis", "joint": "j", "solid": "datum"},
        {"feature": "box_edge", "solid": "datum"},
        {"feature": "box_z_max", "solid": "datum", "edge_axis": "x"},
        {"feature": "cylinder_axis", "joint": "j"},
    ],
)
def test_ambiguous_binding_sources_fail_schema(data):
    with pytest.raises(ValidationError):
        InterfaceBinding(interface_id="feature", **data)


def test_revision_must_keep_axis_edge_requirement_instead_of_helper_planes():
    before, _ = case()
    after = before.model_copy(deep=True)
    for i in after.interfaces[-2:]:
        i.kind = "plane"
    assert any(r["code"] == "axis_replaced_by_planes" for r in revision_findings(before, after))


def test_declared_line_normal_confusion_fails_description_before_any_model_or_geometry():
    from roboenv_perception.construction.assembly_design import check_assembly

    bp, recipe = case("orthogonal", "plane")
    bp.interfaces[-2].axis_local = (1, 0, 0)
    bp.interfaces[-1].axis_local = (0, 0, 1)
    report = check_assembly(bp)
    assert report["status"] == "needs_revision"
    assert any(
        r["code"] == "declared_direction_conflict"
        for r in report["construction_readiness"]["issues"]
    )
    bp.geometric_constraints[-1].type = "parallel"
    assert check_assembly(bp)["status"] != "needs_revision"
    # Neither unknown nor cross-part directions may be silently transformed.
    bp.geometric_constraints[-1].type = "orthogonal"
    bp.interfaces[-2].axis_local = None
    assert check_assembly(bp)["status"] != "needs_revision"
    bp.interfaces[-2].axis_local = (1, 0, 0)
    bp.interfaces[-2].part = "shaft"
    assert check_assembly(bp)["status"] != "needs_revision"


def test_native_description_failure_is_persisted_for_critic_instead_of_raising_early(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from roboenv_perception.artifacts.registry import tree_index
    from roboenv_perception.components.operations import run
    from roboenv_perception.construction.contracts import ConstructionConfig
    from roboenv_perception.io import read_json, write_json

    bp, _ = case("orthogonal", "plane")
    bp.interfaces[-2].axis_local = (1, 0, 0)
    bp.interfaces[-1].axis_local = (0, 0, 1)
    write_json(
        tmp_path / "scope/scope_plan.json",
        {"all_resolved": True, "objects": [{"asset_id": "a", "action": "build"}]},
    )
    write_json(tmp_path / "evidence/objects.json", {})
    calls = []

    class Model:
        def infer(self, stage, *args):
            calls.append(stage)
            if stage == "component-intake":
                return bp.asset_brief.model_dump(mode="json")
            assert stage == "component-blueprint"
            return bp.model_dump(mode="json")

    monkeypatch.setattr(
        "roboenv_perception.components.operations.make_vlm", lambda *a, **k: Model()
    )
    monkeypatch.setattr(
        "roboenv_perception.components.operations.load_construction_config",
        lambda p: ConstructionConfig(),
    )
    monkeypatch.setattr(
        "roboenv_perception.components.operations.observation",
        lambda *a: (
            {"asset_id": "a"},
            {"object_id": bp.object_id, "views": [{"ref_id": bp.evidence[0].id}]},
            [],
        ),
    )
    sources = {
        role: {"root": str(tmp_path), "ref": role, "files": tree_index(tmp_path / role)}
        for role in ["scope", "evidence"]
    }
    exports, status = run(
        "blueprint",
        sources,
        tmp_path / "result",
        SimpleNamespace(construction_config=None),
        "test description",
        workflow=True,
    )
    report = read_json(tmp_path / "result/blueprint/selfcheck.json")
    assert status == "needs_revision" and report["issues"] and not report["evidence_issues"]
    assert (tmp_path / "result" / exports["blueprint"]).is_file()
    assert calls == ["component-intake", "component-blueprint"]


def test_new_verifier_does_not_reuse_old_workflow_audit_receipt(tmp_path):
    from test_fixed_workflow import setup, target

    from roboenv_perception.harness.workflow import stable_digest

    workflow, component = setup(tmp_path)
    target(workflow, tmp_path)
    bp = workflow.state["blueprint"]
    recipe = workflow.stage("geometry", {"blueprint": (bp, "blueprint")})
    sources = {"blueprint": (bp, "blueprint"), "recipe": (recipe, "recipe")}
    receipt = workflow.stage("geometry_audit", sources)
    # Model an intact pre-upgrade audit indexed without the verifier version.
    current_key = next(k for k, v in workflow.state["stages"].items() if v is receipt)
    legacy_key = stable_digest(
        {
            "operation": "geometry_audit",
            "object_id": None,
            "params": {},
            "sources": {
                r: [row["ref"], row["digest"], export] for r, (row, export) in sources.items()
            },
        }
    )
    workflow.state["stages"][legacy_key] = workflow.state["stages"].pop(current_key)
    workflow.stage("geometry_audit", sources)
    workflow.stage("geometry_audit", sources)
    assert component.calls.count("geometry_audit") == 2
