"""Human projections of actual artifacts. Never infer success from a process exit."""

import hashlib
import re
from pathlib import Path

from ..artifacts.registry import artifact_type
from ..components.catalog import CATALOG
from ..components.sources import execution_record, exports, output_root
from .store import read_json, safe_artifact

WORDS = {
    "bottle": "瓶子",
    "bottle_body": "瓶身",
    "bottle_cap": "瓶盖",
    "bottle_assembly": "整瓶",
    "laptop_computer": "笔记本电脑",
    "computer_mouse": "鼠标",
    "book": "书本",
    "bicycle": "自行车",
    "bicycle_frame": "车架",
    "front_wheel": "前轮",
    "rear_wheel": "后轮",
    "crank": "曲柄",
    "handlebar": "车把",
    "fork": "前叉",
    "saddle": "车座",
    "base_chassis": "机身底座",
    "screen_assembly": "屏幕组件",
    "lid_hinge": "屏幕铰链",
    "arbitrary_sfm_units": "未标定实际尺度",
    "bottle body": "瓶身",
    "bottle cap": "瓶盖",
    "cap": "盖子",
    "body": "主体",
    "laptop": "笔记本电脑",
    "notebook": "笔记本电脑",
    "mouse": "鼠标",
    "table": "桌子",
    "floor": "地面",
    "lid": "盖板",
    "base": "底座",
    "hinge": "铰链",
    "screw": "螺钉",
    "nut": "螺母",
    "wheel": "车轮",
    "fixed": "固定",
    "revolute": "转动",
    "prismatic": "滑动",
    "continuous": "连续转动",
    "observed": "图像观测",
    "inferred": "推断",
    "assumed": "假设",
    "measured": "用户测量",
    "estimated": "估计",
    "unknown": "未知",
    "model": "模型识别",
    "user": "用户修正",
    "agent": "Agent 调用",
    "manipulation_candidate": "交互对象",
    "container": "容器",
    "support": "支撑物",
    "obstacle": "障碍物",
    "decoration": "外观",
    "transient": "临时物体",
    "mesh_candidate": "构建物体",
    "gs_background": "保留背景",
    "exclude_transient": "排除",
    "unresolved": "待确认",
    "build": "构建",
    "exclude": "排除",
    "background": "背景",
    "rigid": "刚体",
    "articulated": "可动结构",
    "rigid_body_candidate": "刚体候选",
    "articulated_part_candidate": "活动部件候选",
    "none": "无",
    "not_run": "未验证",
    "passed": "通过",
    "failed": "未通过",
    "blocked": "暂时阻塞",
    "unsupported": "当前不支持",
    "unverified": "未验证",
}


def word(value):
    if not isinstance(value, str):
        return value
    return WORDS.get(value.casefold(), value)


def note(value, fallback="详细依据可在技术详情中查看。"):
    if not value:
        return ""
    if re.search(r"[\u4e00-\u9fff]", str(value)):
        return str(value)[:400]
    return fallback


def reference(run, path):
    if not path:
        return None
    root = Path(run["root"]).resolve()
    path = Path(path).resolve()
    try:
        ref = str(path.relative_to(root))
        safe_artifact(root, ref)
    except (ValueError, OSError):
        return None
    return {
        "run_id": run["id"],
        "id": hashlib.sha256(ref.encode()).hexdigest()[:24],
        "ref": ref,
        "name": path.name,
        "type": artifact_type(path),
    }


def lineage(store, identity):
    visited, result = set(), []

    def collect(key, depth=0):
        if key in visited or depth > 64:
            return
        visited.add(key)
        row = store.run(key)
        spec = execution_record(Path(row["root"]) / "spec.json")
        for binding in spec.get("sources", {}).values():
            if binding.get("run_id"):
                collect(binding["run_id"], depth + 1)
        if row.get("parent_id"):
            collect(row["parent_id"], depth + 1)
        result.append((row, spec))

    collect(identity)
    return result


def live_exports(run, spec, object_id=None):
    found = exports(run, object_id)
    root = output_root(run)
    operation = spec.get("operation")
    # Partial files are visible, but are never eligible as tool dependencies.
    mapping = {
        "ingest": {"images": "images"},
        "segment": {"masks": "masks"},
        "inventory": {"inventory": "inventory"},
        "label": {"perception": "perception"},
        "perceive": {"perception": "perception"},
        "perception_edit": {"perception": "perception"},
        "blueprint": {"blueprint": "blueprint/blueprint.json"},
        "build": {"built": "built"},
        "render": {"renders": "renders"},
        "gaussian": {"gaussian": "gaussian"},
        "cameras": {"cameras": "cameras"},
        "overview": {"overview": "overview"},
    }
    for role, ref in mapping.get(operation, {}).items():
        if (root / ref).exists():
            found.setdefault(role, root / ref)
    return found


