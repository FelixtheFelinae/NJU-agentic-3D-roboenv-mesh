import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from roboenv_perception.contracts import VLMConfig
from roboenv_perception.vlm import HTTPVLM


@pytest.fixture
def service():
    state = {"requests": [], "headers": [], "response": {}, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            state["headers"].append(dict(self.headers))
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(state["response"]).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/infer", state
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("style", ["chat_completions", "responses", "generic"])
def test_real_http_roundtrip_payload_and_credentials_not_logged(
    service, tmp_path, monkeypatch, style
):
    endpoint, state = service
    monkeypatch.setenv("TEST_VLM_KEY", "local-test-secret")
    expected = {"objects": [], "scene_description": "empty"}
    if style == "chat_completions":
        state["response"] = {
            "choices": [{"message": {"content": json.dumps(expected)}}],
            "usage": {"total_tokens": 10},
        }
    elif style == "responses":
        state["response"] = {
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": json.dumps(expected)}]}],
        }
    else:
        state["response"] = {"result": {"items": [expected]}}
    config = VLMConfig(
        style=style,
        endpoint=endpoint,
        model="user-supplied-model",
        retries=0,
        headers={"Authorization": "Bearer ${TEST_VLM_KEY}"},
        request_template={"query": "{{prompt}}", "pictures": "{{images}}", "schema": "{{schema}}"},
        response_path="result.items.0" if style == "generic" else "",
    )
    image = tmp_path / "image.png"
    Image.new("RGB", (12, 12)).save(image)
    result = HTTPVLM(config).infer(
        "inventory", "Return JSON", [image], {"type": "object"}, tmp_path / "trace"
    )
    assert result == expected and len(state["requests"]) == 1
    assert state["headers"][0]["Authorization"] == "Bearer local-test-secret"
    payload = state["requests"][0]
    if style == "chat_completions":
        assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
    elif style == "responses":
        assert payload["input"][0]["content"][1]["type"] == "input_image"
    else:
        assert isinstance(payload["pictures"], list) and payload["schema"] == {"type": "object"}
    for path in (tmp_path / "trace").glob("*.json"):
        assert "local-test-secret" not in path.read_text()


def test_malformed_content_does_not_trigger_semantic_retry(service, tmp_path):
    endpoint, state = service
    state["response"] = {"choices": [{"message": {"content": "this is not JSON"}}]}
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    config = VLMConfig(endpoint=endpoint, model="any-model", headers={}, retries=3)
    with pytest.raises(ValueError):
        HTTPVLM(config).infer("inventory", "JSON please", [image], {}, tmp_path / "trace")
    assert len(state["requests"]) == 1
    assert (tmp_path / "trace/response.json").exists()


def test_http_auth_error_does_not_leak_server_response(service, tmp_path):
    endpoint, state = service
    state.update(status=401, response={"echo": "sensitive server message"})
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    with pytest.raises(RuntimeError, match="status 401") as error:
        HTTPVLM(VLMConfig(endpoint=endpoint, model="any", headers={}, retries=3)).infer(
            "inventory", "", [image], {}, tmp_path / "trace"
        )
    assert "sensitive" not in str(error.value) and len(state["requests"]) == 1


def test_missing_env_key_fails_before_request(service, tmp_path):
    endpoint, state = service
    image = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(image)
    config = VLMConfig(endpoint=endpoint, model="any", headers={"X-Key": "${MISSING_TEST_VLM_KEY}"})
    with pytest.raises(ValueError, match="environment variable"):
        HTTPVLM(config).infer("inventory", "", [image], {}, tmp_path / "trace")
    assert not state["requests"]


def test_explicit_secret_file_is_literal_and_preserves_existing_environment(tmp_path, monkeypatch):
    from roboenv_perception.secret_env import load_secret_env

    monkeypatch.delenv("VLM_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "existing-fixture-value")
    marker = tmp_path / "must-not-exist"
    secret = tmp_path / "secret.env"
    value = f"$(touch {marker})"
    secret.write_text(f'VLM_API_KEY="{value}"\nDASHSCOPE_API_KEY=new-fixture-value\n')
    load_secret_env(secret)
    import os

    assert os.environ["VLM_API_KEY"] == value
    assert os.environ["DASHSCOPE_API_KEY"] == "existing-fixture-value"
    assert not marker.exists()


def test_bad_secret_file_error_never_echoes_value(tmp_path):
    from roboenv_perception.secret_env import load_secret_env

    secret = tmp_path / "secret.env"
    secret.write_text('VLM_API_KEY="private-fixture-value\n')
    with pytest.raises(ValueError) as caught:
        load_secret_env(secret)
    assert "private-fixture-value" not in str(caught.value)
