"""Run a trusted Isaac worker against an exported USD and audit its evidence.

This module does not infer that a scene is physically useful from a successful
process exit.  The worker must load the exported USD, measure a response to an
applied force or torque, and emit a report bound to the unchanged input files.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class InteractionFamily(str, Enum):
    RIGID = "rigid"
    HINGE = "hinge"
    ROTARY = "rotary"
    COUPLED = "coupled"


_ALLOWED_ACTUATION = {
    InteractionFamily.RIGID: {"external_force", "external_impulse"},
    InteractionFamily.HINGE: {"external_torque"},
    InteractionFamily.ROTARY: {"external_torque", "joint_effort"},
    InteractionFamily.COUPLED: {"external_torque", "joint_effort"},
}
_REQUIRED_CHECKS = {"loaded_exported_usd", "finite_states", "baseline_stable",
                    "driven_response", "source_unchanged"}
_RIGID_STABILITY_CHECKS = {"vertical_stability", "vertical_velocity_stability",
                           "orientation_stability", "driven_support_height"}
_REPORT_SHUTDOWN_GRACE_S = 15.0
LEGACY_WORKER_LIMITATIONS = {
    "isaac_table_worker.py": "Fixed tabletop probe experiment; not a generic object push test.",
    "isaac_hinge_worker.py": "Fixed laptop prim and hinge cases; cannot validate an arbitrary hinge.",
    "isaac_scene_check.py": "Fixed laptop, mouse and bottle prims; reload only.",
    "isaac_audio_scene_worker.py": "Fixed white-table inventory and direct-angle case tests.",
    "isaac_bicycle_worker.py": "Bicycle-specific geometry and joint paths.",
    "isaac_drivetrain_worker.py": "Bicycle-specific fixed-ratio transmission and fixtures.",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class PhysicsProtocol:
    family: InteractionFamily
    target_prim: str
    min_response: float
    max_baseline: float
    force_world_N: tuple[float, float, float] = (3.0, 0.0, 0.0)
    max_baseline_z_span_m: float = 0.02
    max_baseline_abs_vertical_speed_m_s: float = 0.1
    max_baseline_orientation_change_rad: float = 0.15
    max_driven_drop_m: float = 0.02
    max_driven_tilt_rad: float | None = None
    torque_axis_world: tuple[float, float, float] = (1.0, 0.0, 0.0)
    effort_Nm: float = 0.2
    joint_name: str | None = None
    follower_joint_name: str | None = None
    moving_body_name: str | None = None
    base_body_name: str | None = None
    disconnected_control_prim: str | None = None
    control_offset_world_m: tuple[float, float, float] = (0.0, 20.0, 0.0)
    expected_joint_sign: int = 1
    target_ratio: float | None = None
    max_ratio_residual: float | None = None
    max_disconnected_response: float | None = None
    settle_s: float = 0.5
    baseline_s: float = 0.5
    drive_s: float = 1.0
    release_s: float = 0.25
    dt_s: float = 1 / 240
    fixture_description: str = "exported scene as authored"

    def __post_init__(self) -> None:
        object.__setattr__(self, "family", InteractionFamily(self.family))
        if not self.target_prim.startswith("/World/") or "//" in self.target_prim:
            raise ValueError("target_prim must be an absolute /World prim path")
        for name in ("min_response", "max_baseline", "settle_s", "baseline_s",
                     "drive_s", "release_s", "dt_s", "max_baseline_z_span_m",
                     "max_baseline_abs_vertical_speed_m_s", "max_baseline_orientation_change_rad",
                     "max_driven_drop_m"):
            value = getattr(self, name)
            if not _finite(value) or value < 0 or (name in {"min_response", "baseline_s", "drive_s", "dt_s"} and value == 0):
                raise ValueError(f"{name} must be finite and positive where required")
        if self.max_driven_tilt_rad is not None and (
            not _finite(self.max_driven_tilt_rad) or self.max_driven_tilt_rad < 0
        ):
            raise ValueError("max_driven_tilt_rad must be None or finite and nonnegative")
        if self.expected_joint_sign not in {-1, 1}:
            raise ValueError("expected_joint_sign must be -1 or 1")
        if self.family == InteractionFamily.RIGID:
            if not all(_finite(v) for v in self.force_world_N) or math.dist(self.force_world_N, (0, 0, 0)) == 0:
                raise ValueError("rigid test requires a nonzero finite force_world_N")
        else:
            if not self.joint_name or not _finite(self.effort_Nm) or self.effort_Nm == 0:
                raise ValueError("joint test requires joint_name and nonzero finite effort_Nm")
            if not all(_finite(v) for v in self.torque_axis_world) or math.dist(self.torque_axis_world, (0, 0, 0)) == 0:
                raise ValueError("joint test requires a nonzero finite torque_axis_world")
            if self.family == InteractionFamily.HINGE and (not self.moving_body_name or not self.base_body_name):
                raise ValueError("hinge test requires moving_body_name and base_body_name")
            if self.family in {InteractionFamily.ROTARY, InteractionFamily.COUPLED} and not self.moving_body_name:
                raise ValueError("rotary/coupled test requires moving_body_name")
        if self.family == InteractionFamily.COUPLED:
            if not self.follower_joint_name or not self.base_body_name:
                raise ValueError("coupled test requires follower_joint_name and base_body_name")
            if (self.target_ratio is None or not _finite(self.target_ratio)
                    or self.target_ratio == 0
                    or self.max_ratio_residual is None or not _finite(self.max_ratio_residual)
                    or self.max_disconnected_response is None or not _finite(self.max_disconnected_response)):
                raise ValueError("coupled test requires finite ratio and control thresholds")
            if self.max_ratio_residual < 0 or self.max_disconnected_response < 0:
                raise ValueError("coupled control thresholds must be nonnegative")
            if not all(_finite(v) for v in self.control_offset_world_m) or math.dist(self.control_offset_world_m, (0, 0, 0)) < 1:
                raise ValueError("coupled control offset must be finite and at least one metre")
            if self.disconnected_control_prim is None:
                object.__setattr__(self, "disconnected_control_prim", "/World/HarnessValidation/Disconnected")
            if not self.disconnected_control_prim.startswith("/World/"):
                raise ValueError("disconnected_control_prim must be a /World path")
            if self.fixture_description == "exported scene as authored":
                object.__setattr__(self, "fixture_description",
                                   "in-memory fixed-base comparison and disconnected control clone")

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        value = asdict(self)
        value["family"] = self.family.value
        return json.loads(json.dumps(value))


@dataclass(frozen=True)
class PhysicsTask:
    scene_usd: Path
    output_dir: Path
    python_executable: Path
    protocol: PhysicsProtocol
    dependencies: tuple[Path, ...] = ()
    worker_script: Path | None = None
    trusted_worker_sha256: str | None = None
    timeout_s: float = 1800.0
    worker_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhysicsResult:
    status: str
    issues: tuple[str, ...] = ()
    report_path: Path | None = None
    log_path: Path | None = None
    report_sha256: str | None = None
    source_hashes: dict[str, str] = field(default_factory=dict)
    worker_exit_code: int | None = None
    shutdown_status: str = "normal"

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def _source_hashes(task: PhysicsTask) -> dict[str, str]:
    if not task.scene_usd.is_file() or task.scene_usd.suffix.lower() not in {".usd", ".usda", ".usdc"}:
        raise ValueError("scene_usd must be an existing exported USD file")
    paths = set(_ascii_usd_dependencies(task.scene_usd))
    paths.update(path.resolve() for path in task.dependencies)
    if any(not path.is_file() for path in paths):
        raise ValueError("all declared USD dependencies must exist")
    return {str(path): sha256(path) for path in sorted(paths)}


def _ascii_usd_dependencies(scene: Path) -> set[Path]:
    """Bind relative @asset@ references in ASCII layers, including nested ones.

    USD crate layers cannot be inspected without USD libraries in this Python
    environment; their external dependencies must be supplied explicitly.
    """
    root = scene.resolve().parent
    pending = [scene.resolve()]
    found: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in found:
            continue
        if not path.is_file():
            raise ValueError(f"USD dependency does not exist: {path}")
        found.add(path)
        if path.suffix.lower() not in {".usd", ".usda", ".usdc"}:
            continue
        with path.open("rb") as stream:
            header = stream.read(8)
        if header.startswith(b"PXR-USDC"):
            # Crate references need USD libraries to enumerate precisely. The
            # scene assembler packages each referenced export in its own asset
            # directory, so bind every file in that directory conservatively.
            if path != scene.resolve():
                for member in path.parent.rglob("*"):
                    if member.is_symlink():
                        raise ValueError(f"symlink in binary USD asset package: {member}")
                    if member.is_file():
                        found.add(member.resolve())
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for ref in re.findall(r"@([^@\r\n]+)@", source):
            if "://" in ref or "<UDIM>" in ref or Path(ref).is_absolute():
                raise ValueError(f"USD requires a local portable dependency: {ref}")
            dep = (path.parent / ref).resolve()
            if not dep.is_relative_to(root):
                raise ValueError(f"USD dependency escapes the scene package: {ref}")
            pending.append(dep)
    return found


def verify_physics_report(
    protocol: PhysicsProtocol,
    scene_usd: Path,
    source_hashes: dict[str, str],
    report: dict[str, Any],
) -> tuple[str, ...]:
    """Return all reasons a report cannot be accepted as interaction evidence."""
    issues: list[str] = []
    if not isinstance(report, dict):
        return ("validation report is not an object",)
    checks = report.get("checks")
    if report.get("status") != "passed":
        issues.append("worker did not report passed")
    if not isinstance(checks, dict) or not checks or any(value is not True for value in checks.values()):
        issues.append("checks are missing or include nonpassing values")
    if not isinstance(checks, dict) or not _REQUIRED_CHECKS.issubset(checks):
        issues.append("required physical checks are missing")
    if protocol.family == InteractionFamily.RIGID and (
        not isinstance(checks, dict) or not _RIGID_STABILITY_CHECKS.issubset(checks)
    ):
        issues.append("rigid support stability checks are missing")
    if protocol.family == InteractionFamily.COUPLED and (
        not isinstance(checks, dict) or not {
            "disconnected_control", "ratio_consistency", "control_driver_responds"
        }.issubset(checks)
    ):
        issues.append("coupled control checks are missing")
    if protocol.family == InteractionFamily.COUPLED and (
        not protocol.disconnected_control_prim or not protocol.follower_joint_name
    ):
        issues.append("coupled protocol has no paired disconnected control or follower joint")
    artifact = report.get("artifact")
    scene_key = str(scene_usd.resolve())
    if not isinstance(artifact, dict) or artifact.get("scene_path") != scene_key or artifact.get("scene_sha256") != source_hashes.get(scene_key):
        issues.append("report is not bound to the exported USD")
    if not isinstance(artifact, dict) or artifact.get("source_hashes") != source_hashes:
        issues.append("report dependency hashes do not match the tested inputs")
    declared = report.get("protocol")
    if not isinstance(declared, dict) or declared.get("family") != protocol.family.value:
        issues.append("interaction family mismatch")
        declared = {}
    if declared.get("loaded_exported_usd") is not True:
        issues.append("worker did not confirm loading the exported USD")
    if declared.get("state_writes_during_measurement") is not False:
        issues.append("joint/root state writes during measurement are not interaction evidence")
    if declared.get("measurement_source") != "simulated_state":
        issues.append("response must come from simulated state")
    if declared.get("actuation_mode") not in _ALLOWED_ACTUATION[protocol.family]:
        issues.append("actuation mode is incompatible with the task family")
    if declared.get("target_prim") != protocol.target_prim:
        issues.append("measured target prim differs from the frozen protocol")
    if declared.get("fixture_description") != protocol.fixture_description:
        issues.append("fixture differs from the frozen protocol")
    if declared.get("configured_protocol") != protocol.as_dict():
        issues.append("worker protocol differs from the frozen test conditions")
    if protocol.family == InteractionFamily.COUPLED and declared.get("native_mimic_verified") is not True:
        issues.append("native coupling was not verified on the exported USD")
    measurements = report.get("measurements")
    if not isinstance(measurements, dict):
        measurements = {}
    unit = "m" if protocol.family == InteractionFamily.RIGID else "rad"
    if measurements.get("unit") != unit:
        issues.append("measurement unit does not match the family")
    baseline, response = measurements.get("baseline"), measurements.get("response")
    if not _finite(baseline) or baseline < 0 or baseline > protocol.max_baseline:
        issues.append("baseline is missing, nonfinite, or above its limit")
    if not _finite(response) or response < protocol.min_response:
        issues.append("driven response is missing, nonfinite, or below its threshold")
    if protocol.family == InteractionFamily.RIGID:
        upright_required = protocol.max_driven_tilt_rad is not None
        if declared.get("upright_required") is not upright_required:
            issues.append("upright requirement differs from the frozen protocol")
        if upright_required and (not isinstance(checks, dict) or checks.get("driven_upright") is not True):
            issues.append("required driven upright check is missing or failed")
        if not upright_required and isinstance(checks, dict) and "driven_upright" in checks:
            issues.append("unrequested upright constraint was reported as a check")
        if measurements.get("support_evidence") != "root_trajectory_proxy":
            issues.append("rigid support evidence must identify the trajectory proxy")
        for key, limit, label in (
            ("baseline_z_span_m", protocol.max_baseline_z_span_m, "vertical drift"),
            ("baseline_max_abs_vertical_speed_m_s", protocol.max_baseline_abs_vertical_speed_m_s,
             "vertical speed"),
            ("baseline_orientation_change_rad", protocol.max_baseline_orientation_change_rad,
             "orientation change"),
            ("driven_max_drop_m", protocol.max_driven_drop_m, "driven vertical drop"),
        ):
            value = measurements.get(key)
            if not _finite(value) or value < 0 or value > limit:
                issues.append(f"rigid baseline {label} is missing, nonfinite, or above its limit")
        tilt = measurements.get("driven_max_tilt_rad")
        if not _finite(tilt) or tilt < 0 or (
            upright_required and tilt > protocol.max_driven_tilt_rad
        ):
            issues.append("rigid driven tilt is missing, nonfinite, or above its configured limit")
    if protocol.family == InteractionFamily.COUPLED:
        if measurements.get("control_prim") != protocol.disconnected_control_prim:
            issues.append("disconnected control prim differs from frozen protocol")
        disconnected = measurements.get("disconnected_response")
        ratio_error = measurements.get("ratio_residual")
        if not _finite(disconnected) or abs(disconnected) > protocol.max_disconnected_response:
            issues.append("disconnected control does not stay below its limit")
        if not _finite(ratio_error) or abs(ratio_error) > protocol.max_ratio_residual:
            issues.append("coupling ratio residual exceeds its limit")
        if measurements.get("target_ratio") != protocol.target_ratio:
            issues.append("target ratio differs from the frozen protocol")
        if measurements.get("control_driver_response") is None or not _finite(measurements.get("control_driver_response")) or measurements["control_driver_response"] < protocol.min_response:
            issues.append("disconnected control driver did not respond to input")
    return tuple(issues)


def _stop_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def run_physics_task(task: PhysicsTask) -> PhysicsResult:
    """Launch a configured worker in a fresh directory and audit its report.

    Custom workers require a preapproved SHA-256. The bundled worker supports
    rigid, hinge, rotary and native coupled tests. Trusted code remains
    necessary: a fabricated report cannot be
    detected from its JSON alone.
    """
    scene = task.scene_usd.resolve()
    output = task.output_dir.resolve()
    worker = (task.worker_script or Path(__file__).with_name("isaac_generic_worker.py")).resolve()
    if output.exists():
        raise ValueError("output_dir must be a fresh, nonexistent directory")
    if not task.python_executable.is_file() or not worker.is_file():
        raise ValueError("configured Python and worker script must exist")
    if not _finite(task.timeout_s) or task.timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    if task.worker_script is not None and task.trusted_worker_sha256 is None:
        raise ValueError("custom worker requires trusted_worker_sha256")
    worker_hash = sha256(worker)
    if task.trusted_worker_sha256 is not None and worker_hash != task.trusted_worker_sha256:
        raise ValueError("worker script differs from its approved SHA-256")
    source_hashes = _source_hashes(task)
    if task.worker_script is None and task.worker_args:
        raise ValueError("bundled worker does not accept unreviewed extra arguments")
    output.parent.mkdir(parents=True, exist_ok=True)
    log = output.parent / "logs" / f"{output.name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    request = {
        "format": "roboenv-physics-request-v1",
        "scene_usd": str(scene),
        "source_hashes": source_hashes,
        "protocol": task.protocol.as_dict(),
    }
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cache_root = output.parent / "runtime-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    env["XDG_CACHE_HOME"] = str(cache_root)
    env["WARP_CACHE_PATH"] = str(cache_root / "warp")
    code: int | None = None
    timed_out = False
    shutdown_after_report = False
    with tempfile.TemporaryDirectory(prefix="physics-request-", dir=output.parent) as temp:
        request_path = Path(temp) / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
        command = [str(task.python_executable), "-u", str(worker), "--request", str(request_path),
                   "--output", str(output), *task.worker_args]
        with log.open("w") as stream:
            process = subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + task.timeout_s
                report_written_at = None
                while process.poll() is None:
                    now = time.monotonic()
                    if now >= deadline:
                        raise subprocess.TimeoutExpired(command, task.timeout_s)
                    if (output / "validation.json").is_file():
                        if report_written_at is None:
                            report_written_at = now
                        elif now - report_written_at >= _REPORT_SHUTDOWN_GRACE_S:
                            shutdown_after_report = True
                            _stop_process_group(process)
                            break
                    try:
                        process.wait(timeout=min(0.5, max(0.01, deadline - now)))
                    except subprocess.TimeoutExpired:
                        pass
                code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_process_group(process)
                code = process.returncode
    report_path = output / "validation.json"
    issues: list[str] = []
    if timed_out:
        issues.append("worker exceeded timeout")
    if code != 0 and not shutdown_after_report:
        issues.append(f"worker exited with code {code}")
    if sha256(worker) != worker_hash:
        issues.append("worker changed during execution")
    for path, digest in source_hashes.items():
        if not Path(path).is_file() or sha256(Path(path)) != digest:
            issues.append(f"source changed during execution: {path}")
    report: dict[str, Any] | None = None
    if not report_path.is_file():
        issues.append("worker did not persist validation.json")
    else:
        try:
            report = json.loads(report_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            issues.append(f"invalid validation.json: {exc}")
    if report is not None:
        issues.extend(verify_physics_report(task.protocol, scene, source_hashes, report))
    return PhysicsResult(
        "passed" if not issues else "failed",
        tuple(issues), report_path if report_path.is_file() else None, log,
        sha256(report_path) if report_path.is_file() else None, source_hashes, code,
        "terminated_after_report" if shutdown_after_report else "normal",
    )
