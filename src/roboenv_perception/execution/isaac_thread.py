"""Isaac pre-step hook for directly authored, already assembled screw connections."""

import json
import math

import numpy as np


def _quat(gf, transform):
    x, y, z, w = map(float, transform[3:])
    return gf.Quatd(w, gf.Vec3d(x, y, z)).GetNormalized()


class IsaacThreadRuntime:
    """Bind once after starting/resetting physics; call step before each physics step.

    Uses the CPU NumPy tensor backend by default. No poses or velocities are set.
    Stage edits live in the USD session layer. Reload the stage to reassemble.
    """

    def __init__(self, stage, simulation_view=None):
        import omni.physics.tensors as tensors
        from pxr import UsdPhysics

        self.stage = stage
        self.view = simulation_view if simulation_view is not None else tensors.create_simulation_view("numpy")
        self.indices = np.array([0], dtype=np.int32)
        self.rows = []
        for prim in stage.Traverse():
            attr = prim.GetAttribute("envcreator:threadRule")
            if not attr or not attr.HasAuthoredValueOpinion():
                continue
            rule = json.loads(attr.Get())
            if rule.get("rule_ref") != "releasable_metric_thread@1":
                raise ValueError(f"unsupported screw rule at {prim.GetPath()}")
            bindings = {}
            for role in ("screw", "support", "cover", "hinge", "connector"):
                targets = prim.GetRelationship(f"envcreator:{role}").GetTargets()
                if len(targets) != 1 or not stage.GetPrimAtPath(targets[0]).IsValid():
                    raise ValueError(f"missing {role} binding at {prim.GetPath()}")
                bindings[role] = targets[0]
            views = {role: self.view.create_rigid_body_view(str(bindings[role]))
                     for role in ("screw", "support", "cover")}
            if any(view.count != 1 for view in views.values()):
                raise ValueError(f"expected one dynamic rigid body per binding at {prim.GetPath()}")
            connector = UsdPhysics.Joint(stage.GetPrimAtPath(bindings["connector"]))
            drive = UsdPhysics.DriveAPI(stage.GetPrimAtPath(bindings["hinge"]), "angular")
            if not connector or not drive or not connector.GetJointEnabledAttr().Get():
                raise ValueError(f"expected active initial screw constraints at {prim.GetPath()}")
            self.rows.append({"id": str(prim.GetPath()), "rule": rule, "views": views,
                              "connector": connector, "drive": drive, "angle": 0.0,
                              "previous": 0.0, "spring_rest_deg": 0.0, "released": False})

    def _force(self, view, force, point):
        view.apply_forces_and_torques_at_position(
            np.asarray(force, dtype=np.float32).reshape(1, 3), None,
            np.asarray(point, dtype=np.float32).reshape(1, 3), self.indices, True,
        )

    def step(self):
        """Apply equivalent clamp forces and release connectors at the measured threshold."""
        import omni.physx
        from pxr import Gf, Usd

        measurements = []
        changed = False
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            for row in self.rows:
                rule, views = row["rule"], row["views"]
                if row["released"]:
                    measurements.append({"connection": row["id"], "state": "thread_clear",
                                         "turns_at_release": row["angle"] / math.tau})
                    continue
                screw = views["screw"].get_transforms()[0].copy()
                support = views["support"].get_transforms()[0].copy()
                support_q = _quat(Gf, support)
                relative_q = support_q.GetInverse() * _quat(Gf, screw)
                angle = 2 * math.atan2(relative_q.GetImaginary()[2], relative_q.GetReal())
                row["angle"] += math.remainder(angle - row["previous"], math.tau)
                row["previous"] = angle
                axis = np.asarray(tuple(Gf.Rotation(support_q).TransformDir(Gf.Vec3d(0, 0, 1))))
                travel = float(np.dot(screw[:3] - support[:3], axis)) - rule["initial_axial_m"]
                turns = row["angle"] / math.tau
                expected = turns * rule["lead_m"]
                residual = travel - expected
                tolerance = min(5e-5, rule["lead_m"] / 10)
                if (expected >= rule["engagement_m"] and travel >= rule["engagement_m"] - tolerance
                        and abs(residual) <= tolerance):
                    row["connector"].GetJointEnabledAttr().Set(False)
                    row["drive"].GetMaxForceAttr().Set(0.0)
                    row["released"] = True
                    changed = True
                    state, preload, resistance = "thread_clear", 0.0, 0.0
                else:
                    unload = min(1.0, max(0.0, travel / rule["unload_travel_m"]))
                    preload = rule["preload_N"] * (1 - unload)
                    resistance = rule["running_torque_Nm"] + (
                        rule["breakaway_torque_Nm"] - rule["running_torque_Nm"]
                    ) * (1 - unload)
                    state = "loose_engaged" if unload >= 1 else "clamped" if unload == 0 else "unloading"
                    current_deg = math.degrees(row["angle"])
                    max_deflection = resistance / rule["drive_stiffness_Nm_per_deg"]
                    deflection = max(-max_deflection, min(max_deflection, current_deg - row["spring_rest_deg"]))
                    row["spring_rest_deg"] = current_deg - deflection
                    row["drive"].GetTargetPositionAttr().Set(row["spring_rest_deg"])
                    row["drive"].GetMaxForceAttr().Set(resistance)
                    if preload > 0:
                        # Apply the pair on the screw axis; each member gets its actual reaction.
                        self._force(views["cover"], -preload * axis, screw[:3])
                        self._force(views["support"], preload * axis, screw[:3])
                measurements.append({"connection": row["id"], "state": state, "turns": turns,
                                     "axial_travel_m": travel, "lead_residual_m": residual,
                                     "preload_N": preload, "resistance_limit_Nm": resistance})
        if changed:
            omni.physx.get_physx_simulation_interface().flush_changes()
        return measurements
