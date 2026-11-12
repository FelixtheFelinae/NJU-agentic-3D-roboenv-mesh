"""Small video overview and stable source-frame identities for workflow decisions."""

import hashlib
from fractions import Fraction

from PIL import Image, ImageDraw
from pydantic import Field

from ..contracts import VideoConfig
from ..io import read_json, relative_file, sha256, write_json
from ..video_io import require_av


class Sampling(VideoConfig):
    # The decision cannot change preview encoding.
    sample_fps: float = Field(gt=0, le=30, allow_inf_nan=False)
    max_frames: int = Field(ge=1, le=64)
    start_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reference_frame: int = Field(default=0, ge=0, lt=64)

    @classmethod
    def decision(cls, value, duration):
        allowed = {"sample_fps", "max_frames", "start_seconds", "end_seconds", "reference_frame"}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("采样仅允许时间窗、采样率、帧数与参考帧")
        model = cls.model_validate(value)
        if model.reference_frame >= model.max_frames:
            raise ValueError("参考帧必须在预算内")
        if duration is not None and model.start_seconds >= duration:
            raise ValueError("采样起点超出视频")
        return model.model_dump(include=allowed)


def contact_sheet(images, output, *, columns=4):
    """Small, bounded JPEG for the UI and native Agent. Labels describe provenance."""
    if not images:
        return
    w, h = 240, 160
    cols = min(columns, len(images))
    board = Image.new("RGB", (cols * w, ((len(images) + cols - 1) // cols) * h), "#171b20")
    draw = ImageDraw.Draw(board)
    for index, (image, label) in enumerate(images):
        x, y = index % cols * w, index // cols * h
        image.thumbnail((w - 8, h - 25))
        board.paste(image.convert("RGB"), (x + (w - image.width) // 2, y + 3))
        draw.text((x + 6, y + h - 18), label, fill="white")
    output.parent.mkdir(parents=True, exist_ok=True)
    board.save(output, quality=82)


def overview(source, output):
    av = require_av()
    images, times = [], []
    with av.open(str(source)) as container:
        if not container.streams.video:
            raise ValueError("input has no video stream")
        stream = container.streams.video[0]
        # Match decode_video's time origin even when the container has no start_time.
        first = next(container.decode(stream), None)
        if first is None or first.pts is None or first.time_base is None:
            raise ValueError("视频没有可用的时间戳")
        base = stream.time_base or first.time_base
        origin = stream.start_time * base if stream.start_time is not None else first.pts * first.time_base
        duration = (float(stream.duration * base) if stream.duration else
                    float(container.duration / av.time_base) if container.duration else None)
        offsets = [duration * i / 8 for i in range(8)] if duration else [0]
        for offset in offsets:
            target = origin + offset
            container.seek(int(target / base), stream=stream, backward=True)
            # A long GOP may put the target outside the bounded preview scan.
            for i, frame in enumerate(container.decode(stream)):
                if i >= 150:
                    break
                if frame.pts is None or frame.time_base is None:
                    continue
                timestamp = float(frame.pts * frame.time_base - origin)
                if timestamp + 1e-7 < offset:
                    continue
                if times and timestamp <= times[-1]:
                    break
                image = frame.to_image()
                image.thumbnail((232, 135))
                times.append(timestamp)
                images.append((image, f"source {timestamp:.2f}s"))
                break
    if not times:
        raise ValueError("无法为视频生成有时间戳的预览")
    contact_sheet(images, output / "source-overview.jpg")
    write_json(output / "overview.json", {
        "duration_seconds": duration, "preview_seconds": times, "source_sha256": sha256(source),
        "scope": "原视频时间预览；未测量相机视角或物体覆盖率",
    })


def observations(evidence, perception, is_video):
    index = read_json(evidence / "objects.json")
    timeline = read_json(perception / "inputs/video.json") if is_video else None
    frames = {row["frame_id"]: row for row in timeline["frames"]} if timeline else {}
    objects = []
    for row in index["objects"]:
        data = read_json(relative_file(evidence, row["evidence_ref"]))
        views = []
        for view in data["views"]:
            frame = frames.get(view["frame_id"])
            if is_video and frame is None:
                raise ValueError("对象参考图没有对应的原始视频帧")
            source = view.get("source_observation") if index.get("schema_version") == "envcreator.workflow-evidence/1" else None
            identity = source["source_frame"] if source else (f'{timeline["source_sha256"]}:{Fraction(frame["source_time_base"]) * frame["source_pts"]}'
                        if frame else sha256(relative_file(evidence, view["frame_ref"])))
            views.append({
                "ref": view["ref_id"], "source_frame": identity,
                "seconds": source["timestamp_seconds"] if source else frame["timestamp_seconds"] if frame else None,
                "context": view["context_ref"], "mask": view["mask_ref"],
            })
        objects.append({
            "asset_id": row["asset_id"], "object_id": row["object_id"], "status": row["status"],
            "category": data.get("category"), "views": views,
        })
    return objects


MAX_FUSED_VIEWS = 16
FUSION_FORMAT = 2


def pool(origins):
    """Stable tickets point to real raw observations, never candidate renders."""
    result = []
    for number, origin in enumerate(origins):
        matches = [o for o in observations(origin["evidence"], origin["perception"], origin["video"])
                   if o["object_id"] == origin["object_id"]]
        if len(matches) != 1 or matches[0]["status"] != "ready_for_design":
            raise ValueError("融合来源的对象不存在或证据不足")
        for view in matches[0]["views"]:
            result.append({**view, "ticket": f'e{number}:{view["ref"]}',
                           "origin": number, "object_id": origin["object_id"]})
    return result


def selection(candidates, requested=None, *, previous_frames=()):
    by_ticket = {row["ticket"]: row for row in candidates}
    if requested is None:
        unique = {}
        for row in candidates:
            unique.setdefault(row["source_frame"], row)
        selected = list(unique.values())
        if len(selected) > MAX_FUSED_VIEWS:
            raise ValueError("真实观察超过 16 张，请用 evidence_refs 选择本轮所需画面")
    else:
        if not requested or len(requested) > MAX_FUSED_VIEWS or len(set(requested)) != len(requested):
            raise ValueError("evidence_refs 须为 1 到 16 个不重复的观察编号")
        if not set(requested) <= set(by_ticket):
            raise ValueError("evidence_refs 包含本轮不存在的真实观察")
        selected = [by_ticket[ticket] for ticket in requested]
        if len({row["source_frame"] for row in selected}) != len(selected):
            raise ValueError("不能把同一个原始帧重复选作新证据")
    if previous_frames and not {row["source_frame"] for row in selected} - set(previous_frames):
        raise ValueError("选中的建模证据没有增加新的原始帧")
    return selected


def fuse(origins, selected, output, *, stable_target, identity_reason, video_sha256=None):
    """Copy a bounded packet; the original sources and their identities stay immutable."""
    from ..construction.common import copy_ref, new_run, source_identity, verify_run

    candidates = pool(origins)
    selected = selection(candidates, selected)
    if not identity_reason.strip():
        raise ValueError("跨轮融合必须记录对象身份判断")
    for origin in origins:
        verify_run(origin["evidence"], "objects.json")
        verify_run(origin["perception"], "scene.json")
        if video_sha256:
            if not origin["video"] or read_json(origin["perception"] / "inputs/video.json")["source_sha256"] != video_sha256:
                raise ValueError("不能融合其他视频或生成画面")
    by_origin = []
    for origin in origins:
        index = read_json(origin["evidence"] / "objects.json")
        row = next(row for row in index["objects"] if row["object_id"] == origin["object_id"])
        by_origin.append((row, read_json(relative_file(origin["evidence"], row["evidence_ref"]))))
    first, original = by_origin[0]
    with new_run(output, "workflow_evidence_fusion", {
        str(i): {"evidence": source_identity(o["evidence"]), "perception": source_identity(o["perception"])}
        for i, o in enumerate(origins)
    }):
        record = {**original, "object_id": stable_target, "views": [],
                  "selection": "explicit cross-round identity; deduplicated real source frames",
                  "board_ref": "references.jpg", "parent_object_ids": [],
                  "fusion": {"identity_reason": identity_reason, "track_bindings": [
                      {"object_id": o["object_id"], "round": o["round"]} for o in origins
                  ]}}
        boards = []
        for item in selected:
            index = item["origin"]
            origin = origins[index]
            view = next(v for v in by_origin[index][1]["views"] if v["ref_id"] == item["ref"])
            frame_file = relative_file(origin["evidence"], view["frame_ref"])
            if origin["video"]:
                timeline = read_json(origin["perception"] / "inputs/video.json")
                frame = next(f for f in timeline["frames"] if f["frame_id"] == view["frame_id"])
                if sha256(frame_file) != frame["image_sha256"]:
                    raise ValueError("对象图像与原始视频帧哈希不符")
            ref_id = "view-" + hashlib.sha256(item["source_frame"].encode()).hexdigest()[:16]
            copied = {**view, "ref_id": ref_id, "frame_id": "source-" + ref_id.removeprefix("view-"),
                      "source_observation": {
                "source_frame": item["source_frame"], "origin_round": origin["round"],
                "object_id": origin["object_id"], "original_ref": view["ref_id"],
                "original_frame_id": view["frame_id"],
                "timestamp_seconds": item["seconds"], "role": "real_observation",
            }}
            for key in ("frame_ref", "mask_ref", "rgba_ref", "context_ref"):
                if view.get(key):
                    copied[key] = copy_ref(origin["evidence"], view[key], output,
                                           f'views/{ref_id}/{key.removesuffix("_ref")}.png')
            with Image.open(relative_file(output, copied["context_ref"])) as image:
                boards.append((image.copy(), f'round {origin["round"]} {item["seconds"] if item["seconds"] is not None else view["ref_id"]}'))
            record["views"].append(copied)
        record["observation_count"] = len(record["views"])
        contact_sheet(boards, output / "references.jpg")
        write_json(output / "evidence.json", record)
        write_json(output / "objects.json", {
            "schema_version": "envcreator.workflow-evidence/1",
            "format_revision": FUSION_FORMAT,
            "scene_id": read_json(origins[0]["perception"] / "scene.json").get("scene_id", "workflow"),
            "objects": [{**first, "object_id": stable_target, "evidence_ref": "evidence.json"}],
            "scope": "selected real observations only; no inferred or generated pixels",
        })
        write_json(output / "fusion.json", {
            "status": "ready", "identity_reason": identity_reason, "stable_target": stable_target,
            "selected": [{k: row[k] for k in ("ticket", "source_frame", "seconds", "object_id")} for row in selected],
            "source_video_sha256": video_sha256, "generated_evidence_count": 0,
        })
