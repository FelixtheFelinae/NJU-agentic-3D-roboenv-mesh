"""Force-only screw surrogate; no simulator pose writes or model-generated callbacks.

Coordinates are relative to an explicitly fixed, anti-rotated mating part.
This models a declared hypothesis, not identified hardware or real thread contact.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Parameters:
    lead_m: float = 0.0008
    engagement_m: float = 0.004
    preload_release_m: float = 0.0004
    breakaway_Nm: float = 0.002
    running_Nm: float = 0.0008
    angular_damping: float = 0.0002
    coupling_stiffness: float = 2000.0
    coupling_damping: float = 5.0
    lateral_stiffness: float = 500.0
    lateral_damping: float = 2.0
    separation_m: float = 0.018

    def __post_init__(self):
        if not all(math.isfinite(v) and v > 0 for v in vars(self).values()):
            raise ValueError("all mechanics parameters must be finite and positive")
        if not self.preload_release_m < self.engagement_m < self.separation_m:
            raise ValueError("preload, disengagement and separation distances must be ordered")


class ScrewModel:
    def __init__(self, parameters):
        self.p = parameters
        self.theta = 0.0
        self.previous_angle = 0.0
        self.state = "clamped"
        self.thread_active = True

    def observe(self, quaternion_wxyz, dz):
        w, x, y, z = quaternion_wxyz
        angle = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        self.theta += math.atan2(
            math.sin(angle - self.previous_angle), math.cos(angle - self.previous_angle)
        )
        self.previous_angle = angle
        old = self.state
        h = self.p.lead_m / (2 * math.pi)
        if self.thread_active:
            if dz >= self.p.engagement_m and self.theta * h >= self.p.engagement_m - 0.00002:
                self.thread_active = False
                self.state = "thread_clear"
            elif dz >= self.p.preload_release_m:
                self.state = "loose_threaded"
            elif self.theta > 0.02:
                self.state = "loosening"
        elif dz >= self.p.separation_m:
            self.state = "removed"
        return (
            None
            if old == self.state
            else {"from": old, "to": self.state, "theta_rad": self.theta, "dz_m": dz}
        )

    def forces(
        self,
        xyz,
        velocity,
        angular_velocity,
        tool_torque,
        axial_pull,
        engaged=True,
        blocked=False,
        transverse_pull=0.0,
    ):
        p = self.p
        h = p.lead_m / (2 * math.pi)
        x, y, dz = xyz
        vx, vy, vz = velocity
        omega = angular_velocity[2]
        applied = tool_torque if engaged and not blocked else 0.0
        force = [transverse_pull, 0.0, axial_pull]
        torque = [0.0, 0.0, applied]
        residual = dz - h * self.theta
        coupling_force = 0.0
        resistance = 0.0
        seat_stop = 0.0
        if self.thread_active:
            coupling_force = -p.coupling_stiffness * residual - p.coupling_damping * (
                vz - h * omega
            )
            force[0] += -p.lateral_stiffness * x - p.lateral_damping * vx
            force[1] += -p.lateral_stiffness * y - p.lateral_damping * vy
            force[2] += coupling_force
            drive = applied - h * coupling_force
            limit = p.breakaway_Nm if self.state in {"clamped", "loosening"} else p.running_Nm
            if self.theta <= 0.002 and dz <= 0.0002 and drive < 0:
                # Declared one-way seated stop. Tightening/preload growth is out of scope.
                seat_stop = -drive
            elif abs(omega) < 0.01 and abs(drive) <= limit:
                resistance = -drive
            else:
                direction = omega if abs(omega) >= 0.01 else drive
                resistance = -p.running_Nm * math.copysign(1, direction) - p.angular_damping * omega
            torque[2] = drive + resistance + seat_stop
        return (
            force,
            torque,
            {
                "coupling_residual_m": residual,
                "coupling_force_N": coupling_force,
                "thread_reaction_torque_Nm": -h * coupling_force,
                "resistance_Nm": resistance,
                "seated_stop_Nm": seat_stop,
                "transmitted_tool_torque_Nm": applied,
                "fixture_reaction_force_N": [
                    -force[0] + transverse_pull,
                    -force[1],
                    -coupling_force,
                ],
                "fixture_reaction_torque_Nm": -torque[2] + applied,
            },
        )


def tool_access(tool_radius, corridor_half_width, approach_length, obstacles):
    """Conservative swept cylindrical tool enclosed by a box, along local +Z.

    Obstacles are axis-aligned bounds in target coordinates. This checks the
    entire declared straight approach, not robot reachability or a hex fit.
    """
    if tool_radius <= 0 or approach_length <= 0 or corridor_half_width <= 0:
        raise ValueError("tool dimensions must be positive")
    hits = []
    for obstacle in obstacles:
        lo, hi = obstacle["min"], obstacle["max"]
        if any(a >= b or not math.isfinite(a + b) for a, b in zip(lo, hi)):
            raise ValueError("invalid obstacle bounds")
        if (
            lo[0] < tool_radius
            and hi[0] > -tool_radius
            and lo[1] < tool_radius
            and hi[1] > -tool_radius
            and lo[2] < approach_length
            and hi[2] > 0
        ):
            hits.append(obstacle["id"])
    return {
        "accessible": tool_radius < corridor_half_width and not hits,
        "obstacle_ids": hits,
        "method": "continuous conservative swept AABB",
        "scope": "declared local fixture; not full real-car reachability",
    }
