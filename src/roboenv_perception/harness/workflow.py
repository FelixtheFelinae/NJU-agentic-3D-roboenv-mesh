"""Durable, single-target EnvCreator workflow. The caller owns the task-model session."""

import fcntl
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ..artifacts.registry import content_digest, tree_index, verify_tree
from ..components.catalog import requirements
from ..construction.assembly_blueprint import parse_blueprint
from ..construction.assembly_readiness import VERSION as READINESS_VERSION
from ..construction.assembly_readiness import analyze_readiness
from ..execution.context import emit
from ..io import read_json, relative_file, sha256, write_json
from . import workflow_critic as critic
from . import workflow_evidence as evidence
from . import workflow_hypothesis as hypothesis
from .workflow_hypothesis import BuildHypothesis

VIDEO = {".mp4", ".mov", ".mkv", ".webm"}
ACTIONS = {
    "sampling": ["sample_video", "stop"],
    "target": ["select_target", "revisit_video", "stop"],
    "structure": ["accept_structure", "propose_build", "repair_constraints", "revise_structure", "revisit_video", "stop"],
    "recovery": ["propose_build", "repair_constraints", "revise_structure", "revisit_video", "stop"],
    "physical_recovery": ["revisit_video", "stop"],
}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    background: Literal["off", "masks", "reconstruct"] = "off"
    physics: bool = False


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "resume", "stop", "sample_video", "select_target", "accept_structure",
        "revise_structure", "revisit_video",
        "propose_build",
        "repair_constraints",
    ]
    revision: int | None = Field(default=None, ge=0)
    sampling: dict | None = None
    object_id: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str | None = Field(default=None, min_length=1, max_length=1500)
    evidence_refs: list[str] | None = Field(default=None, min_length=1, max_length=16)
    hypothesis: BuildHypothesis | None = None
    constraint_ids: list[str] | None = Field(default=None, min_length=1, max_length=4)


def atomic_json(path, value):
    tmp = path.with_name("." + path.name + ".next")
    write_json(tmp, value)
    os.replace(tmp, path)


def stable_digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def config_identity(settings):
    return {
        key: sha256(path) if path and path.is_file() else None
        for key in ("perception_config", "construction_config")
        if (path := getattr(settings, key, None))
    }


