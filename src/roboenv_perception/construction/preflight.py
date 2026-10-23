"""Read-only readiness checks. Never call an API, load weights or print secrets."""

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..io import load_config
from ..vlm import HTTPVLM
from .contracts import load_construction_config


def check_readiness(
    config_path: Path, perception_path: Path | None = None, media: Path | None = None
):
    config = load_construction_config(config_path)
    checks = []

    def add(name, ok, action, layer="configuration"):
        checks.append(
            {"name": name, "ready": bool(ok), "layer": layer, "action": None if ok else action}
        )

    def vlm_checks(prefix, vlm):
        valid = False
        if vlm.backend == "http":
            try:
                HTTPVLM(vlm)
                valid = True
            except ValueError:
                pass
            add(
                prefix + ".endpoint_and_model",
                valid,
                "Fill the complete request URL, model and API style.",
            )
            names = sorted(
                set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", " ".join(vlm.headers.values())))
            )
            for name in names:
                add(
                    prefix + ".env." + name,
                    bool(os.getenv(name)),
                    f"Set {name} in the process environment; do not commit the secret.",
                    "credentials",
                )
        else:
            add(
                prefix + ".replay_directory",
                bool(vlm.replay_dir) and Path(vlm.replay_dir).is_dir(),
                "Set an existing replay directory. Replay does not establish live readiness.",
            )

    vlm_checks("design.vlm", config.vlm)
    if perception_path:
        perception = load_config(perception_path)
        vlm_checks("perception.vlm", perception.vlm)
        sam = perception.sam
        if sam.backend == "replay":
            add("perception.replay_not_live", False, "Select a live segmentation backend.")
        else:
            module = "sam2" if sam.backend == "grounded_sam2" else "sam3"
            add(
                f"perception.{module}_module",
                importlib.util.find_spec(module) is not None,
                f"Install the official {module} source.",
                "dependency",
            )
            add(
                f"perception.{module}_checkpoint",
                bool(sam.checkpoint) and Path(sam.checkpoint).is_file(),
                f"Fill sam.checkpoint with a local {module} checkpoint.",
            )
            if sam.backend == "grounded_sam2":
                add(
                    "perception.transformers_module",
                    importlib.util.find_spec("transformers") is not None,
                    "Install the grounded-sam2 optional dependencies.",
                    "dependency",
                )
                add(
                    "perception.grounding_snapshot",
                    bool(sam.grounding_model_path)
                    and all(
                        (Path(sam.grounding_model_path) / name).is_file()
                        for name in (
                            "config.json",
                            "preprocessor_config.json",
                            "tokenizer_config.json",
                        )
                    )
                    and any(
                        (Path(sam.grounding_model_path) / name).is_file()
                        for name in ("model.safetensors", "pytorch_model.bin")
                    ),
                    "Set sam.grounding_model_path to a local Grounding DINO snapshot.",
                )
    add(
        "input.media",
        media is not None and media.is_file(),
        "Provide an existing image/video path on this Linux host.",
        "input",
    )
    for module in ["cv2", "plyfile"]:
        add(
            "construction." + module,
            importlib.util.find_spec(module) is not None,
            f"Install {module} in the project Python environment.",
            "dependency",
        )
    add(
        "cameras.colmap",
        shutil.which(config.background.colmap) is not None,
        "Install COLMAP or set background.colmap to an installed executable.",
        "dependency",
    )
    repo = Path(config.background.gaussian_repo)
    add(
        "training.gaussian_repo",
        bool(config.background.gaussian_repo)
        and (repo / "gaussian_renderer/__init__.py").is_file(),
        "Set background.gaussian_repo to the installed classic 3DGS checkout.",
        "dependency",
    )
    probe = (
        "import importlib.util,json; "
        "print(json.dumps({n:importlib.util.find_spec(n) is not None "
        "for n in ['torch','numpy','PIL','pydantic','plyfile','diff_gaussian_rasterization','simple_knn']}))"
    )
    try:
        import json

        process = subprocess.run(
            [config.background.gaussian_python, "-c", probe],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        dependencies = json.loads(process.stdout)
        add(
            "training.python_dependencies",
            all(dependencies.values()),
            "Install missing modules in background.gaussian_python: "
            + ", ".join(k for k, v in dependencies.items() if not v),
            "dependency",
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        add(
            "training.python_dependencies",
            False,
            "Set background.gaussian_python to a usable installed worker Python.",
            "dependency",
        )
    cuda = False
    if importlib.util.find_spec("torch") is not None:
        import torch

        cuda = torch.cuda.is_available()
    add("runtime.cuda", cuda, "Check NVIDIA driver and project PyTorch CUDA access.", "runtime")
    groups = {
        "object_design": [c for c in checks if c["name"].startswith("design.")],
        "live_perception": [
            c for c in checks if c["name"].startswith(("perception.", "input.", "runtime."))
        ],
        "camera_reconstruction": [c for c in checks if c["name"].startswith("cameras.")],
        "gaussian_training": [
            c for c in checks if c["name"].startswith(("training.", "runtime.", "construction."))
        ],
    }
    readiness = {
        name: bool(rows) and all(c["ready"] for c in rows) for name, rows in groups.items()
    }
    if perception_path is None:
        readiness["live_perception"] = False
    return {
        "checks": checks,
        "stage_readiness": readiness,
        "ready": all(readiness.values()),
        "network_requests": False,
        "model_inference": "not_run",
        "cuda_training": "not_run",
        "note": "Dependency checks do not establish model, capture or binary compatibility.",
    }
