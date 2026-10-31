"""Independent trajectory checks. Does not accept the runtime state label as proof."""

import argparse
import hashlib
import json
import math
from pathlib import Path


def validate(directory):
    runtime = json.loads((directory / "runtime.json").read_text())
    traces = json.loads((directory / "traces.json").read_text())
    p = runtime["parameters"]
    checks = {}
    snapshot = directory / "semantics_snapshot.json"
    checks["semantics_bound"] = (
        snapshot.exists()
        and hashlib.sha256(snapshot.read_bytes()).hexdigest() == runtime["semantics_sha256"]
    )
    for filename, key in [
        ("isaac_worker.py", "worker_sha256"),
        ("mechanics.py", "mechanics_sha256"),
    ]:
        source = directory / filename
        checks[key + "_bound"] = (
            source.exists() and hashlib.sha256(source.read_bytes()).hexdigest() == runtime[key]
        )
    if snapshot.exists():
        checks["frozen_parameters"] = json.loads(snapshot.read_text())["mechanics"] == p
    checks["required_cases"] = set(traces) == {
        "zero",
        "unengaged",
        "low_torque",
        "wrong_direction",
        "pull_before",
        "normal",
        "tool_blocked",
        "post_release_lateral",
    }
    checks["source_unchanged"] = (
        runtime["source_unchanged"]
        and hashlib.sha256((directory / "initial_scene.usda").read_bytes()).hexdigest()
        == runtime["scene_sha256"]
    )
    checks["no_runtime_pose_writes_reported"] = runtime["runtime_pose_writes"] == 0
    metrics = {}
    for name, rows in traces.items():
        positions = [r["relative_position_m"] for r in rows]
        turns = []
        angle = 0.0
        previous = 0.0
        finite = True
        for row in rows:
            q = row["quaternion_wxyz"]
            w, x, y, z = q
            yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
            angle += math.atan2(math.sin(yaw - previous), math.cos(yaw - previous))
            previous = yaw
            turns.append(angle)
            finite &= all(
                math.isfinite(v)
                for v in [
                    *q,
                    *row["position_m"],
                    *row["velocity_m_s"],
                    *row["force_N"],
                    *row["torque_Nm"],
                ]
            )
        active = [(r, a) for r, a in zip(rows, turns) if r["thread_active"]]
        residual = max(
            abs(r["relative_position_m"][2] - p["lead_m"] * a / (2 * math.pi)) for r, a in active
        )
        release_indices = [i for i, r in enumerate(rows) if not r["thread_active"]]
        checks[name + "_finite"] = finite
        checks[name + "_measured_angle"] = (
            max(abs(a - r["theta_rad"]) for a, r in zip(turns, rows)) < 1e-5
        )
        checks[name + "_coupling_residual"] = residual < 0.0001
        checks[name + "_thread_axis_stable"] = all(
            math.hypot(*r["relative_position_m"][:2]) < 0.0002
            and 2 * (r["quaternion_wxyz"][1] ** 2 + r["quaternion_wxyz"][2] ** 2) < 0.001
            for r, a in active
        )
        checks[name + "_angle_sampling"] = (
            max(abs(r["angular_velocity_rad_s"][2]) * runtime["dt_s"] for r in rows) < math.pi / 2
        )
        metrics[name] = {
            "max_coupling_residual_m": residual,
            "final_dz_m": positions[-1][2],
            "final_theta_rad": turns[-1],
            "release_t_s": None,
        }
        if name in {"zero", "unengaged", "low_torque", "wrong_direction", "tool_blocked"}:
            checks[name + "_retained"] = (
                not release_indices and max(v[2] for v in positions) < 0.0001
            )
        if name in {"zero", "unengaged", "low_torque", "tool_blocked"}:
            checks[name + "_stationary"] = max(abs(a) for a in turns) < 0.02
        if name == "pull_before":
            checks[name + "_retained_under_pull"] = (
                not release_indices and max(v[2] for v in positions) < 0.0001
            )
            checks[name + "_backdrive_below_holding_threshold"] = (
                0.08 * p["lead_m"] / (2 * math.pi) < p["breakaway_Nm"]
            )
        if name in {"normal", "post_release_lateral"}:
            checks[name + "_released"] = bool(release_indices)
            if release_indices:
                j = release_indices[0]
                row = rows[j]
                metrics[name]["release_t_s"] = row["t_s"]
                checks[name + "_release_measured"] = (
                    positions[j][2] >= p["engagement_m"]
                    and turns[j] * p["lead_m"] / (2 * math.pi) >= p["engagement_m"] - 0.00002
                )
                checks[name + "_no_residual_guides"] = all(
                    r["coupling_force_N"] == 0 and r["resistance_Nm"] == 0 for r in rows[j:]
                )
                checks[name + "_actual_separation"] = positions[-1][2] > p["separation_m"]
                checks[name + "_multiple_turns"] = turns[j] > 8 * math.pi
                # After release the force-only body must accelerate as F/m, not teleport.
                checks[name + "_post_release_acceleration"] = (
                    abs(
                        (rows[-1]["velocity_m_s"][2] - row["velocity_m_s"][2])
                        / (rows[-1]["t_s"] - row["t_s"])
                        - 0.08 / 0.012
                    )
                    < 0.15
                )
        if name == "post_release_lateral":
            checks["lateral_freedom_after_release"] = positions[-1][0] > 0.005
        # Head lower face must not sink through the front flange.
        checks[name + "_seat_nonpenetration"] = min(v[2] for v in positions) > -0.0002
    checks["obstacle_rejected"] = not runtime["tool_access"]["tool_blocked"]["accessible"]
    result = {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": metrics,
        "scope": "local hypothesized screw dynamics; not actual hardware identification or complete beam removal",
        "runtime_sha256": hashlib.sha256((directory / "runtime.json").read_bytes()).hexdigest(),
        "traces_sha256": hashlib.sha256((directory / "traces.json").read_bytes()).hexdigest(),
    }
    (directory / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = validate(args.directory)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
