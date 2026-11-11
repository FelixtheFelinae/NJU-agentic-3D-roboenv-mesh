"""Construction hypotheses are separate, hash-bound derivatives of a description."""

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictFloat

from ..construction.assembly_blueprint import AssemblyBlueprint, digest
from ..construction.assembly_design import check_assembly
from ..io import read_json, sha256, write_json


class Assumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=200)
    value: StrictFloat | Literal["at_min", "at_max", "centered"]
    unit: Literal["m", "kg", "rad", "convention"]
    rationale: str = Field(min_length=3, max_length=1000)
    evidence_refs: list[str] = Field(min_length=1, max_length=16)


class BuildHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_blueprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parameters: list[Assumption] = Field(min_length=1, max_length=32)


def requirements(blueprint):
    """Backend prerequisites, not measurements recovered from images."""
    if not isinstance(blueprint, AssemblyBlueprint):
        return []
    missing = []
    dimensions = [blueprint.target_dimensions_m, *(p.dimensions_m for p in blueprint.part_definitions)]
    if not any(q.value is not None for d in dimensions for q in (d.x, d.y, d.z)):
        missing.append({"path": "/target_dimensions_m/x", "reason": "缺少绝对尺度；可保留未知，或另立明确的试验尺度假设"})
    for joint in blueprint.joints:
        if joint.type in {"revolute", "prismatic"}:
            if joint.rest is None:
                missing.append({"path": f'/joints/{joint.name}/rest', "reason": "构建需要参考位姿对应的限位约定"})
            if joint.travel.value is None:
                missing.append({"path": f'/joints/{joint.name}/travel', "reason": "构建需要明确的候选行程，并非视频测量"})
    return missing


def apply(source, raw_plan, observation_refs):
    if not isinstance(source, AssemblyBlueprint):
        raise ValueError("构建假设仅支持新版 AssemblyBlueprint，请先明确迁移旧描述")
    if source.coordinate_units != "m":
        raise ValueError("相对尺度不能通过构建假设改写为米；需要独立尺度适配")
    plan = BuildHypothesis.model_validate(raw_plan)
    if len({p.path for p in plan.parameters}) != len(plan.parameters):
        raise ValueError("一个构建假设不能重复设置同一参数")
    data = source.model_dump(mode="json")
    ref = "prior:build-" + digest(plan.model_dump(mode="json"))[:16]
    data["evidence"].append({
        "id": ref, "origin": "prior",
        "description": "Explicit construction hypothesis; not a measurement or observed motion.",
    })
    declared = {row["id"] for row in data["evidence"]}
    for actual_ref in sorted({ref for p in plan.parameters for ref in p.evidence_refs} & set(observation_refs)):
        if actual_ref not in declared:
            data["evidence"].append({
                "id": actual_ref, "origin": "observation",
                "description": "Actual reference from the bound source packet; does not measure this assumed value.",
            })
    definitions = {row["id"]: row for row in data["part_definitions"]}
    used_definitions = {row["definition_id"] for row in data["part_instances"]}
    joints = {row["name"]: row for row in data["joints"]}
    for parameter in plan.parameters:
        if not parameter.rationale.strip() or not set(parameter.evidence_refs) <= set(observation_refs):
            raise ValueError("构建假设必须引用实际提供的原始观察并说明理由，不能引用生成图")
        parts = parameter.path.split("/")
        if len(parts) > 2 and parts[1] == "part_definitions" and parts[2] not in used_definitions:
            raise ValueError("不能为未实例化的零件声明构建参数")
        owner, key, unit = None, None, None
        if len(parts) == 3 and parts[1] == "target_dimensions_m" and parts[2] in "xyz" and len(parts[2]) == 1:
            owner, key, unit = data["target_dimensions_m"], parts[2], "m"
        elif len(parts) == 5 and parts[1] == "part_definitions" and parts[2] in definitions and parts[3] == "dimensions_m" and parts[4] in {"x", "y", "z"}:
            owner, key, unit = definitions[parts[2]]["dimensions_m"], parts[4], "m"
        elif len(parts) == 4 and parts[1] == "part_definitions" and parts[2] in definitions and parts[3] == "mass_kg":
            owner, key, unit = definitions[parts[2]], "mass_kg", "kg"
        elif len(parts) == 4 and parts[1] == "joints" and parts[2] in joints and parts[3] in {"travel", "rest"}:
            owner, key = joints[parts[2]], parts[3]
            if owner["type"] not in {"revolute", "prismatic"}:
                raise ValueError("只能为有界转动或滑动关节声明参考约定/行程")
            unit = "convention" if key == "rest" else "rad" if owner["type"] == "revolute" else "m"
        if owner is None or not parameter.path.startswith("/") or parameter.unit != unit:
            raise ValueError("未支持的构建参数路径或单位：" + parameter.path)
        if key == "rest":
            if owner[key] is not None or parameter.value not in {"at_min", "at_max", "centered"}:
                raise ValueError("只能填补未知 rest，且必须明确 at_min/at_max/centered")
            owner[key] = parameter.value
            owner["evidence_refs"] = list(dict.fromkeys([*owner["evidence_refs"], ref]))
        else:
            value = parameter.value
            maximum = 1000 if unit == "kg" else 4 * math.pi if unit == "rad" else 100
            if owner[key]["value"] is not None:
                raise ValueError("不能用构建假设覆盖已有参数：" + parameter.path)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError("构建假设数值必须为有界的正有限数")
            owner[key] = {
                "value": value, "source": "assumed",
                "evidence_refs": [ref, *parameter.evidence_refs],
                "rationale": "Construction hypothesis only: " + parameter.rationale,
            }
        data["assumptions"].append(f'Construction only {parameter.path} = {parameter.value} {unit}: {parameter.rationale}')
    return AssemblyBlueprint.model_validate(data)


