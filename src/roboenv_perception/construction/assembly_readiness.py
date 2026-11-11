"""Explain description/backend conflicts before asking a model for a mesh recipe.

This is a read-only capability check, not geometry or physics verification. In
particular it never converts organizational parents into mechanical joints.
"""

from .assembly_blueprint import AssemblyBlueprint, digest

VERSION = "envcreator.assembly-readiness/1"


def revision_findings(previous, current):
    """Keep declared requirements from disappearing during a backend-driven revision.

    This only checks IDs, relation types and explicit feature families. It cannot
    prove that arbitrary natural-language descriptions have equivalent meaning.
    """
    if not isinstance(previous, AssemblyBlueprint) or not isinstance(current, AssemblyBlueprint):
        return []
    findings = []
    old_interfaces = {i.id: i for i in previous.interfaces}
    new_interfaces = {i.id: i for i in current.interfaces}
    new_constraints = {c.id: c for c in current.geometric_constraints}
    axial = {"hinge_region", "shaft", "hole", "cylinder", "thread_internal", "thread_external"}
    for requirement in previous.geometric_constraints:
        candidate = new_constraints.get(requirement.id)
        code, message = None, None
        if candidate is None:
            code, message = "dropped_requirement", "修订删除了已有几何要求，不能以省略要求绕过检查。"
        elif candidate.type != requirement.type:
            code, message = "changed_relation_type", "修订改变了已有几何关系类型，请保留原要求及未决项。"
        elif (any(old_interfaces[r].kind in axial for r in requirement.interface_refs)
              and candidate.interface_refs
              and all(new_interfaces[r].kind == "plane" for r in candidate.interface_refs)):
            code, message = "axis_replaced_by_planes", (
                "修订将轴线或铰链区域要求换成了平面要求；平面法线平行不能证明原轴线/边线关系，"
                "更不能证明同轴。请保留原要求为未决，不能用辅助平面绕过审核。")
        if code:
            findings.append({"code": code, "path": f"/geometric_constraints/{requirement.id}",
                             "message": f"{requirement.id}：{message}", "repair_target": "blueprint"})
    return findings


def analyze_readiness(blueprint):
    issues = []

    def issue(code, path, message, *, refs=(), target="blueprint"):
        issues.append({
            "code": code, "path": path, "message": message,
            "interface_refs": list(refs), "repair_target": target,
            "failure_domain": "assembly_description" if target == "blueprint" else "unsupported_geometry",
        })

    assembly = isinstance(blueprint, AssemblyBlueprint)
    parents = {p.name: p.parent for p in blueprint.parts}
    incoming = {}
    if assembly:
        if blueprint.coordinate_units != "m":
            issue("relative_coordinates", "/coordinate_units",
                  "相对坐标尚不能进入当前构建器；需要显式尺度适配，不能直接把单位改成米。", target="backend")
        for joint in blueprint.joints:
            path = f"/joints/{joint.name}"
            if joint.child in incoming:
                issue("multiple_incoming_joints", path,
                      f"零件 {joint.child} 有多个入关节；当前后端只支持树，不能静默丢弃连接。", target="backend")
            incoming[joint.child] = joint.parent
            if parents[joint.child] != joint.parent:
                issue("parent_tree_conflict", f"/part_instances/{joint.child}/parent",
                      f"{joint.child} 的分组父节点为 {parents[joint.child]!r}，但关节 {joint.name} "
                      f"连接 {joint.parent} → {joint.child}。当前后端要求两者一致；请依据原图明确修订分组，"
                      "或保留未决，不能补造固定关节。")
        for name, parent in parents.items():
            if parent is not None and name not in incoming:
                issue("missing_mechanical_placement", f"/part_instances/{name}/parent",
                      f"{name} 仅有分组父节点 {parent}，没有机械连接和参考位姿；不能据此自动生成固定关节。")
        roots = sorted(set(parents) - set(incoming))
        if len(roots) != 1:
            issue("disconnected_joint_graph", "/joints",
                  f"当前机械图有 {len(roots)} 个根节点 {roots}；后端只支持单根关节树。", target="backend")
        for name in parents:
            seen, current = set(), name
            while current in incoming:
                if current in seen:
                    issue("joint_cycle", "/joints", f"{name} 所在机械图有闭环；当前后端不支持闭环。", target="backend")
                    break
                seen.add(current)
                current = incoming[current]
        represented = {j.mate_id for j in blueprint.joints}
        for mate in blueprint.mates:
            if mate.id not in represented:
                issue("unrepresented_mate", f"/mates/{mate.id}",
                      f"配合 {mate.id} 没有当前后端可用的关节表示；不能忽略配合或擅自降为固定连接。", target="backend")

        interfaces = {i.id: i for i in blueprint.interfaces}
        for source, requirement in [*(('mates', m) for m in blueprint.mates),
                                    *(('geometric_constraints', c) for c in blueprint.geometric_constraints)]:
            path = f"/{source}/{requirement.id}"
            refs = list(requirement.interfaces if source == 'mates' else requirement.interface_refs)
            expected = 1 if requirement.type == "planar" else 2
            if len(refs) != expected or len(set(refs)) != len(refs):
                issue("ambiguous_constraint_surfaces", path,
                      f"{requirement.id}（{requirement.type}）需要 {expected} 个不同的具体接口，当前为 {refs}；"
                      "零件名称或文字描述不能替代功能面绑定。", refs=refs)
                continue
            if requirement.type in {"planar", "orthogonal", "parallel", "coplanar"}:
                kinds = {"plane"}
            elif requirement.type in {"coaxial", "clearance", "revolute", "cylindrical"}:
                kinds = {"shaft", "hole", "cylinder"}
            else:
                issue("unsupported_relation", path,
                      f"{requirement.id}（{requirement.type}）尚无确定性几何审核实现；保留要求，不能以模型 PASS 替代。",
                      refs=refs, target="verifier")
                continue
            for ref in refs:
                interface = interfaces[ref]
                if interface.kind not in kinds:
                    issue("nonfunctional_interface", path,
                          f"{requirement.id} 引用的 {ref} 是 {interface.kind}，当前审核需要 {sorted(kinds)}。"
                          "若原图只支持模糊区域，应保留未决并回访证据，不要虚构隐藏轴孔。", refs=[ref])

    report = {
        "schema": VERSION, "status": "blocked" if issues else "compatible",
        "object_id": blueprint.object_id,
        "input_sha256": {"blueprint": digest(blueprint.model_dump(mode="json"))},
        "organizational_parents": parents,
        "joint_parents": incoming if assembly else {j.child: j.parent for j in blueprint.joints},
        "issues": issues,
        "summary": "结构尚不能交给几何作者，请先处理连接树和功能面要求。" if issues else
                   "结构与当前后端接口相容；仍需尺度、几何、外观及物理检查。",
        "scope": "description/backend compatibility only; no geometry or physical verification",
        "geometry_status": "not_run", "physics_status": "not_run",
    }
    report["report_sha256"] = digest(report)
    return report


def require_readiness(blueprint, output):
    from ..io import write_json

    report = analyze_readiness(blueprint)
    write_json(output / "construction_readiness.json", report)
    if report["status"] == "blocked":
        raise ValueError("结构构建前检查未通过：" + "; ".join(r["message"] for r in report["issues"]))
    return report
