"""Generate a real, calibrated Gaussian render for the human-facing scene card."""

import subprocess
from pathlib import Path

from ..construction.asset_agents import child_environment
from ..construction.common import verify_run
from ..io import read_json, write_json


def render_overview(gaussian, cameras, output, config, frame_id=None):
    model = verify_run(gaussian, "background_model.json")
    data = verify_run(cameras, "cameras.json")
    frame = (
        next((r for r in data["frames"] if r["frame_id"] == frame_id), None)
        if frame_id
        else data["frames"][len(data["frames"]) // 2]
    )
    if frame is None:
        raise ValueError("没有这个相机视角")
    output.mkdir(parents=True)
    write_json(output / "camera.json", frame)
    command = [
        config.gaussian_python,
        str(Path(__file__).with_name("gaussian_render_worker.py")),
        "--repo",
        config.gaussian_repo,
        "--model",
        str((gaussian / model["model_ref"]).resolve()),
        "--camera",
        str((output / "camera.json").resolve()),
        "--output",
        str((output / "overview.png").resolve()),
    ]
    with (output / "render.log").open("w") as stream:
        result = subprocess.run(
            command,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=child_environment(),
            timeout=min(config.timeout_seconds, 600),
        )
    if result.returncode or not (output / "overview.png").is_file():
        raise RuntimeError("场景总览渲染失败，请查看运行详情")
    write_json(
        output / "overview.json",
        {
            "image_ref": "overview.png",
            "frame_id": frame["frame_id"],
            "kind": "reconstructed_camera_view",
            "panorama": False,
            "scale_status": data.get("scale_status", "unknown"),
            "summary": "实际 3DGS 渲染；使用已重建相机视角，未补全未观测区域。",
            "physics_status": "not_built",
        },
    )
    return read_json(output / "overview.json")
