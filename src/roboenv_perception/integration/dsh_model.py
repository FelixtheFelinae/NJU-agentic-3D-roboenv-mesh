"""Independent, local DSH gateway configuration; never reads VLM credentials."""

import json
import os
import re
import stat
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

PROVIDER = "envcreator-gateway"
KEY_ENV = "ENVCREATOR_DSH_API_KEY"
PROTOCOLS = {"chat_completions": "openai-completions", "responses": "openai-responses"}


def read_model_config(path: Path, selected_model: str | None = None) -> dict:
    """Secret-bearing return value. Only pass environment to the native DSH child."""
    path = path.expanduser().absolute()
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
            or info.st_uid != os.getuid() or info.st_size > 65536):
        raise ValueError("DSH config must be a current-user regular file with mode 0600")
    try:
        data = tomllib.loads(path.read_text())
    except (ValueError, UnicodeError):
        raise ValueError("Cannot parse DSH TOML; inspect the local file") from None
    allowed = {"base_url", "model", "models", "api_protocol", "api_key", "context_window", "max_tokens"}
    if set(data) - allowed:
        raise ValueError("Unknown DSH configuration fields")
    for name in ("base_url", "model", "api_protocol", "api_key"):
        if not isinstance(data.get(name), str) or not data[name].strip():
            raise ValueError("Missing DSH config field: " + name)
    endpoint = data["base_url"].strip().rstrip("/")
    try:
        url = urlsplit(endpoint)
        valid_url = (url.scheme in {"https", "http"} and url.hostname and not any(
            (url.username, url.password, url.query, url.fragment)))
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError("DSH base_url requires a credential-free HTTP(S) API root")
    model, protocol = data["model"].strip(), data["api_protocol"].strip()
    if not re.fullmatch(r"[\w./:-]+", model):
        raise ValueError("Invalid DSH model identifier")
    models = data.get("models", [model])
    if (not isinstance(models, list) or not models or any(
            not isinstance(item, str) or not re.fullmatch(r"[\w./:-]+", item) for item in models)
            or len(set(models)) != len(models) or model not in models):
        raise ValueError("DSH models must be unique model identifiers including the default model")
    if selected_model is not None:
        if selected_model not in models:
            raise ValueError("Selected DSH model is not in the configured models")
        model = selected_model
    if protocol not in PROTOCOLS:
        raise ValueError("DSH api_protocol must be chat_completions or responses")
    context, output = data.get("context_window", 131072), data.get("max_tokens", 8192)
    if any(type(v) is not int or v <= 0 for v in (context, output)) or output >= context:
        raise ValueError("DSH token capacities must be positive integers with max_tokens < context_window")
    route = {"apiKeyEnv": KEY_ENV, "api": PROTOCOLS[protocol], "baseURL": endpoint,
             "models": [{"id": item, "input": ["text", "image"],
                         "contextWindow": context, "maxTokens": output} for item in models],
             "retryPolicy": {"mode": "normal", "maxRetries": 0}}
    rows = [{"id": "llm-deepseek", "disabled": True},
            {"id": "llm-pi-ai", "disabled": False, "config": {"providers": {PROVIDER: route}}},
            {"id": "agent-default-model", "config": {"provider": PROVIDER, "model": model}}]
    summary = {"provider": PROVIDER, "model": model, "available_models": models, "api_protocol": protocol,
               "base_url": endpoint, "credential_environment": KEY_ENV,
               "context_window": context, "max_tokens": output,
               "capacity_source": "local declaration or conservative adapter defaults; not provider verified",
               "image_input": "declared; requires native image smoke verification",
               "credential_source": "independent local DSH config", "vlm_credentials_used": False}
    return {"summary": summary, "patch": "".join("- " + json.dumps(row) + "\n" for row in rows),
            "environment": {KEY_ENV: data["api_key"].strip()}}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Check local independent DSH settings without a model request")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = read_model_config(args.config)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps({**config["summary"], "configuration_valid": True,
                      "model_execution": "not_run"}, ensure_ascii=False, indent=2))
    return 0
