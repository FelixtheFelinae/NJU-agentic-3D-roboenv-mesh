"""Immutable run directories and portable, hash-bound downstream inputs."""

import contextlib
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ..io import read_json, relative_file, sha256, write_json
from .contracts import VERSION


def verify_run(root: Path, entry: str):
    root = root.resolve()
    run = read_json(root / "run.json")
    if run.get("status") != "completed":
        raise ValueError(f"source run is not completed: {root}")
    index_path = root / "artifacts.json"
    if sha256(index_path) != run.get("artifact_index_sha256"):
        raise ValueError("source artifact index changed")
    index = read_json(index_path)
    for ref, digest in index.items():
        path = relative_file(root, ref)
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"source artifact changed or missing: {ref}")
    if entry not in index:
        raise ValueError(f"source entry is not hash-bound: {entry}")
    return read_json(relative_file(root, entry))


@contextlib.contextmanager
def new_run(output: Path, stage: str, sources: dict):
    output.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": VERSION,
        "stage": stage,
        "status": "running",
        "sources": sources,
        "started_unix": time.time(),
        "validation": "not_evaluated",
        "reconstruction_ready": False,
    }
    write_json(output / "run.json", record)
    try:
        yield record
        index = {
            str(p.relative_to(output)): sha256(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
            and p.relative_to(output).as_posix() not in {"artifacts.json", "run.json"}
        }
        write_json(output / "artifacts.json", index)
        record.update(status="completed", artifact_index_sha256=sha256(output / "artifacts.json"))
    except BaseException as error:
        record.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        record["ended_unix"] = time.time()
        write_json(output / "run.json", record)


def source_identity(root: Path):
    return {
        "path_at_creation": str(root.resolve()),
        "artifact_index_sha256": sha256(root / "artifacts.json"),
    }


def copy_ref(source: Path, ref: str, output: Path, destination: str):
    src = relative_file(source, ref)
    dst = relative_file(output, destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return destination


def read_mask(path: Path, size=None):
    with Image.open(path) as image:
        array = np.asarray(image)
    if array.ndim != 2 or not np.isin(array, [0, 255]).all():
        raise ValueError(f"mask must be binary single-channel 0/255: {path}")
    if size is not None and array.shape != (size[1], size[0]):
        raise ValueError(f"mask/image size mismatch: {path}")
    return array > 0


def perception_frames(root: Path, scene: dict):
    if scene.get("kind") == "video":
        return scene["frames"]
    return [
        {
            **scene["frame"],
            "objects_ref": scene["objects_ref"],
            "background_ref": scene["background_ref"],
            "timestamp_seconds": 0.0,
        }
    ]


def cv2_module():
    try:
        import cv2
    except ImportError:
        raise RuntimeError(
            "Install construction extras on Linux: pip install -e '.[construction]'"
        ) from None
    return cv2
