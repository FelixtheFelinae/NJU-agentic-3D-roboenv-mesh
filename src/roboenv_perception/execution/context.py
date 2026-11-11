"""Append-only component events. No model, orchestration or GUI policy lives here."""

import contextvars
import functools
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_context = contextvars.ContextVar("envcreator_execution", default=None)
_parent = contextvars.ContextVar("envcreator_operation", default=None)


def emit(event, **data):
    context = _context.get()
    if context is None:
        return
    row = {
        "at": time.time(),
        "run_id": context["run_id"],
        "call_id": context.get("call_id"),
        "event": event,
        "parent_operation_id": _parent.get(),
        **data,
    }
    encoded = (json.dumps(row, ensure_ascii=False, allow_nan=False, default=str) + "\n").encode()
    # One append write avoids interleaving records from threads and subprocesses.
    fd = os.open(context["journal"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


@contextmanager
def execution(root: Path, run_id, call_id=None):
    token = _context.set(
        {"journal": str(root / "events.jsonl"), "run_id": run_id, "call_id": call_id}
    )
    try:
        yield
    finally:
        _context.reset(token)


@contextmanager
def span(name, **data):
    identity = uuid.uuid4().hex[:16]
    upstream = _parent.get()
    emit("started", name=name, operation_id=identity, **data)
    token = _parent.set(identity)
    started = time.monotonic()
    try:
        yield identity
    except BaseException as error:
        emit(
            "failed",
            name=name,
            operation_id=identity,
            parent_operation_id=upstream,
            elapsed_seconds=time.monotonic() - started,
            error=f"{type(error).__name__}: {error}",
        )
        raise
    else:
        emit(
            "completed",
            name=name,
            operation_id=identity,
            parent_operation_id=upstream,
            elapsed_seconds=time.monotonic() - started,
        )
    finally:
        _parent.reset(token)


def traced(name):
    def decorate(func):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            label = name(args, kwargs) if callable(name) else name
            with span(label):
                return func(*args, **kwargs)

        return wrapped

    return decorate
