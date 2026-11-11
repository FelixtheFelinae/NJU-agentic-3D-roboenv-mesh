"""Independent checks over frozen recipe and measured traces; no state-label acceptance."""

import argparse
import hashlib
import json
import math
from pathlib import Path


def audit_spec(spec):
    ids = [p["id"] for p in spec["parts"]]
    checks = {
        "unique_part_ids": len(ids) == len(set(ids)),
        "four_wheels": len([p for p in spec["parts"] if p["body"] == p["id"]]) == 4,
        "all_dimensions_declared_assumed": all(
            p["dimension_source"] == "assumed_metric_scale" for p in spec["parts"]
        ),
    }
    checks["connection_references_exist"] = all(
        all(c[key] in ids for key in ("fastener", "mate") if key in c)
        and all(x in ids for x in c.get("clamped_parts", []))
        for c in spec["connections"]
    )
    checks["finite_geometry"] = all(math.isfinite(v) for p in spec["parts"] for v in p["center"])
    target = next(p for p in spec["parts"] if p["id"] == "bumper_screw_right")
    # Check shank swept cylinder at initial seat against every static box.
    c = target["center"]
    collisions = []
    for part in spec["parts"]:
        if part["id"] == target["id"]:
            continue
        for i, s in enumerate(part["solids"]):
            if s["shape"] != "box":
                continue
            lo = [part["center"][j] + s["center"][j] - s["size"][j] / 2 for j in range(3)]
            hi = [lo[j] + s["size"][j] for j in range(3)]
            distance = sum(max(lo[j] - c[j], 0, c[j] - hi[j]) ** 2 for j in range(2))
            if distance < 0.0019**2 and lo[2] < c[2] - 0.0014 and hi[2] > c[2] - 0.0094:
                collisions.append(part["id"] + f":{i}")
    checks["initial_screw_shank_clearance"] = not collisions
    return {"checks": checks, "initial_shank_collision_ids": collisions}


def audit_run(spec_path, physics_dir, connection_dir):
    spec = json.loads(spec_path.read_text())
    result = audit_spec(spec)
    checks = result["checks"]
    traces = json.loads((physics_dir / "traces.json").read_text())
    pr = json.loads((physics_dir / "report.json").read_text())
    baseline = [r for r in traces if 1 <= r["t_s"] <= 1.5]
    driven = [r for r in traces if 1.5 <= r["t_s"] <= 2.5]
    checks["baseline_vertical_stability"] = (
        max(r["root"][2] for r in baseline) - min(r["root"][2] for r in baseline) < 0.003
    )
    checks["baseline_speed"] = (
        max(math.sqrt(sum(v * v for v in r["root"][7:10])) for r in baseline) < 0.03
    )
    checks["external_push_response"] = driven[-1]["root"][1] - baseline[-1]["root"][1] > 0.02
    checks["wheel_rotation_response"] = (
        max(abs(a - b) for a, b in zip(driven[-1]["joints"], baseline[-1]["joints"])) > 0.1
    )
    checks["no_ground_penetration"] = all(
        pos[2] > 0.048 for r in driven for pos in r["body_positions"][1:]
    )
    checks["source_scene_hash"] = (
        hashlib.sha256((physics_dir / "cart_scene.usda").read_bytes()).hexdigest()
        == pr["scene_sha256"]
    )
    cr = json.loads((connection_dir / "report.json").read_text())
    rows = json.loads((connection_dir / "traces.json").read_text())
    checks["connection_spec_binding"] = (
        cr["source_spec_sha256"] == hashlib.sha256(spec_path.read_bytes()).hexdigest()
    )
    checks["initial_target_preserved"] = (
        math.dist(
            cr["initial_position_m"],
            next(p["center"] for p in spec["parts"] if p["id"] == "bumper_screw_right"),
        )
        < 0.0002
    )
    angle = 0.0
    previous = 0.0
    residual = []
    release = None
    for r in rows:
        w, x, y, z = r["quaternion_wxyz"]
        a = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        angle += math.atan2(math.sin(a - previous), math.cos(a - previous))
        previous = a
        if r["thread_active"]:
            residual.append(abs(r["relative_position_m"][2] - 0.0008 * angle / (2 * math.pi)))
        elif release is None:
            release = (angle, r["relative_position_m"][2])
    checks["independent_screw_coupling"] = max(residual) < 0.0001
    checks["measured_release"] = (
        release is not None and release[0] > 10 * math.pi - 0.001 and release[1] >= 0.004
    )
    checks["measured_separation"] = rows[-1]["relative_position_m"][2] > 0.025
    checks["finite_trajectories"] = all(
        math.isfinite(x) for r in traces for x in r["root"] + r["joints"]
    )
    result.update(
        passed=all(checks.values()),
        scope="reconstruction hypothesis + rolling proxies + one fixed-car screw; not whole bumper release",
        report_hashes={
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [physics_dir / "traces.json", connection_dir / "traces.json"]
        },
    )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--physics", type=Path, required=True)
    p.add_argument("--connection", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    r = audit_run(a.spec, a.physics, a.connection)
    a.output.write_text(json.dumps(r, indent=2) + "\n")
    print(json.dumps(r, indent=2))
    raise SystemExit(0 if r["passed"] else 1)
