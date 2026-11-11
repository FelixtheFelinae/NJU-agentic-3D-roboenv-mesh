import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from roboenv_perception.workbench.config import Settings
from roboenv_perception.workbench.jobs import Jobs
from roboenv_perception.workbench.server import create_app
from roboenv_perception.workbench.store import Store, safe_artifact


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(output, format="PNG")
    return output.getvalue()


def settings(tmp_path):
    source = tmp_path / "config.toml"
    source.write_text("")
    return Settings(
        dsh_root=tmp_path,
        state_root=tmp_path / "studio",
        import_root=tmp_path / "outputs",
        perception_config=source,
        construction_config=source,
        artisan_python=Path(sys.executable),
        physics=False,
    )


def test_native_auth_upload_scope_and_artifact_provenance(tmp_path):
    cfg = settings(tmp_path)
    historical = cfg.import_root / "experiment"
    historical.mkdir(parents=True)
    (historical / "state.json").write_text(
        json.dumps({"status": "running", "request": {"task": "old task"}, "stages": {}})
    )
    (historical / "render.png").write_bytes(image_bytes())
    (historical / "review.json").write_text(
        json.dumps(
            {
                "api_key": "never-display",
                "headers": {"Authorization": "private"},
                "status": "unverified",
            }
        )
    )
    (historical / "dsh-home").mkdir()
    (historical / "dsh-home" / "private.json").write_text('{"key":"private"}')
    outside = tmp_path / "outside.png"
    outside.write_bytes(image_bytes())
    (historical / "escape.png").symlink_to(outside)
    app = create_app(cfg, "test-token")
    with TestClient(app) as client:
        assert client.get("/api/tasks").status_code == 401
        client.headers["authorization"] = "Bearer test-token"
        tasks = client.get("/api/tasks").json()
        old = tasks[0]
        assert old["status"] == "unknown"
        detail = client.get("/api/runs/" + old["latest_run"]).json()
        refs = {v["ref"] for v in detail["artifacts"]["items"]}
        assert "render.png" in refs
        assert "escape.png" not in refs
        assert not any("dsh-home" in ref for ref in refs)
        artifact = next(v for v in detail["artifacts"]["items"] if v["name"] == "render.png")
        endpoint = "/api/runs/" + old["latest_run"] + "/artifacts/" + artifact["id"]
        assert client.get(endpoint).content == image_bytes()
        assert len(client.get(endpoint + "?metadata=true").json()["sha256"]) == 64
        review = next(v for v in detail["artifacts"]["items"] if v["name"] == "review.json")
        text = client.get("/api/runs/" + old["latest_run"] + "/artifacts/" + review["id"]).text
        assert "never-display" not in text and "private" not in text
        task = client.post("/api/bind", json={"session_id": "native-session"}).json()
        upload = client.post(
            "/api/tasks/" + task["id"] + "/upload",
            files={"file": ("source.png", image_bytes(), "image/png")},
        )
        assert upload.status_code == 200
        input_id = upload.json()["id"]
        context = client.post(
            "/api/native/invoke",
            json={
                "operation": "context",
                "session_id": "native-session",
                "call_id": "context-call",
            },
        ).json()
        assert context["task"]["inputs"][0]["id"] == input_id
        other = client.post("/api/bind", json={"session_id": "other-session"}).json()
        with pytest.raises(ValueError, match="当前任务"):
            app.state.store.input_files(other["id"], [input_id])
        app.state.store.input_files(task["id"], [input_id])
        input_path = next((cfg.state_root / "inputs" / task["id"]).iterdir())
        input_path.write_bytes(b"changed")
        with pytest.raises(ValueError, match="发生变化"):
            app.state.store.input_files(task["id"], [input_id])


def test_session_binding_is_atomic_and_missing_artifact_stays_closed(tmp_path):
    store = Store(tmp_path / "state", None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: store.bind("same-session")["id"], range(16)))
    assert len(set(values)) == 1
    assert len(store.tasks()) == 1
    with pytest.raises(ValueError):
        safe_artifact(tmp_path, "../file.png")


