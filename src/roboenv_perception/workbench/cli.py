"""Start a dedicated native DSH asset workbench and private local worker service."""

import argparse
import fcntl
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import Settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    config = Settings.load(args.config.absolute())
    plugin = Path(__file__).resolve().parents[3] / "integrations/studio"
    dsh = config.dsh_root / "node_modules/@deepseek-ai/dsh/package.json"
    checks = {
        "dsh_installed": dsh.is_file(),
        "ui_built": (plugin / "dist/client.js").is_file(),
        "node24": subprocess.run(
            ["node", "-p", 'process.versions.node.split(".")[0]'], capture_output=True, text=True
        ).stdout.strip()
        == "24",
    }
    model = None
    if config.model_config_path:
        from ..integration.dsh_model import read_model_config

        model = read_model_config(config.model_config_path, config.model)
    checks["dsh_version_pinned"] = (
        checks["dsh_installed"] and json.loads(dsh.read_text()).get("version") == "0.2.1-alpha.1"
    )
    if args.doctor:
        print(
            json.dumps(
                {
                    "checks": checks,
                    "model": model["summary"] if model else {"configured": False},
                    "generation_missing": config.generation_missing(),
                    "model_execution": "not_run",
                    "robot_execution": "not_run",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if all(checks.values()) else 2
    if not all(checks.values()):
        parser.error("Missing studio dependencies; run --doctor and build integrations/studio")
    config.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (config.state_root / ".studio.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("This Studio state directory is already running")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        backend_port = probe.getsockname()[1]
    token_path = config.state_root / "backend.token"
    token_path.write_text(secrets.token_urlsafe(32))
    token_path.chmod(0o600)

    def private_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        path.chmod(0o600)

    private_json(
        config.state_root / "model-summary.json",
        model["summary"] if model else {"configured": False},
    )
    if model:
        patch = config.state_root / "model.patch.yml"
        patch.write_text(model["patch"])
        patch.chmod(0o600)
    binding_path = config.state_root / "binding.json"
    private_json(
        binding_path,
        {
            "dshRoot": str(config.dsh_root),
            "stateRoot": str(config.state_root),
            "backendURL": f"http://127.0.0.1:{backend_port}/",
            "tokenFile": str(token_path),
            "workspace": str(config.state_root / "workspace"),
            "pluginRoot": str(plugin),
            "port": config.port,
            "maxWallSeconds": config.max_wall_seconds,
            "debugComponentTools": config.debug_component_tools,
            "modelConfigured": bool(model),
            "model": model["summary"] if model else {},
            "open": args.open,
        },
    )
    (config.state_root / "workspace").mkdir(exist_ok=True, mode=0o700)
    (config.state_root / "ready.json").unlink(missing_ok=True)
    children = []
    stopped = False

    def stop(signum=None, frame=None):
        nonlocal stopped
        stopped = True
        for child in reversed(children):
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        backend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "roboenv_perception.workbench.server",
                str(args.config.absolute()),
                str(token_path),
                str(backend_port),
            ],
            start_new_session=True,
        )
        children.append(backend)
        import httpx

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not stopped:
            if backend.poll() is not None:
                raise RuntimeError("Studio backend exited during startup")
            try:
                if (
                    httpx.get(
                        f"http://127.0.0.1:{backend_port}/api/health",
                        headers={"Authorization": "Bearer " + token_path.read_text()},
                        timeout=1,
                    ).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("Studio backend did not become ready")
        environment = {
            k: v
            for k, v in os.environ.items()
            if k not in {"VLM_API_KEY", "ENVCREATOR_DSH_API_KEY"}
        }
        if model:
            environment.update(model["environment"])
        native = subprocess.Popen(
            ["node", str(Path(__file__).with_name("launcher.mjs")), str(binding_path)],
            env=environment,
            start_new_session=True,
        )
        children.append(native)
        while not stopped and native.poll() is None and backend.poll() is None:
            time.sleep(0.3)
        return 0 if stopped else 1
    finally:
        stop()
        for child in reversed(children):
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
