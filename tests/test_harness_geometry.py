import pytest

from roboenv_perception.construction.contracts import Blueprint
from roboenv_perception.harness.geometry import (
    GeometryRecipe,
    Solid,
    _model_source,
    _preview_offsets,
)


def _fixture_blueprint():
    return Blueprint.model_validate({
        "object_id": "test-cup", "kind": "rigid",
        "frame_convention": "metres; Z-up; front=+Y; local object frame",
        "parts": [{"name": "body", "parent": None, "role": "container",
                   "geometry": "hollow shell", "hollow": True,
                   "material_appearance": "gray", "collision_intent": "preserve cavity",
                   "evidence_refs": ["view-001"]}],
        "joints": [], "acceptance": ["preserve cavity"],
    })


def test_recipe_rejects_missing_parts_and_unbound_evidence():
    blueprint = _fixture_blueprint()
    recipe = GeometryRecipe(object_id=blueprint.object_id, solids=[
        Solid(name="shell", part="body", shape="hollow_cylinder", size_m=(0.1, 0.1, 0.12),
              radius_m=0.05, length_m=0.004, source="estimated", evidence_refs=["view-001"]),
    ], part_masses_kg={"body": 0.1})
    recipe.check_against(blueprint, {"view-001"})
    with pytest.raises(ValueError, match="unknown recipe evidence"):
        recipe.check_against(blueprint, {"view-002"})


def test_hollow_shell_wall_must_leave_interior():
    with pytest.raises(ValueError, match="wall thinner"):
        Solid(name="shell", part="body", shape="hollow_box", size_m=(0.1, 0.1, 0.1),
              length_m=0.06, source="assumed", evidence_refs=["view-001"])


def test_closed_profile_requires_monotone_finite_rings():
    fields = {"name": "sealed_body", "part": "body", "shape": "lathe",
              "source": "assumed", "evidence_refs": ["view-001"]}
    solid = Solid(**fields, profile_m=[(0, 0.03), (0.12, 0.03), (0.15, 0.015)])
    assert len(solid.profile_m) == 3
    for profile in ([(0, 0.03)], [(0.1, 0.03), (0.05, 0.02)],
                    [(0, 0.03), (0.1, 0)], [(0, 0.03), (float("inf"), 0.02)]):
        with pytest.raises(ValueError):
            Solid(**fields, profile_m=profile)


def test_compiled_model_uses_declared_parts_and_meshes():
    blueprint = _fixture_blueprint()
    recipe = GeometryRecipe(object_id=blueprint.object_id, solids=[
        Solid(name="body_surface", part="body", shape="box", size_m=(0.1, 0.1, 0.1),
              source="estimated", evidence_refs=["view-001"]),
    ], part_masses_kg={"body": 0.1})
    source = _model_source(blueprint, recipe)
    compile(source, "generated-model.py", "exec")
    assert 'mesh_from_input(\'body_surface\')' in source
    assert "est_mass_kg=0.1" in source


def test_ideal_coupling_is_exported_by_artisan_and_needs_declared_rotary_joints():
    data = _fixture_blueprint().model_dump(mode="json")
    data["kind"] = "articulated"
    for name in ("crank", "wheel"):
        part = dict(data["parts"][0], name=name, parent="body", role=name)
        data["parts"].append(part)
        data["joints"].append({"name": f"spin_{name}", "parent": "body", "child": name,
                               "type": "continuous", "axis_child": [0, 1, 0],
                               "origin_hint": "axle", "evidence_kind": "inferred",
                               "evidence_refs": ["view-001"]})
    data["couplings"] = [{"driver_joint": "spin_crank", "driven_joint": "spin_wheel",
                          "ratio": {"value": -2.5, "source": "assumed",
                                    "evidence_refs": ["view-001"], "rationale": "ideal drive"},
                          "evidence_kind": "assumed", "evidence_refs": ["view-001"]}]
    blueprint = Blueprint.model_validate(data)
    recipe = GeometryRecipe(object_id=blueprint.object_id, solids=[
        Solid(name=f"solid_{part.name}", part=part.name, shape="box", size_m=(0.1, 0.1, 0.1),
              source="assumed", evidence_refs=["view-001"]) for part in blueprint.parts
    ], part_masses_kg={part.name: 0.1 for part in blueprint.parts}, joint_placements=[
        {"joint": joint.name, "origin_parent_m": (0, 0, 0), "source": "assumed",
         "evidence_refs": ["view-001"]} for joint in blueprint.joints
    ])
    source = _model_source(blueprint, recipe)
    assert "m.couple('spin_crank', 'spin_wheel', mapping=2.5, sense='opposite')" in source
    assert "MotionLimits(lower=-400.0, upper=400.0)" in source
    assert "MotionLimits(lower=-1000.0, upper=1000.0)" in source
    data["couplings"][0]["driven_joint"] = "missing"
    with pytest.raises(ValueError, match="continuous rotary joints"):
        Blueprint.model_validate(data)