def wait_until(test, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for worker")


def test_cancel_reaps_actual_worker_and_descendant_and_preserves_record(tmp_path):
    cfg = settings(tmp_path)
    store = Store(cfg.state_root, None)
    task = store.bind("native-session")
    source = tmp_path / "image.png"
    source.write_bytes(image_bytes())
    input_id = store.add_input(task["id"], "image.png", source)

    class SleepingJobs(Jobs):
        def command(self, root):
            code = "import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);Path(sys.argv[1]).write_text(str(p.pid));time.sleep(120)"
            return [sys.executable, "-c", code, str(root / "descendant.pid")]

    jobs = SleepingJobs(store, cfg)
    run = jobs.submit(task["id"], "test generation", [input_id], "native-call", "native-session")
    root = Path(run["root"])
    try:
        wait_until(lambda: (root / "descendant.pid").is_file())
        descendant = int((root / "descendant.pid").read_text())
        assert jobs.process_ids()[descendant] == run["id"]
        # A retried delivery of the same native invocation cannot spawn a second worker.
        assert (
            jobs.submit(task["id"], "test generation", [input_id], "native-call", "native-session")[
                "id"
            ]
            == run["id"]
        )
        assert len(store.task(task["id"])["runs"]) == 1
        assert jobs.cancel_invocation("native-session", "native-call")["accepted"]
        wait_until(lambda: not jobs.active_ids())
        assert store.run(run["id"])["status"] == "cancelled"
        assert (
            not psutil.pid_exists(descendant)
            or psutil.Process(descendant).status() == psutil.STATUS_ZOMBIE
        )
        assert (root / "worker.log").is_file()
        assert store.detail(run["id"])["events"][-1]["event"] == "cancelled"
        jobs.cancel_invocation("native-session", "before-start")
        with pytest.raises(ValueError, match="已取消"):
            jobs.submit(task["id"], "test generation", [input_id], "before-start", "native-session")
    finally:
        jobs.close()


def test_completed_process_does_not_upgrade_physical_verification(tmp_path):
    cfg = settings(tmp_path)
    store = Store(cfg.state_root, None)
    task = store.bind("native-session")
    source = tmp_path / "image.png"
    source.write_bytes(image_bytes())
    input_id = store.add_input(task["id"], "image.png", source)

    class FixtureJobs(Jobs):
        def command(self, root):
            code = """
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
(p / 'scope').mkdir(parents=True)
(p / 'delivery').mkdir()
(p / 'state.json').write_text(json.dumps({'status': 'unverified', 'completed_unix': 1}))
(p / 'scope/scope_plan.json').write_text(json.dumps({'all_resolved': False}))
(p / 'delivery/delivery.json').write_text(json.dumps({
    'status': 'unverified', 'scene_usd': None,
    'physics': {'status': 'not_run'},
    'assets': [{'status': 'blocked', 'reason': 'object scope plan is unresolved'}]
}))
sys.exit(2)
"""
            return [sys.executable, "-c", code, str(root / "output")]

    jobs = FixtureJobs(store, cfg)
    try:
        run = jobs.submit(
            task["id"], "test generation", [input_id], "native-call", "native-session"
        )
        wait_until(lambda: not jobs.active_ids())
        detail = store.detail(run["id"])
        assert detail["status"] == "completed"
        assert detail["state"]["status"] == "unverified"
        assert detail["events"][-1]["validation_status"] == "unverified"
        assert detail["outcome"]["validation_status"] == "unverified"
        assert detail["outcome"]["physics_status"] == "not_run"
        assert detail["outcome"]["blocked_assets"] == 1
        assert detail["outcome"]["blockers"][0]["report_ref"] == "output/scope/scope_plan.json"
    finally:
        jobs.close()
def test_native_context_summaries_preserve_identity_without_repeating_history_or_hash_dags():
    import json

    from roboenv_perception.workbench.observations import provenance_summary, task_summary

    task = {'id': 'task', 'inputs': [{'id': 'video'}],
            'runs': [{'id': str(i), 'status': 'completed', 'prompt': 'old text'*1000}
                     for i in range(80)]}
    original_size = len(json.dumps(task))
    context = task_summary(task)
    assert context['run_count'] == 80
    assert context['history_truncated']
    assert len(context['runs']) == 12
    assert context['inputs'] == task['inputs']
    assert len(json.dumps(context)) < original_size/20
    provenance = {'call_id': 'call', 'session_id': 'session', 'operation': 'reference_code',
                  'sources': {'reference': {'run_id': 'source', 'role': 'reference', 'ref': 'output/reference',
                                             'files': {str(i): 'a'*64 for i in range(3000)}}},
                  'source_files': {str(i): 'b'*64 for i in range(3000)}}
    result = provenance_summary(provenance)
    assert result['call_id'] == 'call'
    assert result['sources']['reference']['run_id'] == 'source'
    assert result['source_file_count'] == 3000
    assert len(json.dumps(result)) < 1000
    assert len(provenance['source_files']) == 3000




def test_workflow_native_subprocess_identity_snapshot_and_task_scope(tmp_path):
    """Real worker and real PyAV overview; no VLM/DSH-model or GPU request."""
    import av

    from roboenv_perception.artifacts.registry import tree_index
    from roboenv_perception.io import read_json

    video = tmp_path / "source.mp4"
    with av.open(str(video), "w") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(20):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 48), (i * 8, 100, 30)))
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    cfg = settings(tmp_path)
    app = create_app(cfg, "test-token")
    with TestClient(app) as client:
        client.headers["authorization"] = "Bearer test-token"
        task = client.post("/api/bind", json={"session_id": "workflow-session"}).json()
        upload = client.post("/api/tasks/" + task["id"] + "/upload",
                             files={"file": ("source.mp4", video.read_bytes(), "video/mp4")}).json()
        request = {
            "operation": "workflow_start", "session_id": "workflow-session",
            "call_id": "start-call", "task": "reconstruct laptop", "input_ids": [upload["id"]],
        }
        start = client.post("/api/native/invoke", json=request).json()
        wait_until(lambda: app.state.store.run(start["id"])["status"] not in {"queued", "running"})
        detail = app.state.store.detail(start["id"])
        assert detail["status"] == "completed", detail
        summary = detail["agent_observation"]["data"]["workflow"]
        assert summary["phase"] == "sampling" and summary["revision"] == 0
        assert summary["workflow_id"] == start["id"] and summary["previews"] == ["source-video.jpg"]
        frozen = tree_index(Path(start["root"]) / "output")
        assert client.post("/api/native/invoke", json=request).json()["id"] == start["id"]
        view = client.get(f'/api/tasks/{task["id"]}/workspace?run_id={start["id"]}').json()
        assert view["workflow"]["phase"] == "sampling"
        assert view["workflow"]["previews"][0]["artifact"]["run_id"] == start["id"]
        decision = {
            "operation": "workflow_advance", "session_id": "workflow-session", "call_id": "stop-call",
            "parent_id": start["id"], "params": {"action": "stop", "revision": 0, "reason": "no further evidence"},
        }
        stop = client.post("/api/native/invoke", json=decision).json()
        wait_until(lambda: app.state.store.run(stop["id"])["status"] not in {"queued", "running"})
        stopped = app.state.store.detail(stop["id"])
        assert stopped["agent_observation"]["data"]["workflow"]["phase"] == "stopped"
        assert tree_index(Path(start["root"]) / "output") == frozen
        original = read_json(Path(start["root"]) / "workflow/workflow.json")
        assert original["history"][0]["actor"]["call_id"] == "stop-call"
        rejected = client.post("/api/native/invoke", json={**decision, "call_id": "stale-call"}).json()
        wait_until(lambda: app.state.store.run(rejected["id"])["status"] not in {"queued", "running"})
        assert app.state.store.run(rejected["id"])["status"] == "failed"
        assert read_json(Path(start["root"]) / "workflow/workflow.json")["revision"] == 1
        client.post("/api/bind", json={"session_id": "other"})
        cross = client.post("/api/native/invoke", json={**decision, "session_id": "other", "call_id": "cross"})
        assert cross.status_code == 400
        context = client.post("/api/native/invoke", json={
            "operation": "context", "session_id": "workflow-session", "call_id": "context",
        }).json()
        assert {row["id"] for row in context["components"]} == {"workflow_start", "workflow_advance"}
