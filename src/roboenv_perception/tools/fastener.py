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
            elif (dz >= self.p.preload_release_m
                  and self.theta * h >= self.p.preload_release_m - 0.00002):
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


def implicit_screw_step(model, hex_model, *, dt, xyz, velocity, angular_velocity,
                        tool_omega, target_omega, engaged, mass_kg, inertia_z,
                        rotor_inertia, motor_damping, motor_limit,
                        external_axial_N=0.0):
    """Backward-Euler force solve for axial screw, screw spin and spindle spin.

    The housing is a local velocity boundary, not a position command. Motor and
    hex torques are bounded and reciprocal; static Coulomb friction is solved
    before kinetic friction. Returned wrenches must be applied by the simulator;
    this function never writes poses, velocities or an assumed success state.
    Unmodeled fixture/robot contact can invalidate the predicted velocities.
    """
    import itertools
    import numpy as np

    if not all(math.isfinite(v) and v > 0 for v in
               (dt, mass_kg, inertia_z, rotor_inertia)):
        raise ValueError('positive timestep, mass and inertias required')
    p, hp = model.p, hex_model.p
    h = p.lead_m / math.tau
    old = np.array([velocity[2], angular_velocity[2], tool_omega], dtype=float)
    impedance = np.array([mass_kg, inertia_z, rotor_inertia]) / dt
    a, b = np.array([1., -h, 0.]), np.array([0., -1., 1.])
    residual = xyz[2] - h * model.theta
    thread_k = p.coupling_stiffness if model.thread_active else 0.
    thread_g = thread_k * dt + (p.coupling_damping if model.thread_active else 0.)
    hex_k = hp.stiffness_Nm_rad if engaged else 0.
    hex_g = hex_k * dt + (hp.damping_Nm_s_rad if engaged else 0.)
    hex_offset = hex_k * hex_model.twist
    friction_limit = (p.breakaway_Nm if model.state in {'clamped', 'loosening'}
                      else p.running_Nm) if model.thread_active else 0.
    seated = model.thread_active and model.theta <= .002 and xyz[2] <= .0002
    # Each branch is linear. Check the actual constitutive law before accepting
    # it, rather than iterating a stiff explicit spring or clipping velocities.
    friction_modes = ['stick', 'positive', 'negative'] if model.thread_active else ['free']
    for friction_mode, motor_mode, hex_mode in itertools.product(
            friction_modes, (0, 1, -1), (0, 1, -1) if engaged else (0,)):
        matrix = np.diag(impedance) + thread_g * np.outer(a, a)
        rhs = impedance * old - thread_k * residual * a
        rhs[0] += external_axial_N
        if model.thread_active:
            matrix[1, 1] += p.angular_damping
        if hex_mode == 0:
            matrix += hex_g * np.outer(b, b)
            rhs -= hex_offset * b
        else:
            rhs -= hex_mode * hp.torque_limit_Nm * b
        if motor_mode == 0:
            matrix[2, 2] += motor_damping
            rhs[2] += motor_damping * target_omega
        else:
            rhs[2] += motor_mode * motor_limit
        friction = 0.
        if friction_mode == 'stick':
            matrix[1] = [0, 1, 0]
            rhs[1] = 0.
        elif friction_mode in {'positive', 'negative'}:
            friction = -friction_limit * (1 if friction_mode == 'positive' else -1)
            rhs[1] += friction
        v = np.linalg.solve(matrix, rhs)
        raw_hex = hex_offset + hex_g * float(b @ v)
        tau = max(-hp.torque_limit_Nm, min(hp.torque_limit_Nm, raw_hex))
        if not engaged:
            tau = 0.
        raw_motor = motor_damping * (target_omega - v[2])
        motor = max(-motor_limit, min(motor_limit, raw_motor))
        branch_hex = raw_hex if hex_mode == 0 else hex_mode * hp.torque_limit_Nm
        branch_motor = raw_motor if motor_mode == 0 else motor_mode * motor_limit
        if abs(tau - branch_hex) > 1e-8 or abs(motor - branch_motor) > 1e-8:
            continue
        coupling = -thread_k * residual - thread_g * float(a @ v)
        if friction_mode == 'stick':
            friction = impedance[1] * (v[1] - old[1]) - tau + h * coupling
            # An explicit ideal seated stop can resist further tightening.
            if abs(friction) > friction_limit + 1e-8 and not (seated and friction > 0):
                continue
        elif friction_mode == 'positive' and v[1] < -1e-9:
            continue
        elif friction_mode == 'negative' and (v[1] > 1e-9 or seated):
            continue
        force = np.array([0., 0., coupling])
        if model.thread_active:
            lateral_g = p.lateral_stiffness * dt + p.lateral_damping
            force[:2] = (-p.lateral_stiffness * np.array(xyz[:2])
                         - lateral_g * np.array(velocity[:2])) / (1 + lateral_g * dt / mass_kg)
        net_torque = tau - h * coupling + friction
        if model.thread_active:
            net_torque -= p.angular_damping * v[1]
        metrics = {'coupling_residual_m': residual, 'coupling_force_N': coupling,
                   'thread_reaction_torque_Nm': -h * coupling,
                   'resistance_Nm': friction - (p.angular_damping * v[1] if model.thread_active else 0),
                   'transmitted_tool_torque_Nm': tau, 'motor_torque_Nm': motor,
                   'solver': 'backward-Euler coupled force solve', 'friction_mode': friction_mode,
                   'predicted_axial_speed_m_s': float(v[0]),
                   'predicted_screw_speed_rad_s': float(v[1]),
                   'predicted_spindle_speed_rad_s': float(v[2]),
                   'fixture_reaction_force_N': (-force).tolist(),
                   'fixture_reaction_torque_Nm': -net_torque + tau}
        return force, np.array([0., 0., net_torque]), metrics
    raise RuntimeError('No consistent bounded motor/hex/friction solution')
