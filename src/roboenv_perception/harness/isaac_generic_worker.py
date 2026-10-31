"""Isaac Lab probe for one rigid body or passive hinge in an exported scene.

This worker reads a frozen request from ``harness.physics``.  It reloads the
scene, observes a zero-input baseline, applies an external force or torque,
and records motion directly from PhysX state.  A coupled test creates a
disconnected control in an anonymous session layer; the exported USD stays
unchanged and the temporary fixture is recorded in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import traceback
from pathlib import Path


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _hashes(paths: dict[str, str]) -> dict[str, str]:
    return {path: _digest(Path(path)) for path in paths}


def _subtree_prims(stage, root_path: str, predicate):
    """Include a prim and its descendants, but never a similar-name sibling."""
    prefix = root_path.rstrip("/") + "/"
    return [prim for prim in stage.Traverse()
            if (str(prim.GetPath()) == root_path or str(prim.GetPath()).startswith(prefix))
            and predicate(prim)]


def _unique_subtree_prim(stage, root_path: str, predicate, role: str):
    matches = _subtree_prims(stage, root_path, predicate)
    if len(matches) != 1:
        raise RuntimeError(f"{role} under {root_path}: expected exactly one, found {len(matches)}")
    return matches[0]


def _named_joint(stage, root_path: str, name: str, joint_type):
    names = {name, f"joint_{name}"}
    return _unique_subtree_prim(
        stage, root_path,
        lambda prim: prim.IsA(joint_type) and prim.GetName() in names,
        f"rotary joint {name}",
    )


def _schema_tokens(prim):
    """Read authored API names even when a standalone PXR lacks PhysX plugins."""
    values = set(prim.GetAppliedSchemas())
    authored = prim.GetMetadata("apiSchemas")
    if authored is not None:
        for attribute in ("explicitItems", "prependedItems", "appendedItems"):
            values.update(getattr(authored, attribute, ()))
    return values


def _index(names: list[str], requested: str, role: str) -> int:
    candidates = [requested, f"joint_{requested}"] if role == "joint" else [requested]
    matches = [names.index(name) for name in candidates if name in names]
    if len(matches) != 1:
        raise RuntimeError(f"{role} {requested} was not resolved uniquely: {names}")
    return matches[0]


def _clone_disconnected_control(stage, source_path: str, control_path: str, offset: list[float]):
    """Copy a reference prim into the USD session layer and delete mimic APIs."""
    from pxr import Gf, Sdf, Usd, UsdGeom

    if stage.GetPrimAtPath(control_path).IsValid():
        raise RuntimeError("test-only control path already exists in exported scene")
    source = Sdf.Path(source_path)
    destination = Sdf.Path(control_path)
    if stage.GetRootLayer().GetPrimAtPath(source) is None:
        raise RuntimeError("coupled target needs an authored scene prim for control cloning")
    if not destination.IsPrimPath() or not control_path.startswith("/World/"):
        raise RuntimeError("invalid disconnected control prim path")
    session = stage.GetSessionLayer()
    stage.SetEditTarget(Usd.EditTarget(session))
    parent = str(destination.GetParentPath())
    if stage.GetPrimAtPath(parent).IsValid():
        raise RuntimeError("control clone needs a new dedicated parent for its offset")
    parent_xform = UsdGeom.Xform.Define(stage, parent)
    parent_xform.AddTranslateOp().Set(Gf.Vec3d(*offset))
    if not Sdf.CopySpec(stage.GetRootLayer(), source, session, destination):
        raise RuntimeError("could not copy target into test-only session layer")
    clone = stage.GetPrimAtPath(control_path)
    if not clone.IsValid():
        raise RuntimeError("disconnected control clone did not compose")
    removed = []
    for prim in _subtree_prims(stage, control_path, lambda _: True):
        for schema in _schema_tokens(prim):
            if schema.startswith("PhysxMimicJointAPI:"):
                prim.RemoveAppliedSchema(schema)
                removed.append({"joint_path": str(prim.GetPath()), "schema": schema})
        for relationship in prim.GetRelationships():
            targets = relationship.GetTargets()
            mapped = [Sdf.Path(control_path + str(path)[len(source_path):])
                      if str(path) == source_path or str(path).startswith(source_path + "/")
                      else path for path in targets]
            if mapped != targets:
                relationship.SetTargets(mapped)
    if not removed:
        raise RuntimeError("target asset has no native PhysxMimicJointAPI to disconnect")
    return removed


def _ensure_fixed_base(stage, root_path: str, base_body_name: str):
    """Constrain the frame for a transmission comparison, preserving input USD."""
    from pxr import Gf, UsdGeom, UsdPhysics

    base = _unique_subtree_prim(
        stage, root_path,
        lambda prim: prim.GetName() == base_body_name
        and prim.HasAPI(UsdPhysics.RigidBodyAPI), "fixture base body")
    for existing in _subtree_prims(stage, root_path, lambda prim: prim.IsA(UsdPhysics.Joint)):
        joint = UsdPhysics.Joint(existing)
        if (joint.GetBody1Rel().GetTargets() == [base.GetPath()]
                and not joint.GetBody0Rel().GetTargets()):
            return None
    transform = UsdGeom.XformCache().GetLocalToWorldTransform(base)
    position = transform.ExtractTranslation()
    rotation = transform.ExtractRotation().GetQuat()
    fixture_path = root_path + "/HarnessFixedBase"
    fixture = UsdPhysics.FixedJoint.Define(stage, fixture_path)
    fixture.CreateBody1Rel().SetTargets([str(base.GetPath())])
    fixture.CreateLocalPos0Attr(Gf.Vec3f(*position))
    fixture.CreateLocalRot0Attr(Gf.Quatf(rotation.GetReal(), Gf.Vec3f(*rotation.GetImaginary())))
    fixture.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
    # PhysX fixed-base articulations identify their root at the world joint.
    # This is only the declared comparison fixture, authored in the session.
    previous_roots = _subtree_prims(
        stage, root_path, lambda prim: prim.HasAPI(UsdPhysics.ArticulationRootAPI))
    for prim in previous_roots:
        prim.RemoveAPI(UsdPhysics.ArticulationRootAPI)
    UsdPhysics.ArticulationRootAPI.Apply(fixture.GetPrim())
    return {"fixed_joint": fixture_path, "base_body": str(base.GetPath()),
            "world_anchor_m": list(position),
            "previous_articulation_roots": [str(prim.GetPath()) for prim in previous_roots],
            "fixture_articulation_root": fixture_path}


def _rigid_baseline_metrics(traces: list[dict]) -> dict[str, float]:
    """Assess every zero-input baseline sample without requiring a contact sensor."""
    samples = [item for item in traces if item["phase"] == "baseline"]
    if len(samples) < 2:
        raise RuntimeError("rigid baseline needs at least two simulated samples")
    origin = samples[0]["root_position_m"]
    initial_quaternion = samples[0]["root_quaternion_wxyz"]
    initial_norm = math.sqrt(sum(value * value for value in initial_quaternion))
    if not math.isfinite(initial_norm) or initial_norm == 0:
        raise RuntimeError("rigid baseline has an invalid initial orientation")
    z_values = []
    planar_excursions = []
    vertical_speeds = []
    orientation_changes = []
    for sample in samples:
        position = sample["root_position_m"]
        quaternion = sample["root_quaternion_wxyz"]
        speed = sample["root_velocity_m_s"][2]
        norm = math.sqrt(sum(value * value for value in quaternion))
        if (not all(math.isfinite(value) for value in [*position, *quaternion, speed])
                or not math.isfinite(norm) or norm == 0):
            raise RuntimeError("rigid baseline contains a nonfinite root state")
        cosine = abs(sum(a * b for a, b in zip(initial_quaternion, quaternion, strict=True))
                     / (initial_norm * norm))
        z_values.append(position[2])
        planar_excursions.append(math.hypot(position[0] - origin[0], position[1] - origin[1]))
        vertical_speeds.append(abs(speed))
        orientation_changes.append(2 * math.acos(min(1.0, cosine)))
    return {
        "baseline_planar_excursion_m": max(planar_excursions),
        "baseline_z_span_m": max(z_values) - min(z_values),
        "baseline_max_abs_vertical_speed_m_s": max(vertical_speeds),
        "baseline_orientation_change_rad": max(orientation_changes),
    }


def _rigid_driven_metrics(traces: list[dict]) -> dict[str, float]:
    """Audit support height and body-up direction throughout drive and release."""
    baseline = [item for item in traces if item["phase"] == "baseline"]
    driven = [item for item in traces if item["phase"] in {"drive", "release"}]
    if not baseline or not driven:
        raise RuntimeError("rigid support audit needs baseline and driven samples")

    def body_up(quaternion):
        norm = math.sqrt(sum(value * value for value in quaternion))
        if not math.isfinite(norm) or norm == 0:
            raise RuntimeError("invalid rigid orientation during support audit")
        w, x, y, z = [value / norm for value in quaternion]
        return (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y))

    reference = baseline[-1]
    reference_z = reference["root_position_m"][2]
    reference_up = body_up(reference["root_quaternion_wxyz"])
    drops, tilts = [], []
    for sample in driven:
        z = sample["root_position_m"][2]
        if not math.isfinite(z) or not math.isfinite(reference_z):
            raise RuntimeError("nonfinite rigid height during support audit")
        up = body_up(sample["root_quaternion_wxyz"])
        cosine = sum(a * b for a, b in zip(reference_up, up, strict=True))
        drops.append(max(0.0, reference_z - z))
        tilts.append(math.acos(max(-1.0, min(1.0, cosine))))
    return {"driven_max_drop_m": max(drops), "driven_max_tilt_rad": max(tilts)}


def run(request_path: Path, output: Path) -> int:
    request = json.loads(request_path.read_text())
    if request.get("format") != "roboenv-physics-request-v1":
        raise ValueError("unsupported physics request")
    protocol = request["protocol"]
    family = protocol["family"]
    if family not in {"rigid", "hinge", "rotary", "coupled"}:
        raise ValueError("unsupported physical interaction family")
    scene = Path(request["scene_usd"]).resolve()
    expected_hashes = request["source_hashes"]
    if str(scene) not in expected_hashes or _hashes(expected_hashes) != expected_hashes:
        raise ValueError("source files changed before Isaac launch")
    output.mkdir(parents=True, exist_ok=False)
    (output / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    app = None
    sim = None
    try:
        from isaaclab.app import AppLauncher

        app = AppLauncher(headless=True, device="cpu",
                          kit_args=f"--portable-root {output.parent / 'kit-runtime'}").app
        import isaaclab.sim as sim_utils
        import numpy as np
        import omni.usd
        import torch
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
        from pxr import Usd, UsdPhysics

        torch.set_num_threads(4)
        if not omni.usd.get_context().open_stage(str(scene)):
            raise RuntimeError("exported USD could not be loaded")
        stage = omni.usd.get_context().get_stage()
        target_prim = stage.GetPrimAtPath(protocol["target_prim"])
        if not target_prim.IsValid():
            raise RuntimeError("target prim is absent from exported USD")
        if family == "rigid":
            rigid_prim = _unique_subtree_prim(
                stage, protocol["target_prim"],
                lambda prim: prim.HasAPI(UsdPhysics.RigidBodyAPI), "rigid body")
            articulation_roots = _subtree_prims(
                stage, protocol["target_prim"],
                lambda prim: prim.HasAPI(UsdPhysics.ArticulationRootAPI))
            if articulation_roots:
                raise RuntimeError(
                    "rigid target retains Artisan ArticulationRootAPI; adapt the exported "
                    "asset for a rigid scene before verification")
            physical_prim_path = str(rigid_prim.GetPath())
        else:
            articulation_root = _unique_subtree_prim(
                stage, protocol["target_prim"],
                lambda prim: prim.HasAPI(UsdPhysics.ArticulationRootAPI), "articulation root")
            physical_prim_path = str(articulation_root.GetPath())
            joint_prim = _named_joint(stage, physical_prim_path, protocol["joint_name"],
                                      UsdPhysics.RevoluteJoint)
            drive_stiffness = joint_prim.GetAttribute("drive:angular:physics:stiffness").Get()
            if drive_stiffness not in {None, 0}:
                raise RuntimeError("target joint has an active angular position drive")
            control_prim_path = None
            fixture_operations = []
            if family == "coupled":
                stage.SetEditTarget(Usd.EditTarget(stage.GetSessionLayer()))
                follower_prim = _named_joint(stage, physical_prim_path,
                                             protocol["follower_joint_name"],
                                             UsdPhysics.RevoluteJoint)
                for joint in (joint_prim, follower_prim):
                    revolute = UsdPhysics.RevoluteJoint(joint)
                    lower = revolute.GetLowerLimitAttr().Get()
                    upper = revolute.GetUpperLimitAttr().Get()
                    if (lower is None or upper is None or not math.isfinite(float(lower))
                            or not math.isfinite(float(upper)) or lower >= upper):
                        raise RuntimeError(
                            "native PhysX mimic needs finite driver and follower angular limits "
                            "in the exported USD")
                mimic_schemas = [schema for schema in _schema_tokens(follower_prim)
                                 if schema.startswith("PhysxMimicJointAPI:")]
                if len(mimic_schemas) != 1:
                    raise RuntimeError("exported follower joint needs one native PhysxMimicJointAPI")
                instance = mimic_schemas[0].split(":", 1)[1]
                native_gearing = follower_prim.GetAttribute(
                    f"physxMimicJoint:{instance}:gearing").Get()
                native_reference = follower_prim.GetRelationship(
                    f"physxMimicJoint:{instance}:referenceJoint").GetTargets()
                if (native_gearing is None or
                        not math.isclose(float(native_gearing), -float(protocol["target_ratio"]),
                                         rel_tol=1e-5, abs_tol=1e-6) or
                        native_reference != [joint_prim.GetPath()]):
                    raise RuntimeError("exported native gearing or driver joint differs from frozen ratio")
                control_prim_path = protocol["disconnected_control_prim"]
                removed = _clone_disconnected_control(
                    stage, protocol["target_prim"], control_prim_path,
                    protocol["control_offset_world_m"])
                if removed:
                    fixture_operations.append({"kind": "disconnected_clone",
                                               "source_prim": protocol["target_prim"],
                                               "control_prim": control_prim_path,
                                               "removed_mimic_schemas": removed,
                                               "offset_world_m": protocol["control_offset_world_m"]})
                control_root = _unique_subtree_prim(
                    stage, control_prim_path,
                    lambda prim: prim.HasAPI(UsdPhysics.ArticulationRootAPI),
                    "control articulation root")
                control_physical_path = str(control_root.GetPath())
                control_follower = _named_joint(
                    stage, control_physical_path, protocol["follower_joint_name"],
                    UsdPhysics.RevoluteJoint)
                if any(schema.startswith("PhysxMimicJointAPI:")
                       for schema in _schema_tokens(control_follower)):
                    raise RuntimeError("disconnected control still has native mimic coupling")
                for root_path in (physical_prim_path, control_physical_path):
                    fixed = _ensure_fixed_base(stage, root_path, protocol["base_body_name"])
                    if fixed:
                        fixture_operations.append({"kind": "fixed_base", **fixed})
        dt = float(protocol["dt_s"])
        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            device="cpu", dt=dt, render_interval=240,
            physx=sim_utils.PhysxCfg(solver_type=1, enable_external_forces_every_iteration=True)))
        control = None
        if family == "rigid":
            asset = RigidObject(RigidObjectCfg(prim_path=physical_prim_path, spawn=None))
            input_vector = np.asarray(protocol["force_world_N"], dtype=float)
            input_axis = input_vector / np.linalg.norm(input_vector)
            force_tensor = torch.tensor(input_vector.reshape(1, 1, 3), dtype=torch.float32)
            zero_forces = torch.zeros_like(force_tensor)
            def measure():
                return float(np.dot(asset.data.root_state_w[0, :3].cpu().numpy(), input_axis))

            def state():
                return {
                    "root_position_m": asset.data.root_state_w[0, :3].cpu().numpy().tolist(),
                    "root_quaternion_wxyz": asset.data.root_state_w[0, 3:7].cpu().numpy().tolist(),
                    "root_velocity_m_s": asset.data.root_state_w[0, 7:10].cpu().numpy().tolist(),
                }
        else:
            asset = Articulation(ArticulationCfg(prim_path=physical_prim_path,
                                                spawn=None, actuators={}))
            control = (Articulation(ArticulationCfg(prim_path=control_physical_path,
                                                   spawn=None, actuators={}))
                       if family == "coupled" else None)
        sim.reset()
        asset.reset()
        if family != "rigid":
            try:
                joint_index = _index(asset.joint_names, protocol["joint_name"], "joint")
                moving_index = _index(asset.body_names, protocol["moving_body_name"], "body")
                base_index = (_index(asset.body_names, protocol["base_body_name"], "body")
                              if protocol["base_body_name"] else None)
            except ValueError as exc:
                raise RuntimeError("joint or body name absent from loaded articulation") from exc
            axis = np.asarray(protocol["torque_axis_world"], dtype=float)
            axis /= np.linalg.norm(axis)
            torque = axis * float(protocol["effort_Nm"])
            torque_tensor = torch.zeros((1, len(asset.body_names), 3), dtype=torch.float32)
            torque_tensor[0, moving_index] = torch.tensor(torque, dtype=torch.float32)
            if base_index is not None:
                torque_tensor[0, base_index] = -torch.tensor(torque, dtype=torch.float32)
            zero_forces = torch.zeros_like(torque_tensor)
            if control is not None:
                control.reset()
                control_driver_index = _index(control.joint_names, protocol["joint_name"], "joint")
                control_follower_index = _index(control.joint_names,
                                                protocol["follower_joint_name"], "joint")
                follower_index = _index(asset.joint_names, protocol["follower_joint_name"], "joint")
                control_moving_index = _index(control.body_names,
                                              protocol["moving_body_name"], "body")
                control_base_index = _index(control.body_names,
                                            protocol["base_body_name"], "body")
                control_torque = torch.zeros((1, len(control.body_names), 3), dtype=torch.float32)
                control_torque[0, control_moving_index] = torch.tensor(torque, dtype=torch.float32)
                control_torque[0, control_base_index] = -torch.tensor(torque, dtype=torch.float32)
                control_zero = torch.zeros_like(control_torque)

            def measure():
                return float(asset.data.joint_pos[0, joint_index])

            def state():
                result = {
                    "joint_rad": float(asset.data.joint_pos[0, joint_index]),
                    "joint_velocity_rad_s": float(asset.data.joint_vel[0, joint_index]),
                    "root_position_m": asset.data.root_state_w[0, :3].cpu().numpy().tolist(),
                }
                if control is not None:
                    result.update(
                        follower_rad=float(asset.data.joint_pos[0, follower_index]),
                        follower_velocity_rad_s=float(asset.data.joint_vel[0, follower_index]),
                        control_joint_rad=float(control.data.joint_pos[0, control_driver_index]),
                        control_follower_rad=float(control.data.joint_pos[0, control_follower_index]),
                        control_root_position_m=control.data.root_state_w[0, :3].cpu().numpy().tolist(),
                    )
                return result

        traces: list[dict] = []
        endpoints: dict[str, float] = {}
        phases = [("settle", float(protocol["settle_s"])),
                  ("baseline", float(protocol["baseline_s"])),
                  ("drive", float(protocol["drive_s"])),
                  ("release", float(protocol["release_s"]))]
        clock = 0.0
        endpoints["initial"] = measure()
        extra_endpoints = {"initial": state()} if family == "coupled" else {}
        for phase, duration in phases:
            steps = round(duration / dt)
            for _ in range(steps):
                active = phase == "drive"
                if family == "rigid":
                    asset.set_external_force_and_torque(force_tensor if active else zero_forces,
                                                        zero_forces, is_global=True)
                else:
                    asset.set_external_force_and_torque(zero_forces,
                                                        torque_tensor if active else zero_forces,
                                                        is_global=True)
                    if control is not None:
                        control.set_external_force_and_torque(
                            control_zero, control_torque if active else control_zero,
                            is_global=True)
                asset.write_data_to_sim()
                if control is not None:
                    control.write_data_to_sim()
                sim.step(render=False)
                asset.update(dt)
                if control is not None:
                    control.update(dt)
                clock += dt
                traces.append({"t_s": clock, "phase": phase, "input_active": active, **state()})
            endpoints[phase] = measure()
            if family == "coupled":
                extra_endpoints[phase] = state()
        baseline = abs(endpoints["baseline"] - endpoints["settle"])
        driven = endpoints["drive"] - endpoints["baseline"]
        rigid_stability = {}
        if family == "rigid":
            rigid_stability = _rigid_baseline_metrics(traces)
            rigid_stability.update(_rigid_driven_metrics(traces))
            baseline = rigid_stability["baseline_planar_excursion_m"]
        if family != "rigid":
            driven *= int(protocol["expected_joint_sign"])
        coupling_metrics = {}
        if family == "coupled":
            def delta(key: str, start: str, end: str) -> float:
                return extra_endpoints[end][key] - extra_endpoints[start][key]

            follower_delta = delta("follower_rad", "baseline", "drive")
            driver_delta = delta("joint_rad", "baseline", "drive")
            control_driver_delta = delta("control_joint_rad", "baseline", "drive")
            control_follower_delta = delta("control_follower_rad", "baseline", "drive")
            baseline = max(baseline, *[
                abs(delta(key, "settle", "baseline"))
                for key in ("follower_rad", "control_joint_rad", "control_follower_rad")
            ])
            coupling_metrics = {
                "target_ratio": float(protocol["target_ratio"]),
                "ratio_residual": abs(follower_delta - float(protocol["target_ratio"]) * driver_delta),
                "disconnected_response": abs(control_follower_delta),
                "control_driver_response": control_driver_delta * int(protocol["expected_joint_sign"]),
                "follower_response": follower_delta,
                "control_prim": control_prim_path,
                "extra_endpoints": extra_endpoints,
            }
        finite = all(np.isfinite(np.asarray([item["t_s"], *item["root_position_m"],
                                          *item.get("root_quaternion_wxyz", []),
                                          *item.get("root_velocity_m_s", []),
                                          *item.get("control_root_position_m", [])])).all()
                     and ("joint_rad" not in item or
                          (math.isfinite(item["joint_rad"]) and math.isfinite(item["joint_velocity_rad_s"])))
                     and all(math.isfinite(item[key]) for key in
                             ("follower_rad", "follower_velocity_rad_s", "control_joint_rad",
                              "control_follower_rad") if key in item)
                     for item in traces)
        actual_hashes = _hashes(expected_hashes)
        checks = {
            "loaded_exported_usd": True,
            "finite_states": bool(finite),
            "baseline_stable": baseline <= float(protocol["max_baseline"]),
            "driven_response": driven >= float(protocol["min_response"]),
            "source_unchanged": actual_hashes == expected_hashes,
        }
        if family == "coupled":
            checks["ratio_consistency"] = coupling_metrics["ratio_residual"] <= float(protocol["max_ratio_residual"])
            checks["disconnected_control"] = coupling_metrics["disconnected_response"] <= float(protocol["max_disconnected_response"])
            checks["control_driver_responds"] = coupling_metrics["control_driver_response"] >= float(protocol["min_response"])
        if family == "rigid":
            checks["driven_support_height"] = (
                rigid_stability["driven_max_drop_m"] <= float(protocol["max_driven_drop_m"]))
            if protocol["max_driven_tilt_rad"] is not None:
                checks["driven_upright"] = (
                    rigid_stability["driven_max_tilt_rad"] <= float(protocol["max_driven_tilt_rad"]))
            checks["vertical_stability"] = (
                rigid_stability["baseline_z_span_m"] <= float(protocol["max_baseline_z_span_m"]))
            checks["vertical_velocity_stability"] = (
                rigid_stability["baseline_max_abs_vertical_speed_m_s"]
                <= float(protocol["max_baseline_abs_vertical_speed_m_s"]))
            checks["orientation_stability"] = (
                rigid_stability["baseline_orientation_change_rad"]
                <= float(protocol["max_baseline_orientation_change_rad"]))
        report = {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "artifact": {"scene_path": str(scene), "scene_sha256": actual_hashes[str(scene)],
                         "source_hashes": actual_hashes},
            "protocol": {
                "family": family,
                "loaded_exported_usd": True,
                "state_writes_during_measurement": False,
                "measurement_source": "simulated_state",
                "actuation_mode": "external_force" if family == "rigid" else "external_torque",
                "target_prim": protocol["target_prim"],
                "physical_prim_path": physical_prim_path,
                "fixture_description": protocol["fixture_description"],
                "configured_protocol": protocol,
                "upright_required": family == "rigid" and protocol["max_driven_tilt_rad"] is not None,
                "native_mimic_verified": family == "coupled",
                "test_only_fixture_operations": fixture_operations if family == "coupled" else [],
            },
            "measurements": {"baseline": baseline, "response": driven,
                             "unit": "m" if family == "rigid" else "rad",
                             "endpoints": endpoints, **coupling_metrics,
                             **({"support_evidence": "root_trajectory_proxy", **rigid_stability}
                                if family == "rigid" else {})},
            "scope": ("Exported scene with in-memory fixed-base and disconnected comparison fixtures; "
                      "ideal ratio only, no real-world calibration or robot task."
                      if family == "coupled" else
                      "One exported scene and one target under stated force/torque; rigid support "
                      "stability uses the full zero-input root trajectory because contact forces "
                      "are not measured; no robot task or real-world parameter calibration."),
        }
        (output / "trajectory.json").write_text(json.dumps(traces) + "\n")
        (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": report["status"], "checks": checks}), flush=True)
        return 0 if report["status"] == "passed" else 2
    except BaseException:
        failure = {"status": "failed", "checks": {},
                   "error": "Isaac worker could not complete the physical measurement",
                   "traceback": traceback.format_exc(), "configured_protocol": protocol}
        (output / "failure.json").write_text(json.dumps(failure, indent=2) + "\n")
        (output / "validation.json").write_text(json.dumps(failure, indent=2) + "\n")
        traceback.print_exc()
        return 2
    finally:
        if app is not None:
            if sim is not None:
                sim.stop()
                sim.clear_all_callbacks()
                sim.clear_instance()
            app.close(wait_for_replicator=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.request.resolve(), args.output.resolve()))
