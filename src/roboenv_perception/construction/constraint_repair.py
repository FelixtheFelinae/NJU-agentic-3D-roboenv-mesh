"""Transactional, model-proposed rebindings of selected geometric requirements."""

import json

from pydantic import Field

from ..contracts import Contract
from ..io import sha256, write_json
from .assembly_blueprint import AssemblyBlueprint, Interface, digest
from .assembly_design import check_assembly, selfcheck_assembly
from .assembly_readiness import analyze_readiness, revision_findings

VERSION = "envcreator.constraint-repair/2"


class ConstraintRebinding(Contract):
    id: str
    interface_refs: list[str] = Field(min_length=1, max_length=2)
    description: str = Field(min_length=1, max_length=2000)


class ConstraintRepair(Contract):
    source_blueprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    replacements: list[ConstraintRebinding] = Field(min_length=1, max_length=4)
    add_interfaces: list[Interface] = Field(default_factory=list, max_length=8)
    rationale: str = Field(min_length=3, max_length=1500)


def select_constraints(blueprint, identities):
    if not isinstance(blueprint, AssemblyBlueprint):
        raise ValueError("局部关系修订只支持 AssemblyBlueprint；旧格式需先显式迁移")
    if (
        not isinstance(identities, list)
        or not 1 <= len(identities) <= 4
        or any(not isinstance(i, str) for i in identities)
        or len(set(identities)) != len(identities)
    ):
        raise ValueError("请指定 1–4 个不同的几何约束 ID")
    by_id = {c.id: c for c in blueprint.geometric_constraints}
    if not set(identities) <= by_id.keys():
        raise ValueError("局部修订引用了不存在的几何约束")
    return [by_id[i] for i in identities]


def apply_repair(blueprint, proposal, identities, source_sha256):
    """Build a new value; never mutate the source or execute model-generated code."""
    selected = select_constraints(blueprint, identities)
    proposal = ConstraintRepair.model_validate(proposal.model_dump(mode="json"))
    if proposal.source_blueprint_sha256 != source_sha256:
        raise ValueError("修订未绑定当前描述文件")
    replacements = {c.id: c for c in proposal.replacements}
    if len(replacements) != len(proposal.replacements) or set(replacements) != set(identities):
        raise ValueError("修订必须恰好覆盖指定约束，不能新增、遗漏或重复要求")
    old_interfaces = {i.id: i for i in blueprint.interfaces}
    scope = {p for c in selected for p in c.part_refs} | {
        old_interfaces[i].part for c in selected for i in c.interface_refs
    }
    added = {i.id: i for i in proposal.add_interfaces}
    if len(added) != len(proposal.add_interfaces) or added.keys() & old_interfaces.keys():
        raise ValueError("只能新增唯一接口；不能覆盖已有接口或配合面")
    referenced = {i for c in proposal.replacements for i in c.interface_refs}
    if not added.keys() <= referenced:
        raise ValueError("不能加入修订关系未使用的接口")
    for interface in proposal.add_interfaces:
        if (
            interface.part not in scope
            or interface.kind not in {"plane", "axis", "edge"}
            or interface.basis not in {"assumed", "inferred"}
            or interface.local_frame.basis not in {"assumed", "inferred"}
            or interface.parameters
            or interface.local_frame.origin is not None
            or interface.local_frame.rotation_rpy is not None
            or not interface.evidence_refs
        ):
            raise ValueError(
                "新增接口仅允许相关零件的显式假设/推断平面、轴线、边缘；不能补尺寸、接触硬件或确定位置"
            )
    interfaces = {**old_interfaces, **added}
    for original in selected:
        replacement = replacements[original.id]
        original_scope = set(original.part_refs) | {
            old_interfaces[i].part for i in original.interface_refs
        }
        if any(
            i not in interfaces or interfaces[i].part not in original_scope
            for i in replacement.interface_refs
        ):
            raise ValueError("修订接口必须属于所选关系涉及的零件")
        old_kinds = sorted(old_interfaces[i].kind for i in original.interface_refs)
        if (
            old_kinds == ["axis", "edge"]
            and sorted(interfaces[i].kind for i in replacement.interface_refs) != old_kinds
        ):
            raise ValueError("已有轴线—边缘关系不能改为其他特征组合")
    data = blueprint.model_dump(mode="json")
    data["interfaces"] += [i.model_dump(mode="json") for i in proposal.add_interfaces]
    data["geometric_constraints"] = [
        {**c.model_dump(mode="json"), **replacements[c.id].model_dump(mode="json")}
        if c.id in replacements
        else c.model_dump(mode="json")
        for c in blueprint.geometric_constraints
    ]
    candidate = AssemblyBlueprint.model_validate(data)
    if digest(data) == digest(blueprint.model_dump(mode="json")):
        raise ValueError("修订没有改变任何关系，不能作为修复成功")
    findings = revision_findings(blueprint, candidate)
    if findings:
        raise ValueError("; ".join(r["message"] for r in findings))
    report = analyze_readiness(candidate)
    issues = [
        r["message"]
        for r in report["issues"]
        if r["path"] in {f"/geometric_constraints/{i}" for i in identities}
    ]
    for c in candidate.geometric_constraints:
        if c.id not in identities:
            continue
        if c.type in {"parallel", "orthogonal", "coaxial", "coplanar"} and any(
            interfaces[i].axis_local is None for i in c.interface_refs
        ):
            issues.append(f"{c.id} 仍缺少明确方向；不能通过删除方向绕过矛盾")
    if issues:
        raise ValueError("; ".join(issues))
    return candidate


