"""Durable task history and a bounded, read-only index of existing experiments."""

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..artifacts.registry import artifact_type

MEDIA = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".mp4",
    ".webm",
    ".glb",
    ".stl",
    ".ply",
    ".obj",
    ".usd",
    ".usda",
    ".usdc",
}
TEXT = {".json", ".jsonl", ".txt", ".log"}
SKIP_DIRS = {
    "kit-runtime",
    "cache",
    "logs",
    "__pycache__",
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "dsh-home",
    "storages",
    "profiles",
    "sessions",
}
SECRET = re.compile(
    r"(api[_-]?key|authorization|password|credential|secret|access[_-]?token)", re.I
)


def redact(value):
    if isinstance(value, dict):
        return {
            k: "[redacted]" if SECRET.search(k) or k.lower() == "headers" else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", value
        )
    return value


def read_json(path, fallback=None):
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            return fallback
        return json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        return fallback


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_artifact(root, ref):
    root = root.resolve()
    path = (root / ref).resolve()
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or any(p.startswith(".") or p in SKIP_DIRS for p in path.relative_to(root).parts)
        or path.suffix.lower() not in MEDIA | TEXT
        or SECRET.search(path.name)
        or "config" in path.name.lower()
        or "private" in path.name.lower()
        or path.name.endswith(".env")
        or path.name in {"spec.json", "binding.json", "ready.json"}
    ):
        raise ValueError("Artifact is outside the run or is not a viewable artifact")
    return path


def artifact_kind(ref):
    suffix, lower = Path(ref).suffix.lower(), ref.lower()
    if suffix in {".glb", ".stl", ".ply", ".obj", ".usd", ".usda", ".usdc"}:
        return "model"
    if suffix in {".mp4", ".webm"}:
        return "video"
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return (
            "input"
            if any(s in lower for s in ("/inputs/", "original.", "context.", "frame."))
            else "image"
        )
    if any(
        s in lower
        for s in (
            "validation",
            "report",
            "physics",
            "review",
            "delivery",
            "research",
            "evidence",
            "assumption",
            "plan",
        )
    ):
        return "evidence"
    return "log" if suffix in {".log", ".txt", ".jsonl"} else "data"


