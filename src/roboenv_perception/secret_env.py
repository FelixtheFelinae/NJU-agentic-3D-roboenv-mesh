"""Explicit secret-file loading without shell evaluation or value disclosure."""

import os
from pathlib import Path


def load_secret_env(path: Path):
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or name not in {"VLM_API_KEY", "DASHSCOPE_API_KEY"}:
            raise ValueError(f"invalid secret file assignment on line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"unclosed secret file quote on line {number}")
            value = value[1:-1]
        if value:
            os.environ.setdefault(name, value)
