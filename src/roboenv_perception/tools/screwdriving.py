"""Geometry-gated, compliant ideal hex engagement with reciprocal tool torque.

This is an explicit substitute for resolved bit/socket contact, not evidence that
the actual contact mechanics, robot grasp or alignment perception are solved.
"""

import math
from dataclasses import dataclass

import numpy as np


def rotate(q, vector):
    q = np.array(q, dtype=float, copy=True)
    q /= np.linalg.norm(q)
    v = np.asarray(vector, dtype=float)
    return v + 2 * np.cross(q[1:], np.cross(q[1:], v) + q[0] * v)


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


@dataclass(frozen=True)
class EngagementParameters:
    tip_af_m: float = .003
    socket_af_m: float = .0031
    socket_depth_m: float = .0025
    radial_tolerance_m: float = .00015
    angular_tolerance_rad: float = math.radians(3)
    phase_tolerance_rad: float = math.radians(3)
    min_insertion_m: float = .0005
    axial_compliance_m: float = .0005
    lateral_compliance_m: float = .0002
    stiffness_Nm_rad: float = .2
    damping_Nm_s_rad: float = .0003
    torque_limit_Nm: float = .02

    def __post_init__(self):
        if not all(math.isfinite(v) and v > 0 for v in vars(self).values()):
            raise ValueError("engagement parameters must be finite and positive")
        if self.min_insertion_m >= self.socket_depth_m:
            raise ValueError("engagement insertion must be shallower than socket")


class HexEngagement:
    def __init__(self, parameters=EngagementParameters()):
        self.p = parameters
        self.engaged = False
        self.last_relative_yaw = None
        self.twist = 0.0

    def step(self, tcp, tool_q, tool_omega, face, screw_q, screw_omega):
        p = self.p
        normal = rotate(screw_q, (0, 0, 1))
        direction = rotate(tool_q, (0, 1, 0))
        delta = np.asarray(tcp) - np.asarray(face)
        axial = float(delta @ normal)
        radial = float(np.linalg.norm(delta - axial * normal))
        tilt = math.acos(float(np.clip(-direction @ normal, -1, 1)))
        bit_x, screw_x = rotate(tool_q, (1, 0, 0)), rotate(screw_q, (1, 0, 0))
        bit_x -= normal * (bit_x @ normal)
        bit_x /= max(np.linalg.norm(bit_x), 1e-12)
        yaw = math.atan2(float(normal @ np.cross(screw_x, bit_x)), float(screw_x @ bit_x))
        phase = wrap(yaw * 6) / 6
        reasons = []
        # Fit clearance also constrains admissible lateral offset. A loose socket
        # cannot be made usable by merely increasing the position tolerance.
        clearance = (p.socket_af_m - p.tip_af_m) / 2
        if not 0 < clearance <= .0001:
            reasons.append("bit_socket_size_mismatch")
        if radial > min(p.radial_tolerance_m, max(0, clearance)) + (p.lateral_compliance_m if self.engaged else 0.):
            reasons.append("radial_misalignment")
        if tilt > p.angular_tolerance_rad:
            reasons.append("axis_misalignment")
        if not p.min_insertion_m <= -axial <= p.socket_depth_m + p.axial_compliance_m:
            reasons.append("not_inserted_or_bottomed_out")
        if not self.engaged and abs(phase) > p.phase_tolerance_rad:
            reasons.append("hex_phase_misalignment")
        previous = self.engaged
        self.engaged = not reasons
        if self.engaged:
            if not previous:
                self.twist = 0.0
            else:
                self.twist += wrap(yaw - self.last_relative_yaw)
            relative_speed = float((np.asarray(tool_omega) - np.asarray(screw_omega)) @ normal)
            torque = float(np.clip(p.stiffness_Nm_rad * self.twist + p.damping_Nm_s_rad * relative_speed,
                                   -p.torque_limit_Nm, p.torque_limit_Nm))
        else:
            self.twist = 0.0
            torque = 0.0
        self.last_relative_yaw = yaw
        wrench = normal * torque
        return {"engaged": self.engaged, "reasons": reasons, "radial_error_m": radial,
                "axis_error_rad": tilt, "insertion_m": -axial, "hex_phase_error_rad": phase,
                "twist_rad": self.twist, "torque_to_screw_world_Nm": wrench.tolist(),
                "torque_to_tool_world_Nm": (-wrench).tolist(), "transmitted_Nm": torque,
                "event": "engaged" if self.engaged and not previous else "disengaged" if previous and not self.engaged else None}