class Store:
    def __init__(self, root: Path, import_root: Path | None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.import_root = import_root.resolve() if import_root else None
        self.db = self.root / "history.sqlite3"
        self.cache = {}
        self.cache_lock = threading.Lock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task_id TEXT, root TEXT UNIQUE,
                    status TEXT, imported INTEGER, created REAL, prompt TEXT, parent_id TEXT);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, run_id TEXT, data TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS bindings(session_id TEXT PRIMARY KEY, task_id TEXT);
                CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY, task_id TEXT, name TEXT,
                    path TEXT, sha256 TEXT, size INTEGER, created REAL);
                CREATE TABLE IF NOT EXISTS invocations(call_id TEXT PRIMARY KEY, run_id TEXT, session_id TEXT);
                CREATE TABLE IF NOT EXISTS cancelled_calls(session_id TEXT, call_id TEXT, PRIMARY KEY(session_id,call_id));
                CREATE TABLE IF NOT EXISTS selections(session_id TEXT PRIMARY KEY, run_id TEXT, object_id TEXT);
            """)
            db.execute(
                "UPDATE runs SET status='interrupted' WHERE imported=0 AND status IN ('planning','running','stopping','queued')"
            )
        self.discover()

    def connect(self):
        db = sqlite3.connect(self.db, timeout=20)
        db.row_factory = sqlite3.Row
        return db

    def discover(self):
        if not self.import_root or not self.import_root.is_dir():
            return
        with self.connect() as db:
            for root in sorted(self.import_root.iterdir()):
                if (
                    root.is_symlink()
                    or not root.is_dir()
                    or root.name.startswith(".")
                    or self.root.is_relative_to(root)
                ):
                    continue
                state = read_json(root / "state.json", {})
                request = state.get("request", {}) if isinstance(state, dict) else {}
                identity = "history-" + hashlib.sha256(str(root).encode()).hexdigest()[:16]
                title = request.get("task") or root.name
                status = (
                    state.get("status", "historical") if isinstance(state, dict) else "historical"
                )
                if status == "running":
                    status = "unknown"  # Old state alone does not prove a live process.
                db.execute(
                    "INSERT OR IGNORE INTO tasks VALUES(?,?,?)",
                    (identity, title, root.stat().st_mtime),
                )
                db.execute(
                    "INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?,?)",
                    (
                        identity,
                        identity,
                        str(root),
                        status,
                        1,
                        root.stat().st_mtime,
                        request.get("task", ""),
                        None,
                    ),
                )

    def create_task(self, title="新建还原任务"):
        identity = uuid.uuid4().hex[:16]
        with self.connect() as db:
            db.execute("INSERT INTO tasks VALUES(?,?,?)", (identity, title[:160], time.time()))
        return identity

    def tasks(self):
        with self.connect() as db:
            rows = db.execute("""SELECT t.*, (SELECT COUNT(*) FROM runs r WHERE r.task_id=t.id) AS run_count,
                (SELECT status FROM runs r WHERE r.task_id=t.id ORDER BY created DESC LIMIT 1) AS status,
                (SELECT id FROM runs r WHERE r.task_id=t.id ORDER BY created DESC LIMIT 1) AS latest_run
                FROM tasks t ORDER BY t.created DESC""").fetchall()
        return [dict(r) for r in rows]

    def task(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            runs = db.execute(
                "SELECT * FROM runs WHERE task_id=? ORDER BY created DESC", (identity,)
            ).fetchall()
            inputs = db.execute(
                "SELECT id,name,sha256,size FROM inputs WHERE task_id=? ORDER BY created",
                (identity,),
            ).fetchall()
        return {**dict(row), "runs": [dict(r) for r in runs], "inputs": [dict(r) for r in inputs]}

    def bind(self, session_id, task_id=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT task_id FROM bindings WHERE session_id=?", (session_id,)
            ).fetchone()
            if task_id is None:
                if old:
                    task_id = old["task_id"]
                else:
                    task_id = uuid.uuid4().hex[:16]
                    db.execute(
                        "INSERT INTO tasks VALUES(?,?,?)", (task_id, "新建还原任务", time.time())
                    )
            if not db.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise KeyError(task_id)
            db.execute("INSERT OR REPLACE INTO bindings VALUES(?,?)", (session_id, task_id))
        return self.task(task_id)

    def add_input(self, task_id, name, path):
        self.task(task_id)
        identity = uuid.uuid4().hex[:16]
        with self.connect() as db:
            db.execute(
                "INSERT INTO inputs VALUES(?,?,?,?,?,?,?)",
                (
                    identity,
                    task_id,
                    name,
                    str(path),
                    digest(path),
                    path.stat().st_size,
                    time.time(),
                ),
            )
        return identity

    def input_files(self, task_id, ids):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM inputs WHERE task_id=?", (task_id,)).fetchall()
        selected = [dict(row) for row in rows if row["id"] in ids]
        if len(selected) != len(ids) or not ids:
            raise ValueError("输入必须是当前任务已上传的文件 ID")
        for row in selected:
            if digest(Path(row["path"])) != row["sha256"]:
                raise ValueError("上传输入发生变化，请重新上传")
        return selected

    def run(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        return dict(row)

    def new_run(self, task_id, prompt, parent_id=None):
        self.task(task_id)
        identity = uuid.uuid4().hex[:16]
        root = self.root / "runs" / identity
        root.mkdir(parents=True, mode=0o700)
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
                (identity, task_id, str(root), "queued", 0, time.time(), prompt, parent_id),
            )
            db.execute(
                "UPDATE tasks SET title=? WHERE id=? AND title='新建还原任务'",
                (prompt[:80], task_id),
            )
        return self.run(identity)

    def status(self, identity, status):
        with self.connect() as db:
            db.execute("UPDATE runs SET status=? WHERE id=?", (status, identity))

    def event(self, run_id, data):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(run_id,data,created) VALUES(?,?,?)",
                (run_id, json.dumps(redact(data), ensure_ascii=False), time.time()),
            )

    def artifacts(self, run_id, refresh=False):
        run = self.run(run_id)
        with self.cache_lock:
            cached = self.cache.get(run_id)
            ttl = 4 if run["status"] in {"running", "planning"} else 60
            if not refresh and cached and time.time() - cached[0] < ttl:
                return cached[1]
        root, rows, truncated = Path(run["root"]), [], False
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if d not in SKIP_DIRS
                and not d.startswith(".")
                and not (Path(folder) / d).is_symlink()
            )
            for name in sorted(files):
                ref = str((Path(folder) / name).relative_to(root))
                try:
                    path = safe_artifact(root, ref)
                    stat = path.stat()
                except (ValueError, OSError):
                    continue
                rows.append(
                    {
                        "id": hashlib.sha256(ref.encode()).hexdigest()[:24],
                        "ref": ref,
                        "name": name,
                        "kind": artifact_kind(ref),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "revision": str(Path(ref).parent),
                        "type": artifact_type(path),
                    }
                )
                if len(rows) >= 4000:
                    truncated = True
                    break
            if truncated:
                break
        result = {"items": rows, "truncated": truncated}
        with self.cache_lock:
            self.cache[run_id] = (time.time(), result)
        return result

    def detail(self, identity):
        run = self.run(identity)
        root = Path(run["root"])
        state = read_json(root / "output/state.json", {}) or read_json(root / "state.json", {})
        output = root / "output" if (root / "output/state.json").is_file() else root
        delivery = read_json(output / "delivery/delivery.json", {}) or {}
        scope = read_json(output / "scope/scope_plan.json", {}) or delivery.get("object_scope", {})
        assets = delivery.get("assets", [])
        blocked = [item for item in assets if item.get("status") == "blocked"]
        outcome = {
            "validation_status": delivery.get("status") or state.get("status"),
            "physics_status": delivery.get("physics", {}).get("status"),
            "scene_usd": delivery.get("scene_usd"),
            "blocked_assets": len(blocked),
            "blockers": [],
        }
        if scope.get("all_resolved") is False:
            outcome["blockers"].append(
                {
                    "stage": "object_scope",
                    "reason": "部件划分尚未通过独立审核，相关资产的建模被阻塞。",
                    "report_ref": str((output / "scope/scope_plan.json").relative_to(root)),
                }
            )
        for reason in dict.fromkeys(item.get("reason") for item in blocked if item.get("reason")):
            if scope.get("all_resolved") is not False:
                outcome["blockers"].append({"stage": "assets", "reason": reason})
        with self.connect() as db:
            events = [
                {"id": r["id"], "at": r["created"], **json.loads(r["data"])}
                for r in db.execute(
                    "SELECT * FROM events WHERE run_id=? ORDER BY id", (identity,)
                ).fetchall()
            ]
        for journal in (root / "events.jsonl", root / "plugin-events.jsonl"):
            if journal.is_file():
                with journal.open(errors="replace") as stream:
                    for i, line in enumerate(stream):
                        if i >= 6000:
                            break
                        try:
                            events.append(redact(json.loads(line)))
                        except ValueError:
                            continue
        # Legacy stage snapshots remain explicitly distinguished from live call records.
        if not events and state:
            for name, row in state.get("stages", {}).items():
                events.append(
                    {
                        "name": name,
                        "event": row.get("status"),
                        "at": row.get("started_unix"),
                        "historical": True,
                        "output": row.get("path"),
                        "error": row.get("error"),
                    }
                )
        events.sort(
            key=lambda event: event.get("at") if isinstance(event.get("at"), (float, int)) else 0
        )
        from ..components.sources import execution_record
        from .observations import agent_observation

        component = execution_record(root / "output/component.json")
        component_summary = (
            {
                k: component.get(k)
                for k in (
                    "operation",
                    "exports",
                    "object_id",
                    "lineage",
                    "validation_status",
                    "content_sha256",
                    "summary",
                )
            }
            if component
            else None
        )
        return {
            **run,
            "state": redact(state),
            "component": redact(component_summary),
            "agent_observation": agent_observation(run),
            "outcome": redact(outcome),
            "events": events[-1500:],
            "artifacts": self.artifacts(identity),
            "plan": redact(read_json(root / "plan.json")),
            "provenance": read_json(root / "provenance.json"),
            "trace_complete": False if run["imported"] else None,
        }