def perception_view(run, root):
    scene = read_json(root / "scene.json", {}) or {}
    frames = scene.get("frames") or (
        [{**scene["frame"], "objects_ref": scene.get("objects_ref")}] if scene.get("frame") else []
    )
    results = []
    for frame in frames[:240]:
        image = reference(run, root / frame["image_ref"])
        objects = []
        for obj in read_json(root / frame["objects_ref"], []) if frame.get("objects_ref") else []:
            objects.append(
                {
                    "id": obj["instance_id"],
                    "object_id": obj["object_id"],
                    "name": word(obj.get("category", "unknown")),
                    "category": obj.get("category"),
                    "mask": reference(run, root / obj["mask_ref"]),
                    "bbox": obj.get("bbox_xyxy"),
                    "crop": reference(run, root / obj["rgba_ref"]),
                    "score": obj.get("sam_score"),
                    "role": word(obj.get("task_role")),
                    "physical_role": word(obj.get("physical_role")),
                    "route": word(obj.get("visual_route")),
                    "origin": word(obj.get("annotation_source", "model")),
                    "explanation": note(
                        obj.get("rationale"), "这是依据当前图像得到的用途判断，可在对话中纠正。"
                    ),
                    "occluded": obj.get("occluded", False),
                }
            )
        preview = (
            root / "previews/instances.png"
            if len(frames) == 1
            else root / "frames" / frame["frame_id"] / "previews/instances.png"
        )
        results.append(
            {
                "id": frame["frame_id"],
                "time": frame.get("timestamp_seconds"),
                "image": image,
                "overlay": reference(run, preview),
                "objects": objects,
            }
        )
    return {
        "run_id": run["id"],
        "frames": results,
        "kind": scene.get("kind", "image"),
        "message": "点击区域选择物体，可合并重复识别、排除背景或在对话中纠正。",
    }


def masks_view(run, root, images):
    if not images:
        return None
    image_run, image_root = images
    frames = read_json(image_root / "frames.json", {}).get("frames", [])
    result = []
    for frame in frames:
        folder = root / frame["frame_id"]
        rows = read_json(folder / "instances.json", [])
        objects = [
            {
                "id": row["instance_id"],
                "name": f"区域 {i + 1}",
                "score": row.get("score"),
                "mask": reference(run, folder / row["instance_id"] / "visible_mask.png"),
                "crop": reference(run, folder / row["instance_id"] / "rgba.png"),
                "bbox": row.get("bbox_xyxy"),
            }
            for i, row in enumerate(rows)
        ]
        result.append(
            {
                "id": frame["frame_id"],
                "image": reference(image_run, image_root / frame["image_ref"]),
                "overlay": reference(run, folder / "numbered_overlay.png"),
                "objects": objects,
            }
        )
    return {"run_id": run["id"], "frames": result, "message": "分割已生成，物体语义尚未完成。"}


def structure_view(run, path):
    blueprint = read_json(path, {}) or {}
    parts = blueprint.get("parts", [])
    if blueprint.get("schema_version") == "envcreator.assembly-blueprint/1":
        from ..construction.assembly_blueprint import AssemblyBlueprint

        parts = [p.model_dump(mode="json") for p in AssemblyBlueprint.model_validate(blueprint).parts]
    if not parts:
        return None
    return {
        "run_id": run["id"],
        "object_id": blueprint.get("object_id"),
        "kind": word(blueprint.get("kind")),
        "parts": [
            {
                "id": p["name"],
                "parent": p.get("parent"),
                "name": word(p["name"]),
                "role": note(p.get("role"), word(p["name"])),
                "hollow": p.get("hollow"),
                "dimensions": p.get("dimensions_m", {}),
                "evidence": p.get("evidence_refs", []),
            }
            for p in parts
        ],
        "joints": [
            {
                "id": j["name"],
                "parent": j["parent"],
                "child": j["child"],
                "kind": word(j["type"]),
                "type": j["type"],
                "axis": j.get("axis_child"),
                "travel": j.get("travel"),
                "origin": word(j.get("evidence_kind")),
                "evidence": j.get("evidence_refs", []),
            }
            for j in blueprint.get("joints", [])
        ],
        "couplings": blueprint.get("couplings", []),
        "interfaces": blueprint.get("interfaces", []),
        "mates": blueprint.get("mates", []),
        "constraints": blueprint.get("geometric_constraints", []),
        "states": blueprint.get("states", []),
        "selfcheck": read_json(path.parent / "selfcheck.json", {}),
        "dimensions": blueprint.get("target_dimensions_m", {}),
        "assumption_count": len(blueprint.get("assumptions", [])),
        "unresolved_count": len(blueprint.get("unresolved", [])),
        "message": "结构方案示意；实际运动和受力以验证结果为准。",
        "artifact": reference(run, path),
    }