class Workflow:
    def __init__(self, root, settings, task, inputs, options, *, execute=None):
        self.root = root.resolve()
        self.settings, self.task, self.inputs = settings, task, list(inputs)
        self.options = Options.model_validate(options).model_dump()
        self.video = len(inputs) == 1 and Path(inputs[0]["name"]).suffix.lower() in VIDEO
        self.execute = execute
        self.state_path = self.root / "workflow.json"
        self.deadline = time.monotonic() + settings.max_wall_seconds
        self.state = {}
        self.used = {}

    def save(self):
        atomic_json(self.state_path, self.state)

    def load(self):
        if not self.inputs:
            raise ValueError("需要上传一段视频或一组图片")
        if not self.video and any(Path(row["name"]).suffix.lower() in VIDEO for row in self.inputs):
            raise ValueError("视频不能与其他输入混合")
        if self.options["physics"] and not self.settings.physics:
            raise ValueError("此工作台未启用物理验证")
        for row in self.inputs:
            if sha256(Path(row["path"])) != row["sha256"]:
                raise ValueError("原始输入哈希变化，请创建新任务")
        signature = stable_digest({
            "task": self.task, "inputs": self.inputs, "options": self.options,
            "settings": self.settings.model_dump(mode="json"), "configs": config_identity(self.settings),
        })
        if self.state_path.is_file():
            self.state = read_json(self.state_path)
            if self.state["signature"] != signature:
                raise ValueError("输入或运行配置发生变化，请新建工作流")
        else:
            self.state = {
                "schema": "envcreator.workflow/1", "signature": signature, "revision": 0,
                "phase": "initial", "round": 0, "rounds": [], "stages": {}, "history": [],
                "critic": [], "geometry_repairs": 0, "physics_status": "not_run",
                "objects": [], "observed_frames": [], "stable_target": None,
            }
            self.save()
        self.state.setdefault("evidence_origins", [])
        self.state.setdefault("build_attempts", 0)

    def path(self, ref):
        return relative_file(self.root, ref)

    def verify(self, receipt):
        by_ref = {row["ref"]: row for row in self.state["stages"].values()}
        for ref, digest, _export in receipt.get("sources", {}).values():
            upstream = by_ref.get(ref)
            if upstream is None or upstream["digest"] != digest:
                raise ValueError("上游阶段记录发生变化")
            if ref not in self.used:
                self.verify(upstream)
        if receipt["ref"] not in self.used:
            verify_tree(self.path(receipt["ref"]), receipt["files"])
            self.used[receipt["ref"]] = receipt["files"]
        elif self.used[receipt["ref"]] != receipt["files"]:
            raise ValueError("同一阶段出现不同哈希")
        return self.path(receipt["ref"])

    def ref(self, receipt, role):
        return relative_file(self.verify(receipt), receipt["exports"][role])

    def stage(self, operation, sources=None, *, params=None, object_id=None):
        sources, params = sources or {}, params or {}
        # Changed verifier semantics must not reuse an older PASS receipt.
        verifier_key = {}
        if operation == "geometry_audit":
            from .geometric_verifier import VERSION as GEOMETRY_VERSION

            verifier_key = {"verifier_version": GEOMETRY_VERSION}
        elif operation == "blueprint_repair":
            from ..construction.constraint_repair import VERSION as REPAIR_VERSION

            verifier_key = {"repair_version": REPAIR_VERSION}
        bindings = {}
        for role, (receipt, export) in sources.items():
            path = self.ref(receipt, export)
            bindings[role] = {
                "root": str(self.root), "ref": str(path.relative_to(self.root)),
                "files": tree_index(path) if path.is_dir() else {".": sha256(path)},
            }
        key = stable_digest({
            **verifier_key,
            "operation": operation, "object_id": object_id, "params": params,
            "sources": {r: [row["ref"], row["digest"], export] for r, (row, export) in sources.items()},
        })
        old = self.state["stages"].get(key)
        if old:
            self.verify(old)
            emit("workflow.reused", name=operation, round=self.state["round"])
            return old
        if time.monotonic() >= self.deadline:
            raise TimeoutError("工作流达到本次执行时限，可从已保存阶段继续")
        internal = {"video_overview", "evidence_fusion", "build_hypothesis", "build_hypothesis_audit", "structure_audit"}
        missing = [] if operation in internal else requirements(operation, self.settings)
        if missing:
            raise ValueError(f'{operation} 缺少运行配置：{", ".join(missing)}')
        ref = f'stages/{operation}-{uuid.uuid4().hex[:12]}'
        output = self.path(ref)
        self.state["active_stage"] = operation
        self.save()
        if operation == "video_overview":
            evidence.overview(Path(self.inputs[0]["path"]), output)
            exports, status = {"video_overview": "overview.json"}, "observed"
        elif operation in internal:
            from ..components.sources import source_path

            paths = {role: source_path(binding) for role, binding in bindings.items()}
            if operation == "structure_audit":
                report = analyze_readiness(parse_blueprint(read_json(paths["blueprint"])))
                write_json(output / "construction_readiness.json", report)
                exports, status = {"construction_readiness": "construction_readiness.json"}, report["status"]
            elif operation == "evidence_fusion":
                origins = [
                    {**row, "evidence": paths[f"evidence_{i}"], "perception": paths[f"perception_{i}"]}
                    for i, row in enumerate(params["origins"])
                ]
                evidence.fuse(origins, params["selection"], output / "evidence",
                              stable_target=self.state["stable_target"],
                              identity_reason=params["reason"],
                              video_sha256=self.inputs[0]["sha256"] if self.video else None)
                exports, status = {"evidence": "evidence"}, "ready"
            elif operation == "build_hypothesis":
                hypothesis.materialize(paths["blueprint"], params["hypothesis"], output,
                                       {v["ref"] for v in self.state["target"]["views"]})
                exports = {"blueprint": "blueprint.json", "build_hypothesis": "hypothesis.json"}
                status = read_json(output / "selfcheck.json")["status"]
            else:
                from .geometry import GeometryRecipe

                report = hypothesis.audit(
                    parse_blueprint(read_json(paths["blueprint"])),
                    GeometryRecipe.model_validate(read_json(paths["recipe"])),
                    read_json(paths["build_hypothesis"])["plan"],
                )
                write_json(output / "hypothesis_audit.json", report)
                exports, status = {"hypothesis_audit": "hypothesis_audit.json"}, report["status"]
        else:
            execute = self.execute
            if execute is None:
                from ..components.operations import run as execute
            exports, status = execute(
                operation, bindings, output, self.settings, self.task,
                inputs=self.inputs if operation == "perceive" else (),
                params=params, object_id=object_id, origin="workflow", workflow=True,
            )
        for path in exports.values():
            if not relative_file(output, path).exists():
                raise ValueError("阶段未生成声明的产物")
        files = tree_index(output)
        if not files:
            raise ValueError("阶段未生成可验证的文件")
        row = {
            "operation": operation, "ref": ref, "exports": exports, "status": status,
            "files": files, "digest": content_digest(files),
            "sources": {role: [value["ref"], value["digest"], export]
                        for role, (value, export) in sources.items()},
        }
        self.state["stages"][key] = row
        self.state["active_stage"] = None
        self.save()
        return row

    def phase(self, phase, **data):
        self.state.update(phase=phase, active_stage=None, **data)
        self.save()
        emit("workflow.phase", name=phase, round=self.state["round"])

    def actions(self):
        actions = ACTIONS.get(self.state["phase"], []).copy()
        if not self.video:
            actions = [v for v in actions if v != "revisit_video"]
        if self.state["round"] >= self.settings.max_revisions:
            actions = [v for v in actions if v not in {"revise_structure", "repair_constraints", "revisit_video"}]
        if "repair_constraints" in actions:
            from ..construction.assembly_blueprint import AssemblyBlueprint

            bp = parse_blueprint(read_json(self.ref(self.state["blueprint"], "blueprint"))) if self.state.get("blueprint") else None
            if not isinstance(bp, AssemblyBlueprint) or not bp.geometric_constraints:
                actions.remove("repair_constraints")
        if (not self.state.get("blueprint") or not self.state.get("blueprint_review") or
                self.state["blueprint_review"]["status"] != "review_passed" or
                self.state["build_attempts"] > self.settings.max_revisions):
            actions = [v for v in actions if v != "propose_build"]
        return actions

    def origins(self, rows):
        by_ref = {row["ref"]: row for row in self.state["stages"].values()}
        return [
            {**row, "evidence": self.ref(by_ref[row["evidence"]], "evidence"),
             "perception": self.ref(by_ref[row["perception"]], "perception")}
            for row in rows
        ]

    def current_origin(self, object_id):
        s = self.state
        return {"evidence": s["evidence"]["ref"], "perception": s["perception"]["ref"],
                "object_id": object_id, "video": self.video, "round": s["round"]}

    def evidence_pool(self, object_id):
        rows = [*self.state["evidence_origins"], self.current_origin(object_id)]
        return rows, evidence.pool(self.origins(rows))

    def active_blueprint(self):
        return self.state.get("build_blueprint") or self.state["blueprint"]

    def decide(self, raw, actor):
        decision = Decision.model_validate(raw)
        action = decision.action
        s = self.state
        if action == "resume":
            if set(raw) != {"action"}:
                raise ValueError("resume 不接受修订参数")
            return
        if action not in self.actions() or decision.revision != s["revision"]:
            raise ValueError("当前工作流不接受该决策，或决策版本已过期")
        required = {"sample_video": {"sampling"}, "revisit_video": {"sampling", "reason"},
                    "select_target": {"object_id", "reason"}, "revise_structure": {"reason"},
                    "accept_structure": {"reason"}, "stop": {"reason"},
                    "repair_constraints": {"reason", "constraint_ids"},
                    "propose_build": {"reason", "hypothesis"}}[action]
        optional = {"evidence_refs"} if action == "select_target" else set()
        if set(raw) - optional != required | {"action", "revision"} or any(
            getattr(decision, key) is None or
            (isinstance(getattr(decision, key), str) and not getattr(decision, key).strip())
            for key in required
        ):
            raise ValueError("决策必须包含对应的参数和简明理由")
        if action in {"sample_video", "revisit_video"}:
            overview = read_json(self.ref(s["video_overview"], "video_overview"))
            sampling = evidence.Sampling.decision(decision.sampling, overview["duration_seconds"])
            if sampling in s.get("sampling_history", []):
                raise ValueError("采样设置已执行；请针对缺口选择其他时段或停止")
        if action == "select_target":
            self.verify(s["evidence"])
            matches = [o for o in s["objects"] if o["object_id"] == decision.object_id]
            if len(matches) != 1 or matches[0]["status"] != "ready_for_design":
                raise ValueError("目标必须是本轮证据充足的真实对象 ID")
            target = matches[0]
            if s.get("revisit"):
                if not {v["source_frame"] for v in target["views"]} - set(s["observed_frames"]):
                    self.phase("target", reason="这个对象没有增加实际用于建模的原始帧；预算内可另选时段或停止。")
                    return
                origins, candidates = self.evidence_pool(decision.object_id)
                selected = evidence.selection(candidates, decision.evidence_refs,
                                              previous_frames=s["observed_frames"])
            elif decision.evidence_refs is not None:
                raise ValueError("evidence_refs 用于跨轮融合；首轮由证据组件整理")
        if action == "propose_build":
            path = self.ref(s["blueprint"], "blueprint")
            if decision.hypothesis.source_blueprint_sha256 != sha256(path):
                raise ValueError("构建假设未绑定当前语义描述")
            hypothesis.apply(parse_blueprint(read_json(path)),
                             decision.hypothesis.model_dump(mode="json"),
                             {v["ref"] for v in s["target"]["views"]})
        if action == "repair_constraints":
            from ..construction.constraint_repair import select_constraints

            select_constraints(parse_blueprint(read_json(self.ref(s["blueprint"], "blueprint"))),
                               decision.constraint_ids)
        s["history"].append({
            "revision": s["revision"], "round": s["round"], "phase": s["phase"],
            **decision.model_dump(exclude_none=True), "actor": actor,
        })
        s["revision"] += 1
        if action == "stop":
            self.phase("stopped", reason=decision.reason)
        elif action in {"sample_video", "revisit_video"}:
            if action == "revisit_video":
                self.new_round("evidence", decision.reason)
            s.setdefault("sampling_history", []).append(sampling)
            self.phase("perception", sampling=sampling, objects=[], target=None)
        elif action == "select_target":
            prior = set(s["observed_frames"])
            actual = {v["source_frame"] for v in target["views"]}
            s["stable_target"] = s["stable_target"] or target["object_id"]
            if s.get("revisit"):
                pending = {"origins": origins, "selection": [v["ticket"] for v in selected],
                           "reason": decision.reason}
            else:
                s["evidence_origins"] = [self.current_origin(decision.object_id)]
                pending = None
            self.phase("design", target=target, pending_fusion=pending, revisit=False, reason=None,
                       latest_track_id=decision.object_id,
                       observed_frames=sorted(prior | actual) if pending is None else s["observed_frames"],
                       direction=f'{s.get("direction") or ""}\n目标身份判断：{decision.reason}'.strip())
        elif action == "revise_structure":
            self.new_round("structure", decision.reason)
            self.phase("design", previous=s["blueprint"])
        elif action == "repair_constraints":
            self.new_round("constraints", decision.reason)
            self.phase("design", previous=s["blueprint"], pending_constraint_ids=decision.constraint_ids)
        elif action == "accept_structure":
            self.phase("construction")
        elif action == "propose_build":
            s["build_attempts"] += 1
            self.phase("hypothesis", pending_hypothesis=decision.hypothesis.model_dump(mode="json"),
                       build_blueprint=None, build_review=None, reason=decision.reason, built=None)

    def new_round(self, kind, reason):
        s = self.state
        s["rounds"].append({
            "round": s["round"], "kind": kind, "reason": reason,
            "target": s.get("target", {}).get("object_id") if s.get("target") else None,
            "blueprint_ref": s["blueprint"]["ref"] if s.get("blueprint") else None,
            "candidate_ref": s["built"]["ref"] if s.get("built") else None,
            "diagnostic": s["critic"][-1] if s["critic"] else None,
        })
        if not s["evidence_origins"] and s.get("target"):
            s["evidence_origins"] = [self.current_origin(s["target"]["object_id"])]
        s.update(
            round=s["round"] + 1, geometry_repairs=0, direction=reason,
            revisit=kind == "evidence", previous=None, built=None, physics_status="not_run",
            blueprint=None if kind == "evidence" else s["blueprint"], final_stages=[],
            build_blueprint=None, build_review=None, pending_hypothesis=None,
            pending_fusion=None, build_attempts=0,
            structure_readiness=None,
            pending_constraint_ids=None,
        )

    def record(self, receipt, role, candidate, report_name=None):
        path = self.ref(receipt, role)
        if report_name:
            path = relative_file(path if path.is_dir() else path.parent, report_name)
        report = read_json(path)
        s = self.state
        entry = critic.finding(
            receipt["operation"], receipt["status"], report, report_ref=str(path.relative_to(self.root)),
            candidate=candidate, observation_refs=[
                {"ref": v["ref"], "source_frame": v["source_frame"]} for v in s["target"]["views"]
            ],
        )
        if not s["critic"] or s["critic"][-1] != entry:
            s["critic"].append(entry)
        return entry

    def candidate(self, **rows):
        return {role: {"ref": row["ref"], "sha256": row["digest"]} for role, row in rows.items()}

    def preflight_build(self):
        from ..construction.assembly_blueprint import (
            AssemblyBlueprint,
            legacy_projection,
            parse_blueprint,
        )

        blueprint = parse_blueprint(read_json(self.ref(self.active_blueprint(), "blueprint")))
        if isinstance(blueprint, AssemblyBlueprint):
            legacy_projection(blueprint)
            if any(j.travel.value is None for j in blueprint.joints if j.type in {"revolute", "prismatic"}):
                raise ValueError("当前构建后端需要明确的关节行程；视频未提供的量保留为未知")
            if hypothesis.requirements(blueprint):
                raise ValueError("当前构建后端缺少尺度依据；请保留未知或声明独立的试验尺度假设")

    def advance(self):
        s = self.state
        if s["phase"] == "initial":
            if self.video:
                self.phase("sampling", video_overview=self.stage("video_overview"))
            else:
                self.phase("perception")
        if s["phase"] == "perception":
            perception = self.stage("perceive", params={"sampling": s["sampling"]} if self.video else {})
            prepared = self.stage("evidence", {"perception": (perception, "perception")})
            objects = evidence.observations(
                self.ref(prepared, "evidence"), self.ref(perception, "perception"), self.video,
            )
            self.phase("target", perception=perception, evidence=prepared, objects=objects,
                       reason=None if objects else "本次采样没有找到对象")
        if s["phase"] == "design":
            if s.get("pending_fusion"):
                pending = s["pending_fusion"]
                by_ref = {row["ref"]: row for row in s["stages"].values()}
                sources = {}
                for i, row in enumerate(pending["origins"]):
                    sources[f"evidence_{i}"] = (by_ref[row["evidence"]], "evidence")
                    sources[f"perception_{i}"] = (by_ref[row["perception"]], "perception")
                merged = self.stage("evidence_fusion", sources, params={
                    **pending, "format_revision": evidence.FUSION_FORMAT, "origins": [
                        {k: row[k] for k in ("object_id", "round", "video")} for row in pending["origins"]
                    ],
                })
                objects = evidence.observations(self.ref(merged, "evidence"), None, False)
                self.phase("design", evidence_origins=pending["origins"], pending_fusion=None,
                           evidence=merged, objects=objects, target=objects[0],
                           observed_frames=sorted(set(s["observed_frames"]) |
                                                  {v["source_frame"] for v in objects[0]["views"]}))
            target = s["target"]["object_id"]
            observed = {"evidence": (s["evidence"], "evidence")}
            dispositions = [
                {"asset_id": o["asset_id"], "action": "build" if o["object_id"] == target else "exclude",
                 "reason": "当前工作流选定的单个对象" if o["object_id"] == target else "不在此单对象工作流范围内"}
                for o in s["objects"]
            ]
            scope = self.stage("scope_apply", observed, params={"objects": dispositions})
            sources = {**observed, "scope": (scope, "scope")}
            diagnostic = s["rounds"][-1]["diagnostic"] if s["rounds"] else None
            feedback = [f'决策方向：{s["direction"]}'] if s.get("direction") else []
            if diagnostic:
                feedback += ["旧候选诊断（不是观测证据）：" + issue for issue in critic.feedback(diagnostic)[:12]]
            if s.get("previous"):
                sources["previous"] = (s["previous"], "blueprint")
            if s.get("pending_constraint_ids"):
                bp = self.stage("blueprint_repair", {
                    **observed, "blueprint": (s["previous"], "blueprint"),
                }, object_id=target, params={"constraint_ids": s["pending_constraint_ids"], "feedback": feedback})
                self.record(bp, "constraint_repair", self.candidate(blueprint=bp), "constraint_repair.json")
            else:
                bp = self.stage("blueprint", sources, object_id=target, params={"feedback": feedback})
            s.update(blueprint=bp, blueprint_review=None)
            if bp["status"] != "needs_revision":
                review = self.stage("blueprint_review", {**observed, "blueprint": (bp, "blueprint")}, object_id=target)
                s["blueprint_review"] = review
                self.record(review, "blueprint_review", self.candidate(blueprint=bp))
                if review["status"] == "review_passed":
                    self.audit_structure(bp)
                    self.phase("structure", reason=None)
                    return
            else:
                self.record(bp, "blueprint", self.candidate(blueprint=bp), "selfcheck.json")
            self.phase("recovery", reason="结构检查未通过；需要修订假设、回访原视频或保留未决")
        if s["phase"] == "hypothesis":
            derivative = self.stage("build_hypothesis", {"blueprint": (s["blueprint"], "blueprint")},
                                    params={"hypothesis": s["pending_hypothesis"]})
            s["build_blueprint"] = derivative
            report = read_json(self.ref(derivative, "build_hypothesis"))
            if derivative["status"] == "needs_revision" or report["unresolved_backend"]:
                self.phase("recovery", reason="构建假设仍缺少明确参数；原始结构描述保持不变")
                return
            review = self.stage("blueprint_review", {
                "blueprint": (derivative, "blueprint"), "evidence": (s["evidence"], "evidence"),
            }, object_id=s["target"]["object_id"])
            s["build_review"] = review
            self.record(review, "blueprint_review", self.candidate(blueprint=derivative))
            self.audit_structure(derivative)
            self.phase("structure" if review["status"] == "review_passed" else "recovery",
                       reason="独立构建假设已保存并审核；原始未知量没有被改写")
            return
        if s["phase"] == "construction":
            self.construct()
        if s["phase"] == "validation":
            self.finish()

    def audit_structure(self, blueprint):
        receipt = self.stage("structure_audit", {"blueprint": (blueprint, "blueprint")},
                             params={"version": READINESS_VERSION})
        self.state["structure_readiness"] = receipt
        self.record(receipt, "construction_readiness", self.candidate(blueprint=blueprint))
        self.save()
        return receipt

    def construct(self):
        s = self.state
        target = s["target"]["object_id"]
        bp = self.active_blueprint()
        readiness = self.audit_structure(bp)
        if readiness["status"] != "compatible":
            self.phase("recovery", reason="连接树或功能面要求尚不能构建；请根据原始画面修订结构，禁止进入几何作者")
            return
        try:
            self.preflight_build()
        except ValueError as exc:
            s["critic"].append(critic.finding(
                "backend_preflight", "blocked", {"issues": [str(exc)]},
                report_ref=str(self.ref(bp, "blueprint").relative_to(self.root)),
                candidate=self.candidate(blueprint=bp),
                observation_refs=[{"ref": v["ref"], "source_frame": v["source_frame"]} for v in s["target"]["views"]],
            ) | {"suggested_scope": "structure_or_evidence"})
            self.phase("recovery", reason="结构描述已保留；当前构建后端不支持这些未决参数或结构")
            return
        base = {"blueprint": (bp, "blueprint"), "evidence": (s["evidence"], "evidence")}
        if s.get("build_blueprint"):
            base["build_hypothesis"] = (s["build_blueprint"], "build_hypothesis")
        recipe = self.stage("geometry", base, object_id=target)
        for index in range(2):
            sources = {**base, "recipe": (recipe, "recipe")}
            if s.get("build_blueprint"):
                bound = self.stage("build_hypothesis_audit", sources, object_id=target)
                report = self.record(bound, "hypothesis_audit", self.candidate(blueprint=bp, recipe=recipe))
                if bound["status"] != "passed":
                    if bound["status"] == "failed" and index == 0:
                        recipe = self.stage("geometry", {**base, "previous": (recipe, "recipe")},
                                            object_id=target, params={"feedback": critic.feedback(report)})
                        s["geometry_repairs"] = 1
                        continue
                    self.phase("recovery", reason="配方未满足已采纳的构建假设；禁止进入构建")
                    return
            geo = self.stage("geometry_audit", sources, object_id=target)
            report = self.record(geo, "geometric_verification", self.candidate(blueprint=bp, recipe=recipe),
                                 "geometric_verification.json")
            if geo["status"] in {"passed", "not_applicable"}:
                break
            if report["suggested_scope"] == "geometry" and index == 0:
                recipe = self.stage(
                    "geometry", {**base, "previous": (recipe, "recipe"),
                                 "geometric_verification": (geo, "geometric_verification")},
                    object_id=target, params={"feedback": critic.feedback(report)},
                )
                s["geometry_repairs"] = 1
                self.save()
                continue
            self.phase("recovery", reason="确定性几何检查未通过；当前配方禁止进入构建")
            return
        assembly = self.stage("assembly_audit", sources, object_id=target)
        self.record(assembly, "assembly", self.candidate(blueprint=bp, recipe=recipe), "assembly_validation.json")
        if assembly["status"] not in {"geometrically_plausible", "not_applicable"}:
            self.phase("recovery", reason="装配约束检查未通过；当前配方禁止进入构建")
            return
        built = self.stage("build", {
            **sources, "blueprint_review": (s.get("build_review") or s["blueprint_review"], "blueprint_review"),
            "assembly": (assembly, "assembly"),
        }, object_id=target)
        s["built"] = built
        visual = self.stage("geometry_review", {**sources, "built": (built, "built")}, object_id=target)
        report = read_json(self.ref(visual, "assets") / "assets.json")["objects"][0]["review"]
        entry = critic.finding(
            "geometry_review", visual["status"], report,
            report_ref=str((self.ref(visual, "assets") / "assets.json").relative_to(self.root)),
            candidate=self.candidate(blueprint=bp, recipe=recipe, built=built),
            observation_refs=[{"ref": v["ref"], "source_frame": v["source_frame"]} for v in s["target"]["views"]],
        )
        if not s["critic"] or s["critic"][-1] != entry:
            s["critic"].append(entry)
        if visual["status"] != "review_passed":
            self.phase("recovery", reason="候选外观不通过；渲染只用于诊断，不进入证据池")
            return
        self.phase("validation", visual=visual)

    def finish(self):
        s = self.state
        finals = []
        if self.options["physics"]:
            sources = {"assets": (s["visual"], "assets")}
            scene = self.stage("scene", {**sources, "evidence": (s["evidence"], "evidence")})
            if scene["status"] == "blocked":
                self.record(scene, "scene", self.candidate(built=s["built"]), "scene_result.json")
                self.phase("physical_recovery", reason="场景无法装配，未执行物理验证")
                return
            physical = self.stage("physics", {**sources, "scene": (scene, "scene")})
            self.record(physical, "physics", self.candidate(built=s["built"]), "physics.json")
            s["physics_status"] = physical["status"]
            if physical["status"] != "physics_passed":
                self.phase("physical_recovery", reason="物理协议未通过；外观通过不代表可用")
                return
            finals += [scene, physical]
        if self.options["background"] != "off":
            background = self.stage("background", {"perception": (s["perception"], "perception")})
            finals.append(background)
            if self.options["background"] == "reconstruct":
                cameras = self.stage("cameras", {"background": (background, "background")})
                gaussian = self.stage("gaussian", {"cameras": (cameras, "cameras")})
                overview = self.stage("overview", {"gaussian": (gaussian, "gaussian"), "cameras": (cameras, "cameras")})
                finals += [cameras, gaussian, overview]
        self.phase("completed", final_stages=finals, reason=None)

    def summary(self, output):
        s = self.state
        objects = [{**o, "views": [
                       {**{k: v[k] for k in ("ref", "source_frame", "seconds")},
                        "ticket": f'e{len(s["evidence_origins"])}:{v["ref"]}' if s.get("revisit") else None}
                       for v in o["views"]]}
                   for o in s["objects"][:32]]
        previews = []
        def preview(name, pairs):
            if not pairs:
                return
            images = []
            for path, label in pairs:
                with Image.open(path) as raw:
                    image = raw.copy()
                images.append((image, label))
            evidence.contact_sheet(images, output / name)
            previews.append(name)

        if s.get("video_overview") and s["phase"] in {"sampling", "recovery", "physical_recovery", "target", "structure"}:
            name = "source-video.jpg"
            shutil.copyfile(self.ref(s["video_overview"], "video_overview").parent / "source-overview.jpg", output / name)
            previews.append(name)
        if s.get("evidence") and s["objects"]:
            base = self.ref(s["evidence"], "evidence")
            pairs = []
            for obj in ([s["target"]] if s.get("target") else s["objects"][:8]):
                for v in obj["views"][:(16 if s.get("target") else 1)]:
                    label = f'{obj["asset_id"]} ' + (f'{v["seconds"]:.2f}s' if v["seconds"] is not None else v["ref"])
                    pairs.append((relative_file(base, v["context"]), label))
            preview("object-evidence.jpg", pairs)
        if s.get("built"):
            built = self.ref(s["built"], "built")
            candidate = read_json(built / "candidate.json")
            pairs = [(relative_file(built, v), "generated candidate - diagnostic")
                     for v in candidate.get("visual_previews", [])[:4]]
            preview("candidate.jpg", pairs)
        for row in s.get("final_stages", []):
            if row["operation"] == "overview":
                preview("background-view.jpg", [(self.ref(row, "overview") / "overview.png", "3DGS calibrated view")])
        bp = None
        blueprint_identity = None
        if s.get("blueprint"):
            path = self.ref(s["blueprint"], "blueprint")
            source = read_json(path)
            blueprint_identity = {"sha256": sha256(path), "role": "canonical_description"}
            bp = {k: source.get(k) for k in ("kind", "coordinate_units", "target_dimensions_m")}
            bp.update({k: source.get(k, [])[:16] for k in (
                "part_definitions", "part_instances", "interfaces", "joints", "mates", "geometric_constraints", "hypotheses", "unresolved",
            )})
        construction = (
            read_json(self.ref(s["build_blueprint"], "build_hypothesis"))
            if s.get("build_blueprint") else None
        )
        retained = []
        if s.get("revisit") and s["evidence_origins"]:
            retained = [
                {k: row[k] for k in ("ticket", "source_frame", "seconds", "object_id")}
                for row in evidence.pool(self.origins(s["evidence_origins"]))
            ]
        history = []
        for row in s["history"][-8:]:
            item = {k: row[k] for k in ("revision", "round", "phase", "action", "reason", "object_id", "constraint_ids", "actor") if k in row}
            if row.get("hypothesis"):
                item["hypothesis"] = {
                    "source_blueprint_sha256": row["hypothesis"]["source_blueprint_sha256"],
                    "parameter_count": len(row["hypothesis"]["parameters"]),
                }
            history.append(item)
        result = {
            "schema": s["schema"], "phase": s["phase"], "revision": s["revision"], "round": s["round"],
            "reason": s.get("reason"), "allowed_actions": self.actions(), "active_stage": s.get("active_stage"),
            "task": self.task, "options": self.options, "stable_target": s["stable_target"],
            "selected_object_id": s["target"]["object_id"] if s.get("target") else None,
            "latest_track_id": s.get("latest_track_id"), "retained_evidence": retained,
            "video": read_json(self.ref(s["video_overview"], "video_overview")) if s.get("video_overview") else None,
            "sampling": s.get("sampling"), "objects": objects, "objects_total": len(s["objects"]),
            "blueprint": bp, "critic": s["critic"][-8:], "history": history, "rounds": s["rounds"][-4:],
            "blueprint_identity": blueprint_identity, "construction_hypothesis": construction,
            "construction_readiness": analyze_readiness(parse_blueprint(
                read_json(self.ref(self.active_blueprint(), "blueprint")))) if bp else None,
            "backend_requirements": hypothesis.requirements(parse_blueprint(
                read_json(self.ref(self.active_blueprint(), "blueprint")))) if bp else [],
            "previews": previews, "physics_status": s["physics_status"],
            "deliverables": self.candidate(assets=s["visual"], built=s["built"]) if s["phase"] == "completed" else {},
            "limits": [
                "单个目标；2D 视频预览不是视角覆盖度或尺寸测量。",
                "候选渲染只用于诊断。视觉通过不构成网格、接触或真实物理验证。",
            ],
        }
        if s["phase"] == "completed":
            result["deliverables"].update(self.candidate(**{r["operation"]: r for r in s["final_stages"]}))
            if s.get("build_blueprint"):
                result["deliverables"].update(self.candidate(build_hypothesis=s["build_blueprint"]))
        return result

    def run(self, output, decision=None, *, actor=None):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.used = {}
            self.load()
            if decision is not None:
                self.decide(decision, actor)
            self.advance()
            output.mkdir(parents=True, exist_ok=True)
            result = self.summary(output)
            atomic_json(output / "workflow.json", result)
            return result
