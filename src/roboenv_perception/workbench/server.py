"""Private asset/job service. Browser access goes through authenticated native DSH."""

import asyncio
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .jobs import Jobs
from .store import Store, digest, read_json, redact, safe_artifact
from .telemetry import Telemetry


class Bind(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    task_id: str | None = None


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=160)
    call_id: str = Field(min_length=1, max_length=200)
    task: str = Field(default="", max_length=4000)
    input_ids: list[str] = Field(default_factory=list, max_length=32)
    run_id: str | None = None
    parent_id: str | None = None
    sources: dict[str, str] = Field(default_factory=dict, max_length=12)
    params: dict = Field(default_factory=dict)
    object_id: str | None = Field(default=None, max_length=200)


class HumanAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=160)
    operation: Literal["perception_edit", "scope_apply"]
    sources: dict[str, str] = Field(max_length=12)
    params: dict
    task: str = Field(default="按用户选择修正对象", max_length=4000)


class Selection(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    run_id: str | None = None
    object_id: str | None = Field(default=None, max_length=200)


def create_app(settings, token):
    store = Store(settings.state_root, settings.import_root)
    jobs = Jobs(store, settings)
    telemetry = Telemetry(store.root, jobs)

    @asynccontextmanager
    async def lifespan(app):
        telemetry.start()
        yield
        telemetry.stop.set()
        jobs.close()
        if telemetry.thread:
            telemetry.thread.join(timeout=6)

    def auth(request: Request):
        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "Private service authentication required")

    app = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(auth)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store, app.state.jobs, app.state.telemetry = store, jobs, telemetry

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "任务或运行不存在"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/api/health")
    def health():
        return {"status": "ready", "native_model_loop": True}

    @app.get("/api/config")
    def config():
        from ..components.catalog import describe

        summary = read_json(store.root / "model-summary.json", {})
        return {
            "model": summary,
            "generation_missing": settings.generation_missing(),
            "settings": settings.model_dump(mode="json", exclude={"model_config_path", "env_file"}),
            "capabilities": ["context", "generate", "inspect", *[r["id"] for r in describe()]],
            "components": describe(),
            "generation": "versioned internal component tools; native DSH owns orchestration; full Harness remains available",
            "robot_execution": "not_supported",
            "dsh_version": "0.2.1-alpha.1",
        }

    @app.get("/api/tasks")
    def tasks():
        return store.tasks()

    @app.post("/api/discover")
    def discover():
        store.discover()
        return store.tasks()

    @app.post("/api/bind")
    def bind(body: Bind):
        return store.bind(body.session_id, body.task_id)

    @app.get("/api/tasks/{identity}")
    def task(identity: str):
        return store.task(identity)

    @app.get("/api/tasks/{identity}/workspace")
    def human_workspace(identity: str, run_id: str | None = None, object_id: str | None = None):
        from .presentation import workspace

        return workspace(store, identity, run_id, object_id)

    @app.post("/api/selection")
    def selection(body: Selection):
        task = store.bind(body.session_id)
        if body.run_id and store.run(body.run_id)["task_id"] != task["id"]:
            raise ValueError("选择的版本不属于当前任务")
        with store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO selections VALUES(?,?,?)",
                (body.session_id, body.run_id, body.object_id),
            )
        return {"selected": True}

    @app.get("/api/runs/{identity}/events")
    async def stream_events(identity: str, request: Request):
        run = store.run(identity)
        try:
            cursor = max(0, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            cursor = 0
        journal = Path(run["root"]) / "events.jsonl"

        async def stream():
            nonlocal cursor
            last_status = None
            while not await request.is_disconnected():
                if journal.is_file():
                    with journal.open("rb") as handle:
                        handle.seek(min(cursor, journal.stat().st_size))
                        for _ in range(100):
                            line = handle.readline(1024 * 1024)
                            if not line:
                                break
                            if not line.endswith(b"\n"):
                                if len(line) < 1024 * 1024:
                                    break
                                while line and not line.endswith(b"\n"):
                                    line = handle.readline(1024 * 1024)
                                cursor = handle.tell()
                                continue
                            cursor = handle.tell()
                            try:
                                payload = redact(json.loads(line))
                            except ValueError:
                                continue
                            yield f"id: {cursor}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                status = store.run(identity)["status"]
                if status != last_status:
                    yield f"event: status\ndata: {json.dumps({'status': status})}\n\n"
                    last_status = status
                yield ": heartbeat\n\n"
                if status not in {"queued", "running", "stopping"}:
                    break
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/tasks/{identity}/upload")
    async def upload(identity: str, file: UploadFile = File()):
        store.task(identity)
        name = Path(file.filename or "input").name
        suffix = Path(name).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".mkv", ".webm"}:
            raise ValueError("支持 PNG/JPEG/WebP 图片和 MP4/MOV/MKV/WebM 视频")
        directory = store.root / "inputs" / identity
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (uuid.uuid4().hex + suffix)
        total = 0
        try:
            with path.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 512 * 1024 * 1024:
                        raise ValueError("单个输入最大 512 MiB")
                    output.write(chunk)
            if not total:
                raise ValueError("文件为空")
            if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                from PIL import Image

                with Image.open(path) as image:
                    image.verify()
            input_id = store.add_input(identity, name, path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return {"id": input_id, "name": name, "sha256": digest(path), "size": total}

    @app.get("/api/runs/{identity}")
    def run(identity: str):
        return store.detail(identity)

    @app.post("/api/runs/{identity}/cancel")
    def cancel(identity: str):
        return jobs.cancel(identity)

    @app.post("/api/native/invoke")
    def invoke(body: Invocation):
        from ..components.catalog import CATALOG, WORKFLOW_OPERATIONS, describe, requirements
        from .observations import provenance_summary, task_summary

        if body.operation not in {"context", "generate", "inspect"} | set(CATALOG):
            raise ValueError("未注册的组件操作")
        task = store.bind(body.session_id)
        if body.operation == "context":
            from .presentation import workspace

            with store.connect() as db:
                selected = db.execute(
                    "SELECT * FROM selections WHERE session_id=?", (body.session_id,)
                ).fetchone()
            chosen = (
                selected["run_id"]
                if selected
                and selected["run_id"]
                and store.run(selected["run_id"])["task_id"] == task["id"]
                else None
            )
            view = workspace(store, task["id"], chosen, selected["object_id"] if selected else None)
            return {
                "task": task_summary(task),
                "generation_missing": settings.generation_missing(),
                "workflow": "Use workflow_start for a single object and workflow_advance with the original workflow ID as parent_id. Decide only sampling, object identity and structure. Follow allowed_actions/revision. Use resume after an interrupted call. Background and physics are opt-in. Component tools are debug/legacy only.",
                "components": [
                    {**row, "missing_runtime": requirements(row["id"], settings)}
                    for row in describe() if settings.debug_component_tools or row["id"] in WORKFLOW_OPERATIONS
                ],
                "workspace": {
                    k: view[k] for k in ("run_id", "sources", "objects", "decisions", "stale")
                },
                "selected_object": selected["object_id"] if selected else None,
                "active_workflow": {
                    key: view["workflow"].get(key) for key in (
                        "workflow_id", "phase", "revision", "reason", "allowed_actions",
                        "selected_object_id", "physics_status",
                    )
                } if view.get("workflow") else None,
                "revision_feedback": "blueprint/geometry params.feedback accepts up to 16 concrete findings, each <=4000 chars. Keep the original user requirement in task; pass compiler/reviewer findings as data. Read agent_observation for actual object IDs, reviews, failure tails and output_sources. Missing runtime lists are path preflight only.",
                "parameters": {
                    "perception_edit": "frame_id (required for video); merge: instance IDs; exclude: instance IDs; labels: {instance_id: {category, task_role, physical_role, visual_route, rationale}}. A merge unions real masks; never invent masks.",
                    "segment": "optional candidates: full Inventory.objects array with cNNN IDs/category/prompt/bbox_xyxy_norm; reruns actual SAM",
                    "scope_apply": "objects: every evidence asset_id exactly once with action build/background/exclude/unresolved and reason. Explicit selection, not independent acceptance.",
                    "blueprint": "optional full validated blueprint under params.blueprint; always same object/evidence identity",
                    "blueprint_repair": "constraint_ids: 1–4 existing geometric constraint IDs. One bounded internal proposal; other fields remain frozen. Use workflow repair_constraints with reason and constraint_ids on the default surface.",
                    "geometry": "optional full validated recipe under params.recipe; preserve frozen standards",
                    "gaussian": "optional iterations; resume=true plus sources.checkpoint for an existing complete checkpoint; matching original settings required",
                    "overview": "optional frame_id; calibrated actual 3DGS view, no generated panorama",
                    "physics": "optional assembly_validation off/preflight/functional; specialized bench limits remain",
                },
                "follow_up": "Read the selected workspace, choose compatible source run IDs; corrections create immutable versions.",
            }
        if body.operation == "inspect":
            if not body.run_id:
                return task_summary(task)
            run = store.detail(body.run_id)
            if run["task_id"] != task["id"]:
                raise ValueError("记录不属于当前任务")
            return {
                "id": run["id"],
                "status": run["status"],
                "state": run["state"],
                "outcome": run["outcome"],
                "component": (
                    {
                        k: run["component"].get(k)
                        for k in (
                            "operation",
                            "exports",
                            "lineage",
                            "object_id",
                            "validation_status",
                        )
                    }
                    if run["component"]
                    else None
                ),
                "agent_observation": run["agent_observation"],
                "events": run["events"][-20:],
                "artifacts": {
                    "items": run["artifacts"]["items"][:50],
                    "total": len(run["artifacts"]["items"]),
                    "truncated": run["artifacts"]["truncated"]
                    or len(run["artifacts"]["items"]) > 50,
                },
                "provenance": provenance_summary(run["provenance"]),
            }
        if body.operation in {"generate", "workflow_start"} and len(body.task.strip()) < 3:
            raise ValueError("任务描述至少 3 个字符")
        if body.operation == "workflow_advance" and body.task:
            raise ValueError("原任务保持不变；请用决策中的 reason 描述这次修订")
        return jobs.submit(
            task["id"],
            body.task or task["title"],
            body.input_ids,
            body.call_id,
            body.session_id,
            body.parent_id,
            operation=body.operation if body.operation in CATALOG else None,
            source_runs=body.sources,
            params=body.params,
            object_id=body.object_id,
        )

    @app.post("/api/human/action")
    def human_action(body: HumanAction):
        task = store.bind(body.session_id)
        result = jobs.submit(
            task["id"],
            body.task,
            [],
            "human-" + uuid.uuid4().hex,
            body.session_id,
            operation=body.operation,
            source_runs=body.sources,
            params=body.params,
            origin="user",
        )
        store.event(
            result["id"], {"event": "user_action", "name": body.operation, "origin": "user"}
        )
        return result

    @app.post("/api/native/cancel")
    def cancel_call(body: dict):
        return jobs.cancel_invocation(body.get("session_id"), body.get("call_id"))

    @app.get("/api/telemetry")
    def measurements():
        return {"latest": telemetry.latest, "history": list(telemetry.history)}

    @app.get("/api/runs/{identity}/artifacts/{artifact_id}")
    def artifact(identity: str, artifact_id: str, metadata: bool = False):
        run = store.run(identity)
        items = store.artifacts(identity)["items"]
        entry = next((item for item in items if item["id"] == artifact_id), None)
        if entry is None:
            raise KeyError(artifact_id)
        path = safe_artifact(Path(run["root"]), entry["ref"])
        if metadata:
            return {**entry, "sha256": digest(path)}
        if path.suffix in {".json", ".jsonl", ".txt", ".log"}:
            with path.open("rb") as stream:
                if path.suffix == ".log":
                    stream.seek(max(0, path.stat().st_size - 256 * 1024))
                data = stream.read(256 * 1024).decode("utf-8", errors="replace")
            if path.suffix == ".json":
                try:
                    data = json.dumps(redact(json.loads(data)), ensure_ascii=False, indent=2)
                except ValueError:
                    data = str(redact(data))
            else:
                data = str(redact(data))
            for key in ("VLM_API_KEY", "ENVCREATOR_DSH_API_KEY"):
                value = os.environ.get(key)
                if value:
                    data = data.replace(value, "[redacted]")
            return PlainTextResponse(
                data, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            )
        media_type = "model/gltf-binary" if path.suffix == ".glb" else None
        return FileResponse(
            path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"}
        )

    return app


def main():
    import sys

    import uvicorn

    settings = Settings.load(Path(sys.argv[1]))
    token = Path(sys.argv[2]).read_text().strip()
    uvicorn.run(
        create_app(settings, token), host="127.0.0.1", port=int(sys.argv[3]), log_level="warning"
    )


if __name__ == "__main__":
    main()