def model_view(run, root):
    candidate = read_json(root / "candidate.json", {}) or {}
    result = read_json(root / "visual/result.json", {}) or read_json(root / "result.json", {}) or {}
    meshes = []
    folder = root / "visual" if (root / "visual/result.json").is_file() else root
    for mesh in result.get("meshes", []):
        item = reference(run, folder / mesh["file"])
        if item:
            meshes.append(
                {
                    **item,
                    "part": mesh.get("part"),
                    "solid": mesh.get("name"),
                    "color": mesh.get("rgba"),
                    "offset": mesh.get("preview_translation", [0, 0, 0]),
                    "title": word(mesh.get("part") or mesh.get("name", "模型")),
                }
            )
    renders = [reference(run, folder / ref) for ref in result.get("previews", [])]
    if not renders:
        renders = [reference(run, Path(ref)) for ref in candidate.get("visual_previews", [])]
    return {
        "run_id": run["id"],
        "meshes": meshes,
        "renders": [r for r in renders if r],
        "asset": reference(run, Path(candidate["usd"])) if candidate.get("usd") else None,
        "partial": not bool(candidate.get("usd")),
        "message": "实际中间预览；构建尚未通过，不能作为完整资产交付。"
        if not candidate.get("usd")
        else "外观模型与真实渲染；物理验证单独展示。",
    }


def background_view(rolemap):
    result = {
        "camera_status": "not_run",
        "registered_views": None,
        "scale": "未知",
        "overview": None,
        "message": "加入多视角素材后可重建背景。",
    }
    if "cameras" in rolemap:
        run, root = rolemap["cameras"]
        data = read_json(root / "cameras.json", {}) or {}
        if data.get("frames"):
            result.update(
                camera_status="completed",
                registered_views=len(data["frames"]),
                scale="未知"
                if data.get("scale_status") in {None, "unknown", "unscaled"}
                else word(data.get("scale_status")),
                message="相机重建完成。背景外观与物理资产分别处理。",
            )
    if "overview" in rolemap:
        run, root = rolemap["overview"]
        data = read_json(root / "overview.json", {}) or {}
        if data.get("image_ref"):
            result.update(
                overview=reference(run, root / data["image_ref"]),
                message=data.get("summary"),
                kind=data.get("kind"),
            )
    if result["overview"] is None and "gaussian" in rolemap:
        run, root = rolemap["gaussian"]
        data = read_json(root / "model/result.json", {}) or {}
        renders = data.get("holdout", [])
        if renders:
            result["overview"] = reference(run, root / "model" / renders[0]["render_ref"])
        previews = list((root / "model/checkpoints").glob("iteration_*/preview.png"))
        previews.sort(key=lambda p: int(p.parent.name.split("_")[-1]), reverse=True)
        if previews:
            result["overview"] = reference(run, previews[0])
        result["message"] = (
            "实际 3DGS 视角渲染；未补全未观测区域。"
            if result["overview"]
            else "背景训练中；首张实际渲染生成后显示。"
        )
        result["kind"] = "reconstructed_camera_view"
    return result


