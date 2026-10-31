"""Credential separation and native gateway handoff, without external requests."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from roboenv_perception.integration.dsh_model import KEY_ENV, read_model_config


def configuration(tmp_path, **updates):
    data = {"base_url": "https://gateway.example/v1", "model": "declared-model",
            "api_protocol": "chat_completions", "api_key": "fixture-private-key", **updates}
    path = tmp_path / "private.toml"
    path.write_text("\n".join(f"{key} = {json.dumps(value)}" for key, value in data.items()))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("protocol,api", [("chat_completions", "openai-completions"),
                                        ("responses", "openai-responses")])
def test_secret_only_reaches_separate_environment(tmp_path, protocol, api, monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "must-not-use-vlm")
    result = read_model_config(configuration(tmp_path, api_protocol=protocol))
    assert result["environment"] == {KEY_ENV: "fixture-private-key"}
    assert "fixture-private-key" not in result["patch"] + json.dumps(result["summary"])
    rows = [json.loads(line[2:]) for line in result["patch"].splitlines()]
    route = rows[1]["config"]["providers"][result["summary"]["provider"]]
    assert route["api"] == api
    assert route["models"][0]["id"] == "declared-model"


@pytest.mark.parametrize("updates", [{"api_key": ""}, {"model": ""},
    {"api_protocol": "unknown"}, {"base_url": "https://user:secret@gateway.example/v1"},
    {"base_url": "https://gateway.example/v1?api_key=private"},
    {"max_tokens": True}, {"context_window": 1024, "max_tokens": 2048}])
def test_invalid_local_config_rejected_without_source_excerpt(tmp_path, updates):
    with pytest.raises(ValueError) as error:
        read_model_config(configuration(tmp_path, **updates))
    assert "fixture-private-key" not in str(error.value)
    assert "user:secret" not in str(error.value)


def test_insecure_file_and_vlm_only_credentials_rejected(tmp_path, monkeypatch):
    path = configuration(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        read_model_config(path)
    path.chmod(0o600)
    path.write_text('base_url = "')
    with pytest.raises(ValueError, match="Cannot parse"):
        read_model_config(path)
    monkeypatch.setenv("VLM_API_KEY", "vlm-only-key")
    with pytest.raises(ValueError, match="api_key"):
        read_model_config(configuration(tmp_path, api_key=""))


def test_cli_and_node_loader_keep_secret_out_of_diagnostics(tmp_path):
    path = configuration(tmp_path)
    command = subprocess.run([sys.executable, "-m", "roboenv_perception.integration.cli",
                              "dsh-model", "--config", str(path)], capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    assert "fixture-private-key" not in command.stdout + command.stderr
    module = Path(__file__).parents[1] / "src/roboenv_perception/integration/model_config.mjs"
    probe = subprocess.run(["node", "--input-type=module", "-e",
                           f'import {{ loadModelConfig }} from {json.dumps(module.as_uri())};' + """
const result = loadModelConfig(process.argv[1]);
if (Object.keys(result.environment).join() !== 'ENVCREATOR_DSH_API_KEY') process.exit(2);
console.log(JSON.stringify(result.summary));
""", str(path)], env={**os.environ, "ENVCREATOR_PYTHON": sys.executable}, capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    assert "fixture-private-key" not in probe.stdout + probe.stderr


def test_local_config_reaches_real_native_launcher_without_model_fallback(tmp_path):
    root = os.environ.get("ENVCREATOR_CLAWSPINE_ROOT")
    if not root:
        pytest.skip("Set ENVCREATOR_CLAWSPINE_ROOT for actual native argument boundary")
    from test_clawspine_integration import bundle_fixture

    bundle = bundle_fixture(tmp_path / "bundle")
    path = configuration(tmp_path, api_protocol="responses")
    args = [sys.executable, "-m", "roboenv_perception.integration.cli", "clawspine",
            "--bundle", str(bundle), "--clawspine-root", root, "--dry-run",
            "--spawn", "0,0,0,0", "--observer-eye", "1,-2,1", "--observer-target", "0,0,1",
            "--dsh-model-config", str(path)]
    completed = subprocess.run(args, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert "fixture-private-key" not in completed.stdout + completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["model_selection"]["model"] == "declared-model"
    assert receipt["model_selection"]["provider"] == "envcreator-gateway"
    assert receipt["gateway"]["api_protocol"] == "responses"
    conflict = subprocess.run([*args, "--model", "another-model"], capture_output=True, text=True)
    assert conflict.returncode != 0
    assert "conflicting" in conflict.stderr


def test_model_alternatives_share_route_and_keep_explicit_selection(tmp_path):
    path = configuration(tmp_path, model="gpt-6.1-sol", models=["gpt-6.1-sol", "gpt-6-astra"])
    default = read_model_config(path)
    astra = read_model_config(path, "gpt-6-astra")
    assert default["summary"]["model"] == "gpt-6.1-sol"
    assert astra["summary"]["model"] == "gpt-6-astra"
    assert default["environment"] == astra["environment"]
    rows = [json.loads(line[2:]) for line in astra["patch"].splitlines()]
    route = rows[1]["config"]["providers"]["envcreator-gateway"]
    assert [m["id"] for m in route["models"]] == ["gpt-6.1-sol", "gpt-6-astra"]
    assert rows[2]["config"]["model"] == "gpt-6-astra"
    with pytest.raises(ValueError, match="not in"):
        read_model_config(path, "unregistered-model")


@pytest.mark.parametrize("models", [[], "astra", ["wrong-default"], ["declared-model", "declared-model"],
                                   ["declared-model", "invalid model"]])
def test_invalid_alternative_models(tmp_path, models):
    with pytest.raises(ValueError, match="models"):
        read_model_config(configuration(tmp_path, models=models))