def materialize(source_path, raw_plan, output, observation_refs):
    plan = BuildHypothesis.model_validate(raw_plan)
    if sha256(source_path) != plan.source_blueprint_sha256:
        raise ValueError("构建假设未绑定当前语义描述，拒绝使用过期版本")
    source = AssemblyBlueprint.model_validate(read_json(source_path))
    candidate = apply(source, raw_plan, observation_refs)
    report = check_assembly(candidate)
    output.mkdir(parents=True)
    write_json(output / "blueprint.json", candidate.model_dump(mode="json"))
    write_json(output / "selfcheck.json", report)
    write_json(output / "hypothesis.json", {
        "schema": "envcreator.build-hypothesis/1", "status": report["status"],
        "source_blueprint_sha256": plan.source_blueprint_sha256,
        "candidate_blueprint_sha256": sha256(output / "blueprint.json"),
        "plan": plan.model_dump(mode="json"), "unresolved_backend": requirements(candidate),
        "scope": "assumed construction candidate; canonical observation description is unchanged",
        "physics_status": "not_run", "measurement_status": "not_measured",
    })


def audit(blueprint, recipe, raw_plan):
    """Check adopted numerical assumptions on the actual recipe, before the other gates."""
    from .geometric_verifier import _offsets, rotation

    plan = BuildHypothesis.model_validate(raw_plan)
    instances = {p.name: p.definition_id for p in blueprint.part_instances}
    checks = []
    for parameter in plan.parameters:
        path = parameter.path.split("/")
        result = {"path": parameter.path, "expected": parameter.value, "unit": parameter.unit,
                  "status": "passed", "actual": parameter.value}
        if path[1] == "part_definitions" and path[-1] == "mass_kg":
            masses = [recipe.part_masses_kg[name] for name, definition in instances.items() if definition == path[2]]
            result.update(actual=masses, status="passed" if all(
                math.isclose(v, parameter.value, rel_tol=1e-6) for v in masses) else "failed")
        elif path[1] == "target_dimensions_m" or (len(path) == 5 and path[3] == "dimensions_m"):
            # Every assumed dimension is checked in the builder's reference pose.
            part_names = None if path[1] == "target_dimensions_m" else {
                name for name, definition in instances.items() if definition == path[2]
            }
            solids = [s for s in recipe.solids if part_names is None or s.part in part_names]
            if not solids or any(s.shape not in {"box", "hollow_box"} for s in solids):
                result.update(status="unresolved", actual=None, message="尺寸审核当前只支持盒状配方包络")
            else:
                try:
                    offsets = _offsets(blueprint, recipe) if part_names is None else {p: np.zeros(3) for p in instances}
                    groups = [solids] if part_names is None else [
                        [solid for solid in solids if solid.part == name] for name in sorted(part_names)
                    ]
                    extents = []
                    axis = "xyz".index(path[-1])
                    for group in groups:
                        bounds = []
                        for solid in group:
                            half = np.abs(rotation(solid.rotation_rpy)) @ (np.asarray(solid.size_m) / 2)
                            center = np.asarray(solid.center_m) + offsets[solid.part]
                            bounds.append((center - half, center + half))
                        extents.append(float(np.max([p[1] for p in bounds], axis=0)[axis] -
                                             np.min([p[0] for p in bounds], axis=0)[axis]))
                    result.update(actual=extents[0] if len(extents) == 1 else extents,
                                  status="passed" if all(math.isclose(
                                      v, parameter.value, rel_tol=1e-3, abs_tol=1e-5) for v in extents) else "failed")
                except ValueError as exc:
                    result.update(status="unresolved", actual=None, message=str(exc))
        elif path[1] == "joints":
            joint = next(j for j in blueprint.joints if j.name == path[2])
            actual = joint.rest if path[-1] == "rest" else joint.travel.value
            result.update(actual=actual, status="passed" if actual == parameter.value else "failed")
        checks.append(result)
    if any("dimensions_m" in p.path for p in plan.parameters):
        for solid in recipe.solids:
            if solid.source not in {"assumed", "estimated"}:
                checks.append({"path": f"/solids/{solid.name}/source", "status": "failed",
                               "message": "假设尺度的几何不能标记为原图观测值"})
    return {
        "schema": "envcreator.build-hypothesis-audit/1",
        "status": "failed" if any(r["status"] == "failed" for r in checks)
                  else "unresolved" if any(r["status"] == "unresolved" for r in checks) else "passed",
        "checks": checks, "issues": [r for r in checks if r["status"] != "passed"],
        "input_sha256": {"blueprint": digest(blueprint.model_dump(mode="json")),
                         "recipe": digest(recipe.model_dump(mode="json")),
                         "plan": digest(plan.model_dump(mode="json"))},
        "scope": "analytic recipe envelopes and adopted assumptions; not mesh/contact/physical verification",
    }


def bound_report(path, blueprint_path):
    report = read_json(path)
    if report.get("schema") != "envcreator.build-hypothesis/1":
        raise ValueError("构建假设记录格式无效")
    plan = BuildHypothesis.model_validate(report["plan"])
    if (report.get("candidate_blueprint_sha256") != sha256(blueprint_path)
            or report.get("source_blueprint_sha256") != plan.source_blueprint_sha256):
        raise ValueError("构建假设与当前候选不匹配")
    return report
