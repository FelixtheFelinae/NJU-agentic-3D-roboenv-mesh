"""Synthetic contract/workflow regressions; no live model or physical validation."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from roboenv_perception.construction.assembly_blueprint import AssemblyBlueprint
from roboenv_perception.construction.common import new_run
from roboenv_perception.harness.geometry import GeometryRecipe
from roboenv_perception.harness.workflow import Workflow
from roboenv_perception.io import read_json, sha256, write_json
from roboenv_perception.workbench.config import Settings


def drawer_blueprint():
    prior = "prior:side-mounted-drawer-guides"
    return {
        "object_id": "drawer-1", "kind": "articulated",
        "asset_brief": {
            "object_id": "drawer-1", "category": "drawer", "summary": "Closed drawer exterior",
            "build_route": "articulated", "support_mode": "grounded",
            "key_interactions": ["pull drawer"], "observed_motion": [],
            "reference_interpretation": "single", "evidence_refs": ["view-1"],
        },
        "part_definitions": [
            {"id": "cabinet-def", "category": "cabinet", "geometry": "cabinet with assumed fixed side rails",
             "material_appearance": "wood", "collision_intent": "rigid proxy", "evidence_refs": [prior]},
            {"id": "drawer-def", "category": "drawer", "geometry": "drawer with assumed mating side guides",
             "hollow": True, "material_appearance": "wood", "collision_intent": "rigid proxy", "evidence_refs": [prior]},
        ],
        "part_instances": [
            {"name": "cabinet", "definition_id": "cabinet-def"},
            {"name": "drawer", "definition_id": "drawer-def", "parent": "cabinet"},
        ],
        "interfaces": [
            {"id": name, "part": part, "kind": "axis", "axis_local": [0, 1, 0],
             "basis": "assumed", "evidence_refs": [prior], "description": "Assumed sliding guide axis",
             "local_frame": {"origin": [0, 0, 0], "rotation_rpy": [0, 0, 0],
                             "basis": "assumed", "evidence_refs": [prior]}}
            for name, part in (("fixed-guide", "cabinet"), ("moving-guide", "drawer"))
        ],
        "mates": [{"id": "slide", "type": "prismatic", "interfaces": ["fixed-guide", "moving-guide"],
                   "basis": "inferred", "evidence_refs": [prior], "contact_status": "not_modelled",
                   "alternatives": ["wooden runners", "bottom-mounted guide"]}],
        "joints": [{"name": "slide", "parent": "cabinet", "child": "drawer", "type": "prismatic",
                    "axis_child": [0, 1, 0], "origin_hint": "assumed side guide placement",
                    "rest": "at_min", "travel": {"value": .25, "source": "assumed", "evidence_refs": [prior]},
                    "evidence_kind": "inferred", "evidence_refs": [prior], "mate_id": "slide"}],
        "evidence": [
            {"id": "view-1", "origin": "observation", "description": "Synthetic static exterior fixture only"},
            {"id": prior, "origin": "prior", "description": "A possible ordinary drawer uses side-mounted guides; no rail image or motion"},
        ],
        "hypotheses": [{"id": "guides", "relation_refs": ["slide"], "explanation": "Choose side guides as a plausible mechanism",
                        "alternatives": ["wooden runners", "bottom-mounted guide"], "evidence_refs": [prior]}],
        "assumptions": ["Guide type, placement and 0.25 m travel are assumptions; no drawer motion observed"],
        "acceptance": ["A plausible structure candidate, not identification of actual hidden hardware"],
    }


class NativeStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "fixture.png"
        Image.new("RGB", (32, 32), "gray").save(self.image)
        self.config = self.root / "config.toml"
        self.config.write_text("")
        self.settings = Settings(dsh_root=self.root, perception_config=self.config,
                                 construction_config=self.config, physics=False)
        self.operations = []
        self.call_count = 0
        self.actor = {"origin": "agent", "session_id": "native-session", "call_id": "native-call"}
        self.controller = Workflow(
            self.root / "workflow", self.settings, "Generate a plausible closed drawer structure",
            [{"name": "fixture.png", "path": str(self.image), "sha256": sha256(self.image)}],
            {"structure_author": "native_dsh"}, execute=self.execute,
        )
        # Any accidental internal model request must fail the test immediately.
        self.model_patch = patch("roboenv_perception.vlm.HTTPVLM.infer", side_effect=AssertionError("unexpected model call"))
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)

    def execute(self, operation, bindings, output, settings, task, **kwargs):
        self.operations.append(operation)
        if operation == "perceive":
            write_json(output / "perception/scene.json", {"synthetic_fixture": True})
            return {"perception": "perception"}, "review"
        if operation == "evidence":
            base = output / "evidence"
            with new_run(base, "synthetic_evidence", {}):
                with Image.open(self.image) as image:
                    image.save(base / "frame.png")
                write_json(base / "object.json", {
                    "object_id": "drawer-1", "category": "drawer", "status": "ready_for_design",
                    "views": [{"ref_id": "view-1", "frame_id": "f0", "frame_ref": "frame.png",
                               "context_ref": "frame.png", "mask_ref": "frame.png"}],
                })
                write_json(base / "objects.json", {"objects": [
                    {"asset_id": "drawer", "object_id": "drawer-1", "status": "ready_for_design",
                     "evidence_ref": "object.json"},
                ]})
            return {"evidence": "evidence"}, "ready"
        raise AssertionError("Unexpected downstream component: " + operation)

    def run_step(self, decision=None):
        output = self.root / "calls" / str(self.call_count)
        self.call_count += 1
        return self.controller.run(output, decision, actor=self.actor)

    def select(self):
        result = self.run_step()
        return self.run_step({"action": "select_target", "revision": result["revision"],
                              "object_id": "drawer-1", "reason": "same synthetic exterior"})

    def submit(self, blueprint=None):
        result = self.select()
        self.assertEqual(result["phase"], "structure_proposal")
        self.assertIn("schema", result["structure_request"])
        return self.run_step({"action": "submit_structure", "revision": result["revision"],
                              "blueprint": blueprint or drawer_blueprint(), "reason": "Choose plausible side guides"})

    def test_static_drawer_accepts_prior_only_hidden_guides_without_model_calls(self):
        result = self.submit()
        self.assertEqual(result["phase"], "structure")
        self.assertEqual(self.operations, ["perceive", "evidence"])
        self.assertEqual(result["blueprint"]["joints"][0]["evidence_kind"], "inferred")
        self.assertIn("prior:", result["blueprint"]["evidence"][1]["id"])
        self.assertEqual(self.controller.state["history"][-1]["actor"], self.actor)
        bp = self.controller.state["blueprint"]
        report = read_json(self.controller.ref(bp, "selfcheck"))
        self.assertFalse(report["model_execution_this_stage"])
        self.assertFalse(report["independent_model_review"])

    def test_acceptance_is_bound_and_does_not_bypass_unsupported_prismatic_geometry(self):
        result = self.submit()
        accepted = self.run_step({"action": "accept_structure", "revision": result["revision"],
                                  "reason": "Accept as a hypothetical structure, not verified rails"})
        review = read_json(self.controller.ref(self.controller.state["blueprint_review"], "blueprint_review"))
        self.assertEqual(review["blueprint_sha256"], result["blueprint_identity"]["sha256"])
        self.assertEqual(review["review_source"], "native_dsh_acceptance")
        self.assertFalse(review["independent_model_review"])
        self.assertEqual(accepted["phase"], "recovery")
        self.assertEqual(self.operations, ["perceive", "evidence"])
        self.assertEqual(accepted["physics_status"], "not_run")
        self.assertTrue(any(i["code"] == "unsupported_relation" for i in accepted["construction_readiness"]["issues"]))

    def test_fabricated_observation_rejected_without_advancing_revision(self):
        result = self.select()
        candidate = drawer_blueprint()
        candidate["evidence"][0]["id"] = "fabricated-view"
        candidate["asset_brief"]["evidence_refs"] = ["fabricated-view"]
        with self.assertRaisesRegex(ValueError, "unprovided evidence"):
            self.run_step({"action": "submit_structure", "revision": result["revision"],
                           "blueprint": candidate, "reason": "invalid source"})
        saved = read_json(self.controller.state_path)
        self.assertEqual(saved["revision"], result["revision"])
        self.assertEqual(saved["phase"], "structure_proposal")

    def test_static_input_cannot_be_labelled_observed_motion(self):
        result = self.select()
        candidate = drawer_blueprint()
        candidate["asset_brief"]["observed_motion"] = ["drawer opens"]
        with self.assertRaisesRegex(ValueError, "单张静态观察"):
            self.run_step({"action": "submit_structure", "revision": result["revision"],
                           "blueprint": candidate, "reason": "invalid motion claim"})

    def test_prior_only_relation_cannot_be_labelled_observed(self):
        candidate = drawer_blueprint()
        candidate["mates"][0]["basis"] = "observed"
        with self.assertRaisesRegex(ValueError, "observed relation/frame"):
            AssemblyBlueprint.model_validate(candidate)

    def test_prior_cannot_be_labelled_measurement(self):
        candidate = drawer_blueprint()
        candidate["joints"][0]["travel"]["source"] = "measured"
        with self.assertRaisesRegex(ValueError, "measurement evidence"):
            AssemblyBlueprint.model_validate(candidate)

    def test_revision_returns_to_native_author_with_original_blueprint(self):
        result = self.submit()
        revised = self.run_step({"action": "revise_structure", "revision": result["revision"],
                                 "reason": "Consider wooden runners instead"})
        self.assertEqual(revised["phase"], "structure_proposal")
        self.assertEqual(revised["structure_request"]["previous_blueprint"]["object_id"], "drawer-1")
        self.assertNotIn("repair_constraints", revised["allowed_actions"])
        self.assertEqual(self.operations, ["perceive", "evidence"])

    def test_geometry_accepts_declared_prior_but_not_fake_reference(self):
        blueprint = AssemblyBlueprint.model_validate(drawer_blueprint())
        prior = blueprint.evidence[1].id
        raw = {
            "object_id": "drawer-1", "part_masses_kg": {"cabinet": 5, "drawer": 1},
            "solids": [{"name": p, "part": p, "shape": "box", "size_m": [.3, .3, .1],
                        "source": "assumed", "evidence_refs": [prior]} for p in ("cabinet", "drawer")],
            "joint_placements": [{"joint": "slide", "origin_parent_m": [0, 0, 0],
                                  "source": "assumed", "evidence_refs": [prior]}],
        }
        GeometryRecipe.model_validate(raw).check_against(blueprint, {"view-1"})
        for source in ("observed", "reference"):
            bad = copy.deepcopy(raw)
            bad["solids"][0]["source"] = source
            with self.assertRaisesRegex(ValueError, "prior-only geometry"):
                GeometryRecipe.model_validate(bad).check_against(blueprint, {"view-1"})

    def test_legacy_options_do_not_change_existing_workflow_identity(self):
        old = Workflow(self.root / "old", self.settings, "old", [], {}, execute=self.execute)
        explicit = Workflow(self.root / "old", self.settings, "old", [],
                            {"structure_author": "internal_vlm"}, execute=self.execute)
        self.assertEqual(old.options, {"background": "off", "physics": False})
        self.assertEqual(old.options, explicit.options)


if __name__ == "__main__":
    unittest.main()
