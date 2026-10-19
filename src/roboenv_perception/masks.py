import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .contracts import InstanceLabel, ProcessingConfig
from .segmentation import Detection

COLORS = [
    (235, 75, 75),
    (55, 130, 235),
    (45, 190, 105),
    (235, 175, 45),
    (170, 90, 225),
    (35, 190, 200),
]


def bbox(mask: np.ndarray) -> list[int]:
    ys, xs = np.where(mask)
    if not len(xs):
        raise ValueError("cannot derive a box from an empty mask")
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def save_mask(path, mask):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def clean_detections(raw: list[Detection], size, config: ProcessingConfig, threshold: float):
    width, height = size
    kept, issues = [], []
    uncertain = np.zeros((height, width), dtype=bool)
    for index, detection in enumerate(raw):
        detection.raw_ids = [f"r{index + 1:04d}"]
        if detection.mask.shape != (height, width):
            raise ValueError(
                f"mask shape {detection.mask.shape} does not match image {(height, width)}"
            )
        if not math.isfinite(detection.score) or not 0 <= detection.score <= 1:
            raise ValueError("segmentation score must be finite and within [0, 1]")
        detection.mask = detection.mask.astype(bool)
        if int(detection.mask.sum()) < config.min_mask_pixels or detection.score < threshold:
            uncertain |= detection.mask
            issues.append(
                {
                    "code": "discarded_detection",
                    "severity": "review",
                    "raw_id": detection.raw_ids[0],
                    "candidate_ids": detection.candidate_ids,
                }
            )
            continue
        kept.append(detection)
    merged = []
    for detection in sorted(kept, key=lambda d: -d.score):
        for previous in merged:
            union = int(np.logical_or(detection.mask, previous.mask).sum())
            iou = int(np.logical_and(detection.mask, previous.mask).sum()) / union
            if iou >= config.duplicate_iou:
                previous.candidate_ids = sorted(
                    set(previous.candidate_ids + detection.candidate_ids)
                )
                previous.raw_ids.extend(detection.raw_ids)
                previous.track_keys = sorted(set(previous.track_keys + detection.track_keys))
                issues.append(
                    {
                        "code": "duplicate_merged",
                        "severity": "info",
                        "raw_ids": detection.raw_ids,
                        "into": previous.raw_ids[0],
                    }
                )
                break
        else:
            merged.append(detection)
    for index, detection in enumerate(merged):
        detection.instance_id = f"i{index + 1:04d}"
    return merged, uncertain, issues


def make_crops(image, mask, padding):
    tight = bbox(mask)
    x0, y0, x1, y1 = tight
    context = [
        max(0, x0 - padding),
        max(0, y0 - padding),
        min(image.width, x1 + padding),
        min(image.height, y1 + padding),
    ]
    rgba = image.convert("RGBA")
    rgba.putalpha(Image.fromarray(mask.astype(np.uint8) * 255))
    return rgba.crop(tight), image.crop(context), tight, context


def draw_overlay(image, detections):
    array = np.asarray(image).copy()
    for index, detection in enumerate(detections):
        color = np.asarray(COLORS[index % len(COLORS)])
        array[detection.mask] = (array[detection.mask] * 0.55 + color * 0.45).astype(np.uint8)
    output = Image.fromarray(array)
    draw = ImageDraw.Draw(output)
    font = ImageFont.load_default(size=14)
    for index, detection in enumerate(detections):
        x0, y0, x1, y1 = bbox(detection.mask)
        color = COLORS[index % len(COLORS)]
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=color, width=2)
        # Put labels INSIDE the mask when possible, with a contrasting box.
        ys, xs = np.where(detection.mask)
        anchor = len(xs) // 2
        x, y = int(xs[anchor]), int(ys[anchor])
        text = detection.instance_id
        bounds = draw.textbbox((0, 0), text, font=font)
        tw, th = bounds[2] + 6, bounds[3] + 4
        x, y = min(x, max(0, image.width - tw)), min(y, max(0, image.height - th))
        draw.rectangle([x, y, x + tw, y + th], fill=(20, 20, 20))
        draw.text((x + 3, y + 1), text, fill=(255, 255, 255), font=font)
    return output


def route_masks(detections, labels: dict[str, InstanceLabel], size, uncertain=None):
    shape = (size[1], size[0])
    routes = {
        name: np.zeros(shape, dtype=bool) for name in ["mesh", "background", "transient", "unknown"]
    }
    issues = []
    effective = {}
    for detection in detections:
        label = labels[detection.instance_id]
        route = label.visual_route
        if label.task_role == "transient" and route != "exclude_transient":
            issues.append(
                {
                    "code": "transient_route_conflict",
                    "severity": "review",
                    "instance_id": label.instance_id,
                }
            )
            route = "unresolved"
        if route == "gs_background" and label.task_role in {"manipulation_candidate", "container"}:
            issues.append(
                {
                    "code": "interaction_route_conflict",
                    "severity": "review",
                    "instance_id": label.instance_id,
                }
            )
            route = "unresolved"
        name = {
            "mesh_candidate": "mesh",
            "gs_background": "background",
            "exclude_transient": "transient",
            "unresolved": "unknown",
        }[route]
        effective[detection.instance_id] = route
        routes[name] |= detection.mask

    # Nested parent/part masks are retained as observations. Unrelated overlapping
    # foreground instances have ambiguous visible ownership and are excluded.
    def ancestors(identifier):
        result = set()
        parent = labels[identifier].parent_instance_id
        while parent:
            if parent in result:
                raise ValueError("cyclic parent relation")
            result.add(parent)
            parent = labels[parent].parent_instance_id
        return result

    for index, first in enumerate(detections):
        if effective[first.instance_id] != "mesh_candidate":
            continue
        for second in detections[index + 1 :]:
            if effective[second.instance_id] != "mesh_candidate":
                continue
            if first.instance_id in ancestors(
                second.instance_id
            ) or second.instance_id in ancestors(first.instance_id):
                continue
            overlap = first.mask & second.mask
            if overlap.any():
                routes["unknown"] |= overlap
                issues.append(
                    {
                        "code": "instance_overlap",
                        "severity": "review",
                        "instances": [first.instance_id, second.instance_id],
                        "pixels": int(overlap.sum()),
                    }
                )
    if uncertain is not None:
        routes["unknown"] |= uncertain
    # Routing masks form a disjoint partition; unassigned pixels remain unknown.
    routes["transient"] &= ~routes["unknown"]
    routes["mesh"] &= ~(routes["unknown"] | routes["transient"])
    routes["background"] &= ~(routes["unknown"] | routes["transient"] | routes["mesh"])
    routes["unknown"] |= ~(routes["mesh"] | routes["background"] | routes["transient"])
    return routes, effective, issues


def routing_preview(image, routes):
    colors = {
        "mesh": (235, 75, 75),
        "background": (45, 170, 85),
        "transient": (60, 125, 235),
        "unknown": (125, 125, 125),
    }
    array = np.asarray(image).copy()
    for name, mask in routes.items():
        array[mask] = (array[mask] * 0.4 + np.asarray(colors[name]) * 0.6).astype(np.uint8)
    return Image.fromarray(array)
