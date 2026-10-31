"""Geometry adapters: audit the same solids used by builders, never a second hole model."""

import math

import numpy as np

from .assembly import FASTENER_NAME


def cart_parts(spec):
    parts = []
    for p in spec["parts"]:
        solids = []
        for i, raw in enumerate(p["solids"]):
            s = dict(raw, id=f"solid_{i}")
            if "axis" in s:
                s["axis"] = list(s["axis"])
            if s["shape"] == "rod":
                a, b = np.array(s["start"]), np.array(s["end"])
                s.update(
                    shape="cylinder",
                    center=((a + b) / 2).tolist(),
                    length=float(np.linalg.norm(b - a)),
                    axis=((b - a) / np.linalg.norm(b - a)).tolist(),
                )
            s["center"] = [a + b for a, b in zip(p["center"], s["center"], strict=True)]
            if s["shape"] == "torus":
                half = [
                    s["radius"] * math.sqrt(max(0, 1 - v * v)) + s["minor"]
                    for v in s.get("axis", [0, 0, 1])
                ]
                s["bounds"] = [
                    [v - r for v, r in zip(s["center"], half)],
                    [v + r for v, r in zip(s["center"], half)],
                ]
                s["shape"] = "unsupported"
            solids.append(s)
        parts.append(
            {"id": p["id"], "body": p["body"], "category": p["category"], "solids": solids}
        )
    return parts


def recipe_parts(blueprint, recipe):
    from .geometry import _preview_offsets

    offsets = _preview_offsets(blueprint, recipe)
    parts = {
        p.name: {"id": p.name, "body": p.name, "category": p.role, "solids": []}
        for p in blueprint.parts
    }
    for s in recipe.solids:
        center = np.array(s.center_m) + np.array(offsets[s.part])
        row = {"id": s.name, "shape": s.shape, "center": center.tolist()}
        if s.assembly_role in {"fastener_head", "fastener_shaft"}:
            parts[s.part]["category"] = "fastener"
        if FASTENER_NAME.search(s.part) and not any(
            word in s.part for word in ("washer", "nut", "receiver", "support", "螺母", "垫圈")
        ):
            parts[s.part]["category"] = "fastener"
        if s.shape in {"box", "cylinder", "annulus"} and s.rotation_rpy == (0, 0, 0):
            if s.shape == "box":
                row["size"] = list(s.size_m)
            else:
                row.update(radius=s.radius_m, length=s.length_m, axis=[0, 0, 1])
                if s.shape == "annulus":
                    row["inner_radius"] = s.inner_radius_m
        else:
            # Conservative bounding sphere for unsupported surfaces/orientations.
            # They can never clear a collision by being omitted from the audit.
            if s.shape == "tube":
                end = np.array(s.tube_end_m) + np.array(offsets[s.part])
                center = (center + end) / 2
                radius = math.dist(s.center_m, s.tube_end_m) / 2 + s.radius_m
            elif s.shape == "lathe":
                radius = max(math.hypot(z, r) for z, r in s.profile_m)
            elif s.shape == "torus":
                radius = s.radius_m + s.length_m / 2
            elif s.size_m:
                radius = float(np.linalg.norm(s.size_m)) / 2
            else:
                radius = math.hypot(
                    max(s.radius_m or 0, s.radius_top_m or 0), (s.length_m or 0) / 2
                )
            row.update(
                shape="unsupported", bounds=[(center - radius).tolist(), (center + radius).tolist()]
            )
        parts[s.part]["solids"].append(row)
    return list(parts.values())
