"""Read-only release observation v1, usable beside a frozen standalone runtime.

Caller order: observer.before_step(dt); mechanics.before_step(dt);
observer.after_mechanics(); simulator.step(); observer.after_step().
This module never commands motors, forces, poses, velocities or constraint switches.
"""

import math

import numpy as np


def _rotate(q, v):
    q = np.asarray(q, dtype=float)
    q = q / np.linalg.norm(q)
    return v + 2 * np.cross(q[1:], np.cross(q[1:], v) + q[0] * v)


def _multiply(a, b):
    return np.array(
        [a[0] * b[0] - a[1:] @ b[1:], *(a[0] * b[1:] + b[0] * a[1:] + np.cross(a[1:], b[1:]))]
    )


def _pose(view):
    row = view.get_transforms()[0].cpu().numpy().copy()
    return row[:3], np.array([row[6], *row[3:6]])


class ReleaseObserver:
    schema = "envcreator.release-observation/1"

    def __init__(self, mechanics):
        self.mechanics = mechanics
        self.records = []
        self.angles = {}
        self.step = 0
        self.time_s = 0.0
        self.pending = []
        self.seen = set()

    def snapshot(self, screw):
        p, q = _pose(screw.screw)
        support_p, support_q = _pose(screw.support)
        inverse = support_q * np.array([1, -1, -1, -1])
        relative = _multiply(inverse, q)
        relative /= np.linalg.norm(relative)
        w, x, y, z = relative
        angle = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        key = screw.screw_path
        last, accumulated = self.angles.get(key, (0.0, 0.0))
        accumulated += math.atan2(math.sin(angle - last), math.cos(angle - last))
        self.angles[key] = angle, accumulated
        exit_m = float((_rotate(inverse, p - support_p) - screw.support_origin)[2])
        enabled = screw.guide_joint.GetJointEnabledAttr().Get()
        return {
            "screw_pose_xyzw": [*p.tolist(), *q[1:].tolist(), float(q[0])],
            "support_pose_xyzw": [
                *support_p.tolist(),
                *support_q[1:].tolist(),
                float(support_q[0]),
            ],
            "exit_m": exit_m,
            "relative_rotation_rad": accumulated,
            "rotation_travel_m": accumulated * screw.model.p.lead_m / math.tau,
            "connector_enabled": True if enabled is None else bool(enabled),
            "thread_active": bool(screw.model.thread_active),
        }

    def before_step(self, dt):
        self.dt = dt
        self.before = {s.screw_path: self.snapshot(s) for s in self.mechanics.screws}

    def after_mechanics(self):
        self.pending = []
        for screw in self.mechanics.screws:
            before = self.before[screw.screw_path]
            enabled = screw.guide_joint.GetJointEnabledAttr().Get()
            enabled = True if enabled is None else bool(enabled)
            if before["connector_enabled"] and not enabled and screw.screw_path not in self.seen:
                record = {
                    "schema": self.schema,
                    "screw_id": screw.screw_path,
                    "physics_step": self.step,
                    "time_before_s": self.time_s,
                    "time_after_s": self.time_s + self.dt,
                    "dt_s": self.dt,
                    "required_engagement_m": screw.model.p.engagement_m,
                    "before": before,
                    "after_topology": self.snapshot(screw),
                    "after_integration": None,
                    "observation_permissions": "privileged rigid poses and USD connector state",
                    "turn_count_source": "independently unwrapped relative quaternion yaw at each step",
                }
                self.pending.append((screw, record))
                self.seen.add(screw.screw_path)

    def after_step(self):
        samples = {s.screw_path: self.snapshot(s) for s in self.mechanics.screws}
        for screw, record in self.pending:
            record["after_integration"] = samples[screw.screw_path]
            self.records.append(record)
        self.pending = []
        self.step += 1
        self.time_s += self.dt


def evaluate_release_records(records, *, expected_ids):
    """Missing or incomplete synchronous evidence is not a physical pass."""
    issues = []
    ids = [r.get("screw_id") for r in records]
    if len(ids) != len(set(ids)):
        issues.append("duplicate release observation")
    if set(ids) != set(expected_ids):
        issues.append("release coverage differs from independently observed released screws")
    for record in records:
        pre = record.get("before", {})
        topology = record.get("after_topology", {})
        post = record.get("after_integration", {}) or {}
        required = record.get("required_engagement_m", float("inf"))
        values = [
            pre.get("exit_m", float("nan")),
            pre.get("rotation_travel_m", float("nan")),
            topology.get("exit_m", float("nan")),
            post.get("exit_m", float("nan")),
            record.get("dt_s", float("nan")),
        ]
        if not all(math.isfinite(v) for v in values) or values[-1] <= 0:
            issues.append("incomplete or nonfinite release record")
            continue
        if pre["exit_m"] < required or pre["rotation_travel_m"] < required - 0.00002:
            issues.append("connector disabled before required axial/rotation travel")
        if (
            pre.get("connector_enabled") is not True
            or topology.get("connector_enabled") is not False
            or topology.get("thread_active") is not False
            or post.get("connector_enabled") is not False
        ):
            issues.append("connector topology transition not independently confirmed")
        if pre.get("screw_pose_xyzw") != topology.get("screw_pose_xyzw"):
            issues.append("pose changed during pre-integration topology observation")
    return {
        "schema": "envcreator.release-evaluation/1",
        "status": "failed" if issues else "passed" if expected_ids else "not_exercised",
        "issues": issues,
        "release_count": len(records),
        "scope": "synchronous release conditions and one integration step; not removal or 6DoF certification",
    }
