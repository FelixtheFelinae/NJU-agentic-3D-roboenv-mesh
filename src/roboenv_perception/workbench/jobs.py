"""Owned subprocess jobs; the native DSH tool owns waiting and cancellation."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from .store import digest, read_json


class Jobs:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.lock = threading.RLock()
        self.entries = {}
        self.cancelled_calls = set()
        self.slots = threading.BoundedSemaphore(settings.max_concurrent)
        self.closing = False

    def active_ids(self):
        with self.lock:
            return [key for key, row in self.entries.items() if not row["done"].is_set()]

    def process_ids(self):
        result = {}
        with self.lock:
            entries = list(self.entries.items())
        for key, row in entries:
            child = row.get("process")
            if child and child.poll() is None:
                try:
                    parent = psutil.Process(child.pid)
                    for process in [parent, *parent.children(recursive=True)]:
                        result[process.pid] = key
                except psutil.Error:
                    pass
        return result

    def submit(
        self,
        task_id,
        task,
        inputs,
        call_id,
        session_id,
        parent_id=None,
        *,
        operation=None,
        source_runs=None,
        params=None,
        object_id=None,
        origin="agent",
    ):
        with self.lock:
            if self.closing:
                raise ValueError("工作台正在关闭")
            with self.store.connect() as db:
                cancelled = db.execute(
                    "SELECT 1 FROM cancelled_calls WHERE session_id=? AND call_id=?",
                    (session_id, call_id),
                ).fetchone()
            if cancelled or (session_id, call_id) in self.cancelled_calls:
                raise ValueError("此原生调用已取消，未启动生成")
            with self.store.connect() as db:
                old = db.execute("SELECT * FROM invocations WHERE call_id=?", (call_id,)).fetchone()
            if old:
                if old["session_id"] != session_id:
                    raise ValueError("调用身份不匹配")
                return self.store.run(old["run_id"])
            from ..components.catalog import CATALOG, WORKFLOW_OPERATIONS, requirements
            from ..components.sources import bind_source, execution_record

            if operation in WORKFLOW_OPERATIONS:
                from ..harness.workflow import Decision, Options

                if source_runs or object_id:
                    raise ValueError("工作流在内部绑定产物，请通过决策参数选择证据和对象")
                if operation == "workflow_start":
                    params = Options.model_validate(params or {}).model_dump(exclude_none=True)
                else:
                    if inputs or not parent_id:
                        raise ValueError("恢复流程须提供初始工作流 ID，不重新上传输入")
                    Decision.model_validate(params or {})
                    anchor = self.store.run(parent_id)
                    if anchor["task_id"] != task_id:
                        raise ValueError("工作流必须属于当前任务")
                    original = execution_record(Path(anchor["root"]) / "spec.json")
                    if anchor["imported"] or original.get("operation") != "workflow_start":
                        raise ValueError("请提供初始工作流的 ID")
                    if anchor["status"] in {"queued", "running", "stopping"}:
                        raise ValueError("初始工作流调用仍在运行")
                    task = original["task"]

            missing = (
                requirements(operation, self.settings)
                if operation
                else self.settings.generation_missing()
            )
            if missing:
                raise ValueError("生成环境未配置：" + ", ".join(missing))
            media = self.store.input_files(task_id, inputs) if inputs else []
            declared = operation == "workflow_start" and (params or {}).get("declared_design") is not None
            if declared and media:
                raise ValueError("已知规格场景与媒体输入应分别提交")
            if (not operation or operation in {"ingest", "perceive", "workflow_start"}) and not media and not declared:
                raise ValueError("请选择已上传的输入")
            sources, lineage = {}, {}
            if operation:
                required = CATALOG[operation][1]
                source_runs = source_runs or {}
                if not set(required) <= set(source_runs):
                    raise ValueError("缺少上游结果：" + ", ".join(set(required) - set(source_runs)))
                optional = {
                    "blueprint": {"previous", "research", "geometric_verification"},
                    "geometry": {"previous", "research", "geometric_verification"},
                    "geometry_audit": {"evidence", "research"},
                    "build": {"assembly", "evidence", "research", "blueprint_review"},
                    "render": {"evidence", "research"},
                    "export": {"physics", "task_review"},
                    "task_review": {"scene"},
                    "gaussian": {"checkpoint"},
                }.get(operation, set())
                if operation == "assets_merge":
                    optional = {k for k in source_runs if k.startswith("assets_")}
                    if not optional:
                        raise ValueError("至少提供一个 assets_ 前缀的资产来源")
                if set(source_runs) - set(required) - optional:
                    raise ValueError("包含此组件不使用的依赖")
                for role, identity in source_runs.items():
                    source = self.store.run(identity)
                    if source["task_id"] != task_id:
                        raise ValueError("依赖必须属于当前任务；可先打开相应历史任务")
                    if (
                        source["status"]
                        in {
                            "queued",
                            "running",
                            "stopping",
                            "failed",
                            "cancelled",
                            "interrupted",
                            "timeout",
                        }
                        and role != "checkpoint"
                    ):
                        raise ValueError("上游操作尚未完成")
                    source_role = (
                        "assets"
                        if operation == "assets_merge" and role.startswith("assets_")
                        else ("blueprint" if operation == "blueprint" else "recipe")
                        if role == "previous"
                        else required.get(role, role)
                    )
                    sources[role] = bind_source(source, source_role, object_id)
                    metadata = read_json(Path(source["root"]) / "output/component.json", {}) or {}
                    if role not in {"previous", "checkpoint"}:
                        for key, value in {
                            **metadata.get("lineage", {}),
                            source_role: identity,
                        }.items():
                            if operation == "assets_merge" and key in {
                                "blueprint",
                                "blueprint_review",
                                "recipe",
                                "assembly",
                                "built",
                                "assets",
                                "renders",
                            }:
                                continue
                            if key in lineage and lineage[key] != value:
                                raise ValueError("上游版本不一致，请使用同一依赖链的结果")
                            lineage[key] = value
            videos = [
                row
                for row in media
                if Path(row["name"]).suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}
            ]
            if videos and (len(videos) != 1 or len(media) != 1):
                raise ValueError("请选择一段视频，或一组图片，不能混合")
            parent_id = parent_id or next((r["id"] for r in self.store.task(task_id)["runs"]), None)
            if parent_id and self.store.run(parent_id)["task_id"] != task_id:
                raise ValueError("父版本不属于当前任务")
            run = self.store.new_run(task_id, task, parent_id)
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO invocations VALUES(?,?,?)", (call_id, run["id"], session_id)
                )
            root = Path(run["root"])
            spec = {
                "task": task,
                "inputs": media,
                "video": bool(videos),
                "settings": self.settings.model_dump(mode="json"),
                "call_id": call_id,
                "session_id": session_id,
                "run_id": run["id"],
                "operation": operation,
                "sources": sources,
                "params": params or {},
                "object_id": object_id,
                "origin": origin,
                "lineage": lineage,
            }
            if operation in WORKFLOW_OPERATIONS:
                spec["workflow_id"] = run["id"] if operation == "workflow_start" else parent_id
            (root / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2))
            provenance = {
                "session_id": session_id,
                "call_id": call_id,
                "parent_run_id": parent_id,
                "inputs": [{k: row[k] for k in ("id", "name", "sha256")} for row in media],
                "observation": "EnvCreator configured observations; inspect per-stage evidence",
                "operation": operation,
                "sources": sources,
                "origin": origin,
                "source_files": {
                    str(p.relative_to(Path(__file__).parents[1])): digest(p)
                    for p in Path(__file__).parents[1].rglob("*.py")
                },
            }
            (root / "provenance.json").write_text(json.dumps(provenance, indent=2))
            entry = {
                "cancel": threading.Event(),
                "done": threading.Event(),
                "process": None,
                "descendants": {},
            }
            self.entries[run["id"]] = entry
            self.store.event(
                run["id"],
                {
                    "event": "queued",
                    "name": "envcreator_" + operation if operation else "envcreator_generate",
                    "call_id": call_id,
                    "session_id": session_id,
                },
            )
            threading.Thread(target=self._execute, args=(run, entry), daemon=True).start()
            return run

    @staticmethod
    def _signal(child, sig):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass

    @staticmethod
    def _remember_children(child, entry):
        try:
            for process in psutil.Process(child.pid).children(recursive=True):
                entry["descendants"][process.pid] = process.create_time()
        except psutil.Error:
            pass

    @staticmethod
    def _signal_children(entry, sig):
        for pid, created in list(entry.get("descendants", {}).items()):
            try:
                process = psutil.Process(pid)
                if process.create_time() == created:
                    process.send_signal(sig)
            except psutil.Error:
                pass

    def _execute(self, run, entry):
        acquired = False
        root = Path(run["root"])
        try:
            while not entry["cancel"].is_set():
                if self.slots.acquire(timeout=0.2):
                    acquired = True
                    break
            if not acquired or entry["cancel"].is_set():
                self.store.status(run["id"], "cancelled")
                return
            with (root / "worker.log").open("w") as log:
                child = subprocess.Popen(
                    self.command(root), stdout=log, stderr=subprocess.STDOUT, start_new_session=True
                )
                with self.lock:
                    entry["process"] = child
                self.store.status(run["id"], "running")
                self.store.event(
                    run["id"], {"event": "started", "name": "worker", "pid": child.pid}
                )
                started = time.monotonic()
                deadline = self.settings.max_wall_seconds + 30
                reason = None
                while child.poll() is None:
                    self._remember_children(child, entry)
                    if entry["cancel"].wait(0.2):
                        reason = "cancelled"
                        break
                    if time.monotonic() - started > deadline:
                        reason = "timeout"
                        break
                if reason:
                    self.store.status(run["id"], "stopping")
                    self._signal(child, signal.SIGTERM)
                    self._signal_children(entry, signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                # Reap simulator descendants even if their parent has already exited.
                self._signal(child, signal.SIGKILL)
                self._signal_children(entry, signal.SIGKILL)
                child.wait(timeout=10)
                state = read_json(root / "output/state.json", {})
                status = reason or (
                    "completed"
                    if child.returncode in {0, 2} and state.get("completed_unix")
                    else "failed"
                )
                self.store.status(run["id"], status)
                self.store.event(
                    run["id"],
                    {
                        "event": status,
                        "name": "worker",
                        "exit_code": child.returncode,
                        "validation_status": state.get("status"),
                        "error": state.get("error"),
                    },
                )
            self.store.artifacts(run["id"], refresh=True)
        except Exception as exc:
            self.store.status(run["id"], "failed")
            self.store.event(
                run["id"],
                {"event": "failed", "name": "worker", "error": f"{type(exc).__name__}: {exc}"},
            )
        finally:
            child = entry.get("process")
            if child and child.poll() is None:
                self._remember_children(child, entry)
                self._signal(child, signal.SIGKILL)
                self._signal_children(entry, signal.SIGKILL)
                child.wait(timeout=10)
            if acquired:
                self.slots.release()
            entry["done"].set()

    def command(self, root):
        return [sys.executable, "-u", "-m", "roboenv_perception.workbench.worker", str(root)]

    def cancel_invocation(self, session_id, call_id):
        with self.lock:
            self.cancelled_calls.add((session_id, call_id))
            with self.store.connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO cancelled_calls VALUES(?,?)", (session_id, call_id)
                )
                row = db.execute(
                    "SELECT * FROM invocations WHERE call_id=? AND session_id=?",
                    (call_id, session_id),
                ).fetchone()
            return (
                self.cancel(row["run_id"])
                if row
                else {"accepted": True, "status": "cancelled_before_start"}
            )

    def cancel(self, identity):
        run = self.store.run(identity)
        with self.lock:
            entry = self.entries.get(identity)
            if not entry or entry["done"].is_set():
                return {"status": run["status"], "accepted": False}
            entry["cancel"].set()
            self.store.status(identity, "stopping")
        return {"status": "stopping", "accepted": True}

    def close(self):
        self.closing = True
        for identity in self.active_ids():
            self.cancel(identity)
        with self.lock:
            entries = list(self.entries.values())
        for row in entries:
            row["done"].wait(8)