def workspace(store, task_id, identity=None, object_id=None):
    task = store.task(task_id)
    if identity is None:
        identity = task["runs"][0]["id"] if task["runs"] else None
    result = {
        "task_id": task_id,
        "run_id": identity,
        "title": task["title"],
        "inputs": task["inputs"],
        "perception": None,
        "structure": None,
        "model": None,
        "background": None,
        "validation": None,
        "objects": [],
        "decisions": [],
        "sources": {},
        "nodes": [],
        "stale": [],
        "status": "ready",
    }
    if not identity:
        return result
    run = store.run(identity)
    if run["task_id"] != task_id:
        raise ValueError("运行不属于当前任务")
    rows = lineage(store, identity)
    rolemap, metadata = {}, {}
    object_ids = {object_id} if object_id else set()
    if object_id:
        for candidate, candidate_spec in rows:
            found = live_exports(candidate, candidate_spec)
            if "evidence" in found:
                for item in read_json(found["evidence"] / "objects.json", {}).get("objects", []):
                    if object_id in {item["asset_id"], item["object_id"]}:
                        object_ids.update((item["asset_id"], item["object_id"]))
    for row, spec in rows:
        component = execution_record(output_root(row) / "component.json")
        for role, path in live_exports(row, spec, object_id).items():
            if (
                object_ids
                and spec.get("object_id")
                and spec["object_id"] not in object_ids
                and role
                in {"blueprint", "blueprint_review", "recipe", "assembly", "built", "renders"}
            ):
                continue
            rolemap[role] = (row, path)
            metadata[role] = component or {"lineage": spec.get("lineage", {})}
        operation = spec.get("operation")
        result["nodes"].append(
            {
                "id": row["id"],
                "title": CATALOG[operation][0]
                if operation in CATALOG
                else "完整重建"
                if not row["imported"]
                else "历史实验",
                "status": row["status"],
                "origin": word(spec.get("origin", "unknown")),
                "sources": {k: v["run_id"] for k, v in spec.get("sources", {}).items()},
                "created": row["created"],
            }
        )
    current_spec = execution_record(Path(run["root"]) / "spec.json")
    if current_spec.get("operation") in {"workflow_start", "workflow_advance"}:
        data = read_json(output_root(run) / "workflow.json", {}) or {}
        phase = data.get("phase")
        messages = {
            "sampling": "先查看原视频，选择能补充结构信息的画面",
            "target": "对象证据已准备，可确认本轮重建主体",
            "structure_proposal": "可根据外观与常识生成结构，内部机构可采用明确的推断",
            "structure": "结构方案已完成，待确认假设与未知项",
            "recovery": "检查发现问题，需要修订结构或回看原视频",
            "physical_recovery": "物理检查未通过，已有结果和失败记录已保留",
            "completed": "本轮完成，验证范围见检查页",
            "stopped": "已停止，保留未决问题与已有证据",
        }
        result["workflow"] = {
            **data,
            "previews": [
                {"name": name, "artifact": reference(run, output_root(run) / name)}
                for name in data.get("previews", [])
            ],
        } if data else None
        result["status"] = run["status"]
        result["message"] = (
            "固定流程正在执行，已完成的步骤会保留"
            if run["status"] in {"queued", "running"} else messages.get(phase, "本次执行已停止，可恢复已有流程")
        )
        if data.get("reason"):
            result["message"] += " · " + data["reason"]
        return result
    # Propagate stale dependencies; display old output honestly, exclude it from recommended sources.
    for _ in range(8):
        changed = False
        for role, (row, path) in rolemap.items():
            expected = metadata[role].get("lineage", {})
            if not expected:
                dependencies_by_role = {
                    "evidence": ["perception"],
                    "scope": ["evidence"],
                    "research": ["evidence", "scope"],
                    "blueprint": ["evidence", "scope"],
                    "recipe": ["blueprint", "evidence"],
                    "built": ["recipe", "blueprint"],
                    "assets": ["built", "recipe", "blueprint", "evidence"],
                    "scene": ["assets", "evidence"],
                    "physics": ["scene", "assets"],
                    "background": ["perception"],
                    "cameras": ["background"],
                    "gaussian": ["cameras"],
                    "overview": ["gaussian", "cameras"],
                    "delivery": ["scene", "assets", "physics"],
                }
                local = live_exports(row, {})
                expected = {
                    key: row["id"] for key in dependencies_by_role.get(role, []) if key in local
                }
            dependencies = {key: value for key, value in expected.items() if key != role}
            if any(
                key in result["stale"] or key in rolemap and rolemap[key][0]["id"] != value
                for key, value in dependencies.items()
                if key not in {"previous", "checkpoint"}
            ):
                if role not in result["stale"]:
                    result["stale"].append(role)
                    changed = True
        if not changed:
            break
    result["sources"] = {
        key: row["id"]
        for key, (row, _) in rolemap.items()
        if key not in result["stale"]
        and row["status"]
        not in {"running", "queued", "failed", "cancelled", "timeout", "interrupted"}
    }
    if "checkpoint" in rolemap:
        result["sources"]["checkpoint"] = rolemap["checkpoint"][0]["id"]
    if "perception" in rolemap:
        result["perception"] = perception_view(*rolemap["perception"])
    elif "masks" in rolemap:
        result["perception"] = masks_view(*rolemap["masks"], rolemap.get("images"))
    if "evidence" in rolemap:
        row, root = rolemap["evidence"]
        for item in read_json(root / "objects.json", {}).get("objects", []):
            data = read_json(root / item["evidence_ref"], {}) or {}
            result["objects"].append(
                {
                    "id": item["asset_id"],
                    "object_id": item["object_id"],
                    "name": word(data.get("category", "unknown")),
                    "status": item.get("status"),
                    "image": reference(row, root / data["board_ref"])
                    if data.get("board_ref")
                    else None,
                }
            )
    if "blueprint" in rolemap:
        result["structure"] = structure_view(*rolemap["blueprint"])
    visual = None
    if "built" in rolemap:
        visual = "built"
    if "renders" in rolemap and (
        visual is None
        or "built" in result["stale"]
        or rolemap["renders"][0]["created"] > rolemap["built"][0]["created"]
    ):
        visual = "renders"
    if visual:
        result["model"] = model_view(*rolemap[visual])
        result["model"]["stale"] = visual in result["stale"]
    result["background"] = background_view(rolemap)
    if "scope" in rolemap:
        row, root = rolemap["scope"]
        data = read_json(root / "scope_plan.json", {}) or {}
        if data and not data.get("all_resolved"):
            result["decisions"].append(
                {
                    "kind": "scope",
                    "title": "重建范围需要确认",
                    "message": "对象可能重复或存在范围冲突，请查看分割并选择要重建的主体。",
                    "run_id": row["id"],
                }
            )
        result["scope"] = [
            {"id": r["asset_id"], "action": r["action"], "label": word(r["action"])}
            for r in data.get("objects", [])
        ]
    if "assembly" in rolemap:
        row, root = rolemap["assembly"]
        data = read_json(root / "assembly_plan.json", {}) or {}
        result["connections"] = [
            {
                "id": r["id"],
                "fastener": word(r["fastener"]),
                "stack": [word(x) for x in r["stack"]],
                "mate": word(r["mate"]),
                "axis": r.get("axis"),
                "origin": "结构假设",
            }
            for r in data.get("connections", [])
        ]
    if "physics" in rolemap:
        row, root = rolemap["physics"]
        data = read_json(root / "physics.json", {}) or {}
        result["validation"] = {
            "run_id": row["id"],
            "status": word(data.get("status", "not_run")),
            "condition": "按报告中声明的理想观测与外力协议执行；不代表机器人真实抓持或真机实验。",
            "cases": [
                {
                    "name": item.get("asset_id"),
                    "status": word(item.get("status")),
                    "tests": [
                        {"name": t.get("name"), "status": word(t.get("status"))}
                        for t in item.get("tests", [])
                    ],
                }
                for item in data.get("results", [])
            ],
            "media": [
                r
                for p in sorted(root.rglob("*"))
                if p.suffix.lower() in {".mp4", ".webm", ".png"} and (r := reference(row, p))
            ][:12],
            "artifact": reference(row, root / "physics.json"),
            "stale": "physics" in result["stale"],
        }
    result["status"] = run["status"]
    spec = rows[-1][1]
    operation = spec.get("operation")
    result["message"] = (CATALOG[operation][0] if operation in CATALOG else "完整重建") + (
        "中…" if run["status"] in {"running", "queued"} else "已结束"
    )
    state = read_json(output_root(run) / "state.json", {}) or {}
    if state.get("error") or run["status"] in {"failed", "timeout", "interrupted", "cancelled"}:
        result["decisions"].append(
            {
                "kind": "failure",
                "title": "这一步未能完成",
                "message": "已有结果已保留，可修改要求后重试；具体错误见技术详情。",
                "run_id": run["id"],
            }
        )
    result["delivery"] = None
    if "delivery" in rolemap:
        row, root = rolemap["delivery"]
        result["delivery"] = {
            "run_id": row["id"],
            "stale": "delivery" in result["stale"],
            "files": [
                r
                for p in root.rglob("*")
                if p.suffix.lower() in {".usd", ".usda", ".usdc", ".json"}
                and (r := reference(row, p))
            ][:30],
        }
    return result
