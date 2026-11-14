"""Accept structure authored in native DSH; no model client or inference loop."""

from ..construction.assembly_blueprint import AssemblyBlueprint, parse_blueprint
from ..construction.assembly_design import check_assembly
from ..construction.design import validate_blueprint_evidence
from ..construction.structure_prior import PRIOR_INSTRUCTION
from ..io import read_json, sha256, write_json


def validate(evidence_root, object_id, raw, *, previous=None):
    from ..components.operations import observation

    _, data, _ = observation(evidence_root, object_id)
    blueprint = AssemblyBlueprint.model_validate(raw)
    if len({v["frame_ref"] for v in data["views"]}) < 2 and blueprint.asset_brief.observed_motion:
        raise ValueError("单张静态观察不能声明已观察到运动；请把机构标为推断或假设")
    validate_blueprint_evidence(blueprint, blueprint.asset_brief, data, {})
    report = check_assembly(
        blueprint, allowed_refs={v["ref_id"] for v in data["views"]},
        previous=parse_blueprint(read_json(previous)) if previous else None,
    )
    return blueprint, report


def materialize(evidence_root, object_id, raw, output, *, reason, actor, previous=None):
    blueprint, report = validate(evidence_root, object_id, raw, previous=previous)
    write_json(output / "blueprint.json", blueprint.model_dump(mode="json"))
    report.update(
        blueprint_sha256=sha256(output / "blueprint.json"),
        author="native_dsh", actor=actor, reason=reason,
        model_execution_this_stage=False,
        independent_model_review=False,
        prior_policy="plausible_hidden_structure_allowed",
        prior_instruction=PRIOR_INSTRUCTION,
    )
    write_json(output / "selfcheck.json", report)
    return report


def accept(blueprint_path, output, *, reason, actor):
    """Persist the native agent's acceptance without inventing independent QA."""
    blueprint = AssemblyBlueprint.model_validate(read_json(blueprint_path))
    report = check_assembly(blueprint)
    if report["status"] == "needs_revision":
        raise ValueError("结构一致性检查未通过，不能接受")
    review = {
        "blueprint_sha256": sha256(blueprint_path), "decision": "PASS", "findings": [],
        "summary": reason, "review_source": "native_dsh_acceptance",
        "actor": actor, "independent_model_review": False,
        "model_execution_this_stage": False,
        "scope": "native task-agent acceptance and deterministic consistency; no independent model QA or physical verification",
    }
    write_json(output / "review.json", review)
    return review