def test_preview_uses_parent_chain_and_joint_limits_preserve_rest_convention():
    data = _fixture_blueprint().model_dump(mode="json")
    data["kind"] = "articulated"
    data["parts"] += [dict(data["parts"][0], name="lid", parent="body"),
                      dict(data["parts"][0], name="handle", parent="lid")]
    data["joints"] = [
        {"name": "hinge", "parent": "body", "child": "lid", "type": "revolute",
         "axis_child": [1, 0, 0], "origin_hint": "edge", "rest": "at_max",
         "travel": {"value": 1.5, "source": "assumed", "evidence_refs": ["view-001"]},
         "evidence_kind": "assumed", "evidence_refs": ["view-001"]},
        {"name": "handle_fix", "parent": "lid", "child": "handle", "type": "fixed",
         "origin_hint": "lid", "evidence_kind": "assumed", "evidence_refs": ["view-001"]},
    ]
    blueprint = Blueprint.model_validate(data)
    recipe = GeometryRecipe(object_id=blueprint.object_id,
        solids=[Solid(name=part.name, part=part.name, shape="box", size_m=(0.1, 0.1, 0.1),
                      source="assumed", evidence_refs=["view-001"]) for part in blueprint.parts],
        part_masses_kg={part.name: 0.1 for part in blueprint.parts},
        joint_placements=[{"joint": name, "origin_parent_m": xyz, "source": "assumed",
                           "evidence_refs": ["view-001"]}
                          for name, xyz in (("hinge", (0, 0, 0.2)), ("handle_fix", (0.1, 0, 0.03)))])
    assert _preview_offsets(blueprint, recipe)["handle"] == pytest.approx((0.1, 0, 0.23))
    assert "MotionLimits(lower=-1.5, upper=0.0)" in _model_source(blueprint, recipe)


def test_author_and_review_payload_bind_multiple_images_to_roles_and_views(tmp_path, monkeypatch):
    import json

    from PIL import Image

    from roboenv_perception.contracts import VLMConfig
    from roboenv_perception.harness import geometry
    from roboenv_perception.io import sha256

    blueprint = _fixture_blueprint()
    recipe = GeometryRecipe(object_id=blueprint.object_id, solids=[
        Solid(name="body", part="body", shape="box", size_m=(0.1, 0.1, 0.1),
              source="assumed", evidence_refs=["view-001"])], part_masses_kg={"body": 0.1})
    sources = []
    views = []
    for index in (1, 2):
        path = tmp_path / f"view-{index:03d}" / "context.png"
        path.parent.mkdir()
        Image.new("RGB", (4, 4), (index, 0, 0)).save(path)
        sources.append(path)
        views.append({"ref_id": f"view-{index:03d}",
                      "context_ref": f"view-{index:03d}/context.png"})
    renders = []
    for name in ("front", "top", "underside"):
        path = tmp_path / f"preview_{name}.png"
        Image.new("RGB", (4, 4), "blue").save(path)
        renders.append(path)
    evidence = {"views": views}
    calls = []

    class FakeVLM:
        def __init__(self, system):
            self.system = system

        def infer(self, stage, prompt, images, schema, trace):
            payload = json.loads(prompt.split("\nJSON schema:\n")[0])
            calls.append((self.system, payload, images))
            if stage.startswith("geometry-recipe"):
                return recipe.model_dump(mode="json")
            return {"candidate_sha256": "a" * 64, "decision": "REDO",
                    "issues": ["Front render 3 shows an actual gap"]}

    monkeypatch.setattr(geometry, "make_vlm", lambda config, system: FakeVLM(system))
    geometry.infer_recipe(VLMConfig(), blueprint, evidence, sources, tmp_path / "author",
                          feedback=["shoulder gap"], previous_recipe=recipe.model_dump(mode="json"))
    review = geometry.review_geometry(VLMConfig(), blueprint, evidence, recipe, sources,
                                       {"candidate_sha256": "a" * 64,
                                        "visual_previews": list(map(str, renders))}, tmp_path / "review")
    assert review.decision == "REDO"  # Real issues are still blocking.
    author_system, author_payload, author_images = calls[0]
    assert "image_manifest" in author_system
    assert author_images == sources
    assert "modified solid names" in author_payload["repair_instruction"]
    review_system, payload, images = calls[1]
    assert images == sources + renders
    manifest = payload["image_manifest"]
    assert [row["image_index"] for row in manifest] == [1, 2, 3, 4, 5]
    assert [row["role"] for row in manifest] == ["source_evidence"] * 2 + ["candidate_render"] * 3
    for row, path in zip(manifest, images, strict=True):
        assert row["name"] == path.name and row["sha256"] == sha256(path)
    assert manifest[:2] == author_payload["image_manifest"]
    assert manifest[1]["evidence_refs"] == [{"ref_id": "view-002",
                                             "image_ref": "view-002/context.png", "kind": "context_ref"}]
    assert [row["view_name"] for row in manifest[2:]] == ["preview_front", "preview_top", "preview_underside"]
    assert "top/underside" in review_system and "remain blocking" in review_system