def axial_contact(contact, relative_axial_speed, dt, effective_mass_kg, *,
                  socket_depth_m=.0025, stiffness_N_m=20000., damping_N_s_m=10.,
                  capture_radius_m=.004):
    """One-sided compliant end-face/socket-bottom contact, not a force gate.

    Positive force pushes the tool away from the screw along its outward normal.
    No chamfer or resolved polygonal edge contact is claimed. Geometric passage
    needs axis, radial and phase fit; an already inserted key keeps its passage.
    Backward-Euler effective-mass compliance bounds the substep contact impulse.
    """
    if not all(math.isfinite(v) and v > 0 for v in
               (dt, effective_mass_kg, socket_depth_m, stiffness_N_m, damping_N_s_m)):
        raise ValueError('positive finite contact parameters required')
    if contact['radial_error_m'] > capture_radius_m:
        return {'normal_force_N':0., 'surface':'none', 'penetration_m':0.}
    passage = not any(r in contact['reasons'] for r in (
        'radial_misalignment','axis_misalignment','hex_phase_misalignment','bit_socket_size_mismatch'))
    surface_depth = socket_depth_m if passage else 0.
    penetration = contact['insertion_m']-surface_depth
    gain=stiffness_N_m*dt+damping_N_s_m
    # Only repulsive contact. Separated surfaces do not attract or damp motion.
    force=max(0., (stiffness_N_m*penetration-gain*relative_axial_speed)
                      / (1+gain*dt/effective_mass_kg)) if penetration>=0 else 0.
    return {'normal_force_N':force,'surface':'socket_bottom' if passage else 'head_face',
            'penetration_m':max(0.,penetration),'passage':passage,
            'model':'unilateral compliant axial surrogate; no resolved chamfer contact'}


def socket_side_contact(tcp, face, normal, relative_velocity, engagement, dt, mass_kg,
                        clearance_m=.00005, stiffness_N_m=50000., damping_N_s_m=10.):
    """Compliant side-wall reaction for a key already admitted into its socket.

    The compliance interval in HexEngagement represents this finite-stiffness
    contact, not extra clearance for initial insertion. Returns force on screw.
    """
    delta=np.asarray(tcp)-np.asarray(face)
    radial=delta-np.asarray(normal)*float(delta@normal)
    distance=float(np.linalg.norm(radial))
    if not engagement['engaged'] or distance<=clearance_m:
        return np.zeros(3)
    direction=radial/distance
    speed=float(np.asarray(relative_velocity)@direction)
    gain=stiffness_N_m*dt+damping_N_s_m
    load=max(0.,(stiffness_N_m*(distance-clearance_m)+gain*speed)/(1+gain*dt/mass_kg))
    return direction*load


def socket_contact_wrench(tcp, face, tool_outward, normal, tool_velocity, screw_velocity,
                          tool_omega, screw_omega, engagement, dt, mass_kg,
                          clearance_m=.00005):
    """Two side-wall stations at mouth and inserted tip; no orientation servo.

    Forces act at their actual station, including a moment about the screw face.
    The caller transports the opposite wrench to the spindle COM. Clearance is
    geometric and support disappears on withdrawal; this is a compliant contact
    surrogate, not resolved hex-face contact or magnetic angular alignment.
    """
    force, moment = np.zeros(3), np.zeros(3)
    if not engagement['engaged']:
        return force, moment
    depth = engagement['insertion_m']
    for station in (0., depth):
        bit_point = np.asarray(tcp) + np.asarray(tool_outward)*(depth-station)
        socket_point = np.asarray(face)-np.asarray(normal)*station
        relative_v = (np.asarray(tool_velocity)+np.cross(tool_omega,bit_point-tcp)
                      -np.asarray(screw_velocity)-np.cross(screw_omega,socket_point-face))
        load = socket_side_contact(bit_point,socket_point,normal,relative_v,
                                   engagement,dt,mass_kg,clearance_m=clearance_m,
                                   stiffness_N_m=25000.,damping_N_s_m=5.)
        force += load
        moment += np.cross(socket_point-face,load)
    return force, moment


def passive_spindle_torque(speed_rad_s, dt, inertia, damping, torque_limit):
    """Backward-Euler bearing drag: dissipates energy with no active motor.

    Damping and limit are asset assumptions requiring hardware calibration.
    This returns a revolute torque; it never edits measured speed.
    """
    if not all(math.isfinite(v) and v > 0 for v in (dt,inertia,damping,torque_limit)):
        raise ValueError('positive finite passive spindle parameters required')
    return float(np.clip(-damping*speed_rad_s/(1+damping*dt/inertia),-torque_limit,torque_limit))


def motor_drive_parameters(dampings, force_limits, dof, *, enabled,
                           active_damping, passive_damping, active_limit, passive_limit):
    """Update only the tool's rotary DOF, preserving carriage/robot joint drives."""
    damping=np.array(dampings,dtype=float,copy=True)
    limits=np.array(force_limits,dtype=float,copy=True)
    if damping.ndim!=2 or damping.shape!=limits.shape or not 0<=dof<damping.shape[1]:
        raise ValueError('matching articulation drive arrays and a valid spindle DOF required')
    damping[:,dof]=active_damping if enabled else passive_damping
    limits[:,dof]=active_limit if enabled else passive_limit
    return damping,limits
