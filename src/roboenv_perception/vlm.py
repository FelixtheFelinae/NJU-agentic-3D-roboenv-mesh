"""Configurable HTTP inference; calls never execute tools or initiate another stage."""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .contracts import VLMConfig
from .io import read_json, sha256, write_json
from .prompts import SYSTEM


class VLM(Protocol):
    def infer(
        self, stage: str, prompt: str, images: list[Path], schema: dict, trace: Path
    ) -> dict: ...


def expand_env(value: str) -> str:
    def replace(match):
        name = match.group(1)
        if not os.environ.get(name):
            raise ValueError(f"required environment variable is unset: {name}")
        return os.environ[name]

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)


def extract_path(value, path: str):
    for part in path.split(".") if path else []:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def parse_json_content(content):
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise ValueError("VLM response content must be a JSON object or JSON text")
    content = content.strip()
    if content.startswith("```") and content.endswith("```"):
        content = "\n".join(content.splitlines()[1:-1])
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("VLM must return a JSON object")
    return value


def render_template(value, replacements: dict):
    if isinstance(value, dict):
        return {k: render_template(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, replacements) for v in value]
    if isinstance(value, str) and value in replacements:
        return replacements[value]
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward authorization headers to another endpoint.


class HTTPVLM:
    def __init__(self, config: VLMConfig):
        self.config = config
        parsed = urlsplit(config.endpoint)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("vlm.endpoint must be a complete http(s) request URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "endpoint must not contain credentials, query or fragment; use headers"
            )
        if config.style != "generic" and not config.model.strip():
            raise ValueError("vlm.model is required for this API style")
        if config.style == "generic" and not config.request_template:
            raise ValueError("generic API requires request_template")
        if {
            "messages",
            "input",
            "model",
            "tools",
            "stream",
            "instructions",
        } & config.extra_body.keys():
            raise ValueError("extra_body must not override model, input, messages, tools or stream")
        self.opener = urllib.request.build_opener(NoRedirect())

    def build_payload(self, stage, prompt, images, schema):
        urls = [
            "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
            for p in images
        ]
        cfg = self.config
        if cfg.style == "generic":
            return render_template(
                cfg.request_template,
                {
                    "{{system}}": SYSTEM,
                    "{{prompt}}": prompt,
                    "{{images}}": urls,
                    "{{image}}": urls[0],
                    "{{schema}}": schema,
                    "{{stage}}": stage,
                    "{{model}}": cfg.model,
                },
            )
        if cfg.style == "chat_completions":
            payload = {
                "model": cfg.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}]
                        + [{"type": "image_url", "image_url": {"url": url}} for url in urls],
                    },
                ],
            }
            if cfg.json_mode:
                payload["response_format"] = {"type": "json_object"}
        else:
            payload = {
                "model": cfg.model,
                "stream": False,
                "instructions": SYSTEM,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}]
                        + [{"type": "input_image", "image_url": url} for url in urls],
                    },
                ],
            }
            if cfg.json_mode:
                payload["text"] = {"format": {"type": "json_object"}}
        payload.update(cfg.extra_body)
        return payload

    def infer(self, stage, prompt, images, schema, trace):
        cfg = self.config
        # Persist references, not credentials or repeated base64 image contents.
        write_json(
            trace / "request.json",
            {
                "backend": "http",
                "style": cfg.style,
                "endpoint": cfg.endpoint,
                "model": cfg.model,
                "prompt": prompt,
                "system": SYSTEM,
                "schema": schema,
                "images": [{"name": p.name, "sha256": sha256(p)} for p in images],
                "extra_body": cfg.extra_body,
            },
        )
        headers = {"Content-Type": "application/json"}
        headers.update({name: expand_env(value) for name, value in cfg.headers.items()})
        body = json.dumps(
            self.build_payload(stage, prompt, images, schema), allow_nan=False
        ).encode()
        started = time.perf_counter()
        for attempt in range(cfg.retries + 1):
            try:
                request = urllib.request.Request(
                    cfg.endpoint, data=body, headers=headers, method="POST"
                )
                with self.opener.open(request, timeout=cfg.timeout_seconds) as response:
                    raw = json.load(response)
                break
            except urllib.error.HTTPError as error:
                if error.code not in {408, 429, 500, 502, 503, 504} or attempt == cfg.retries:
                    raise RuntimeError(
                        f"VLM HTTP request failed (status {error.code}); no semantic retry"
                    ) from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == cfg.retries:
                    raise RuntimeError(
                        "VLM connection failed or timed out; check endpoint/network"
                    ) from None
            time.sleep(min(2**attempt, 4))
        write_json(trace / "response.json", raw)
        if cfg.response_path:
            content = extract_path(raw, cfg.response_path)
        elif cfg.style == "chat_completions":
            content = raw["choices"][0]["message"]["content"]
        elif cfg.style == "responses":
            if raw.get("status") not in {None, "completed"}:
                raise ValueError("Responses request did not complete")
            content = "".join(
                part["text"]
                for item in raw.get("output", [])
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )
        else:
            content = raw
        result = parse_json_content(content)
        write_json(
            trace / "call.json",
            {
                "backend": "http",
                "elapsed_seconds": time.perf_counter() - started,
                "attempts": attempt + 1,
                "usage": raw.get("usage"),
                "reported_model": raw.get("model"),
                "provider_cost": None,
            },
        )
        return result


class ReplayVLM:
    def __init__(self, config: VLMConfig):
        self.root = Path(config.replay_dir)
        if not config.replay_dir or not self.root.is_dir():
            raise ValueError("replay VLM requires an existing replay_dir")

    def infer(self, stage, prompt, images, schema, trace):
        source = self.root / f"{stage}.json"
        result = read_json(source)
        write_json(
            trace / "request.json",
            {
                "backend": "replay",
                "prompt": prompt,
                "images": [{"name": p.name, "sha256": sha256(p)} for p in images],
            },
        )
        write_json(trace / "response.json", result)
        write_json(
            trace / "call.json",
            {
                "backend": "replay",
                "source_sha256": sha256(source),
                "model_executed": False,
                "provider_cost": None,
            },
        )
        return result


def make_vlm(config: VLMConfig) -> VLM:
    return ReplayVLM(config) if config.backend == "replay" else HTTPVLM(config)