def author_repair(
    vlm, blueprint, source_path, identities, evidence, images, output, *, task, feedback
):
    """One proposal followed by existing selfcheck. Failed transactions retain the source."""
    from .design import infer_model

    selected = select_constraints(blueprint, identities)
    source_hash = sha256(source_path)
    # Existing measurements/retrieval records are immutable inherited claims,
    # not newly measured facts. New interfaces cannot introduce such records.
    allowed_refs = {v["ref_id"] for v in evidence["views"]} | {
        e.id for e in blueprint.evidence if e.origin != "observation"
    }
    readiness = analyze_readiness(blueprint)
    scope = {p for c in selected for p in c.part_refs} | {
        i.part for i in blueprint.interfaces if any(i.id in c.interface_refs for c in selected)
    }
    context = {
        "task": task,
        "source_blueprint_sha256": source_hash,
        "constraint_ids": identities,
        "selected_constraints": [c.model_dump(mode="json") for c in selected],
        "available_interfaces": [
            i.model_dump(mode="json") for i in blueprint.interfaces if i.part in scope
        ],
        "parts": [p.model_dump(mode="json") for p in blueprint.part_instances if p.name in scope],
        "evidence": evidence,
        "coordinate_units": blueprint.coordinate_units,
        "target_findings": [
            r
            for r in readiness["issues"]
            if r["path"] in {f"/geometric_constraints/{i}" for i in identities}
        ],
        "feedback": feedback,
    }
    report = {
        "schema": VERSION,
        "source_blueprint_sha256": source_hash,
        "constraint_ids": identities,
        "geometry_status": "not_run",
        "physics_status": "not_run",
    }
    try:
        proposal = infer_model(
            vlm,
            "component-constraint-repair",
            "Repair selected geometric requirements from original image evidence. "
            "Return ONLY a ConstraintRepair proposal, NOT a full blueprint. "
            "Replace exactly selected_constraints. Each replacement has ONLY id, interface_refs, description. "
            "Do NOT return type, part_refs, parameters, basis, evidence_refs or any other frozen field: "
            "the program preserves them from the source. Existing interfaces are immutable. "
            "Add at most eight new plane/axis/edge interfaces on the selected parts if needed, with "
            "explicit assumed/inferred basis and supplied evidence refs. Do not invent sizes, positions "
            "or contact hardware. New local_frame origin/rotation must stay null, parameters empty. "
            "For a rectangular base's orthogonal requirement prefer TWO ADJACENT EDGES, "
            "retaining the edge role if the old binding contains an edge. Existing axis/edge "
            "requirements cannot be replaced by all-plane bindings. "
            "NOT the top plane and an edge lying along it. Known directional relations need explicit "
            "unit directions/normals in the owning part coordinates. plane means a planar feature, "
            "axis an ideal rotation line, edge an actual straight border. planar references ONE plane; "
            "parallel/orthogonal TWO features. Line-plane parallel means the line is perpendicular "
            "to the plane NORMAL, line-plane orthogonal means along the normal. "
            "A plane's axis_local is its normal; an edge/axis's axis_local is its direction. "
            "Use Z up and front +Y; name features consistently with their direction. "
            "Do not erase directions to hide contradictions. "
            "The source, all other requirements, mates, unknowns and intake remain frozen. "
            "If evidence is insufficient say so in rationale; do not force a PASS.\n"
            + json.dumps(context, ensure_ascii=False),
            images,
            ConstraintRepair,
            output / "repair-author",
        )
        write_json(output / "proposal.json", proposal.model_dump(mode="json"))
        candidate = apply_repair(blueprint, proposal, identities, source_hash)
        provenance = check_assembly(
            candidate, brief=blueprint.asset_brief, allowed_refs=allowed_refs
        )
        if provenance["evidence_issues"]:
            raise ValueError("; ".join(provenance["evidence_issues"]))
    except ValueError as error:
        # Transport failures are not ValueError and deliberately propagate.
        report.update(status="rejected", issues=[str(error)[:8000]], candidate_published=False)
        write_json(output / "blueprint.json", blueprint.model_dump(mode="json"))
        checked = check_assembly(blueprint, previous=blueprint)
        checked.update(
            status="needs_revision",
            issues=report["issues"],
            blueprint_sha256=sha256(output / "blueprint.json"),
        )
        write_json(output / "selfcheck.json", checked)
    else:
        try:
            checked = selfcheck_assembly(
                vlm,
                candidate,
                images,
                output,
                stage="component-constraint-selfcheck",
                brief=blueprint.asset_brief,
                allowed_refs=allowed_refs,
                previous=blueprint,
                context=json.dumps(context, ensure_ascii=False),
            )
        except ValueError as error:
            # A malformed/hash-mismatched review is not a PASS. Preserve the
            # already materialized candidate for Critic inspection, no retry.
            checked = check_assembly(candidate, previous=blueprint)
            checked.update(
                status="needs_revision",
                issues=["局部修订自检查响应无效：" + str(error)[:8000]],
                blueprint_sha256=sha256(output / "blueprint.json"),
            )
            write_json(output / "selfcheck.json", checked)
        report.update(
            status="applied" if checked["status"] != "needs_revision" else "needs_revision",
            issues=checked["issues"],
            candidate_published=True,
        )
    report["result_blueprint_sha256"] = sha256(output / "blueprint.json")
    report["report_sha256"] = digest(report)
    write_json(output / "constraint_repair.json", report)
    return checked
