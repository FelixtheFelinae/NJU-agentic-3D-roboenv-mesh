"""Bounded offline decode with source timestamps; no live-stream claim."""

import math
from pathlib import Path

from .contracts import VideoConfig
from .io import sha256, write_json


def require_av():
    try:
        import av
    except ImportError:
        raise RuntimeError("Install the server extras: pip install -e '.[server]'") from None
    return av


def decode_video(source: Path, root: Path, config: VideoConfig):
    av = require_av()
    frames = []
    previous_time = None
    next_time = config.start_seconds
    limited = False
    with av.open(str(source)) as container:
        if not container.streams.video:
            raise ValueError("input has no video stream")
        stream = container.streams.video[0]
        if float(stream.metadata.get("rotate", 0)) % 360:
            raise ValueError(
                "rotate the source video upright before processing; no implicit coordinate change"
            )
        origin = (
            float(stream.start_time * stream.time_base) if stream.start_time is not None else None
        )
        width_height = None
        for source_index, frame in enumerate(container.decode(stream)):
            if frame.pts is None or frame.time_base is None:
                raise ValueError(
                    "source video has frames without PTS/time_base; timestamps cannot be inferred"
                )
            source_time = float(frame.pts * frame.time_base)
            if origin is None:
                origin = source_time
            timestamp = source_time - origin
            if not math.isfinite(timestamp) or (
                previous_time is not None and timestamp <= previous_time
            ):
                raise ValueError("video timestamps must be finite and strictly increasing")
            previous_time = timestamp
            if config.end_seconds is not None and timestamp >= config.end_seconds:
                break
            tolerance = max(1e-8, float(frame.time_base) / 2)
            if timestamp + tolerance < next_time:
                continue
            if len(frames) >= config.max_frames:
                limited = True
                break
            if getattr(frame, "rotation", 0) % 360:
                raise ValueError("video display rotation must be normalized before processing")
            image = frame.to_image().convert("RGB")
            if width_height is None:
                width_height = image.size
            if image.size != width_height:
                raise ValueError("variable frame geometry is unsupported")
            index = len(frames)
            png_ref = f"inputs/frames/{index:06d}.png"
            jpg_ref = f"inputs/sam_frames/{index:06d}.jpg"
            (root / png_ref).parent.mkdir(parents=True, exist_ok=True)
            (root / jpg_ref).parent.mkdir(parents=True, exist_ok=True)
            image.save(root / png_ref)
            image.save(root / jpg_ref, quality=100, subsampling=0)
            frames.append(
                {
                    "frame_id": f"frame-{index:06d}",
                    "processed_index": index,
                    "source_frame_index": source_index,
                    "source_pts": frame.pts,
                    "source_time_base": str(frame.time_base),
                    "source_timestamp_seconds": source_time,
                    "timestamp_seconds": timestamp,
                    "image_ref": png_ref,
                    "sam_image_ref": jpg_ref,
                    "image_sha256": sha256(root / png_ref),
                    "size": list(image.size),
                }
            )
            next_time = (
                config.start_seconds
                + (
                    math.floor((timestamp + tolerance - config.start_seconds) * config.sample_fps)
                    + 1
                )
                / config.sample_fps
            )
    if not frames:
        raise ValueError("no frames selected; check the input/time window")
    if config.reference_frame >= len(frames):
        raise ValueError("reference_frame exceeds the selected processed-frame range")
    info = {
        "frames": frames,
        "frame_budget_reached": limited,
        "sample_fps_requested": config.sample_fps,
        "source_sha256": sha256(source),
        "decoder": f"PyAV {av.__version__}",
        "coordinate_space": "decoded upright RGB frames; original pixel dimensions",
        "unprocessed_source_frames": "no masks are synthesized for unsampled frames",
    }
    write_json(root / "inputs/video.json", info)
    return info


def encode_preview(paths: list[Path], destination: Path, fps: int, fixture: bool):
    from PIL import Image

    av = require_av()
    destination.parent.mkdir(parents=True, exist_ok=True)
    codec = "mpeg4" if fixture else "h264_nvenc"
    with Image.open(paths[0]) as first:
        width, height = first.size
    # yuv420p requires even dimensions. Padding is preview-only, never mask coordinates.
    width, height = width + width % 2, height + height % 2
    with av.open(str(destination), "w") as container:
        stream = container.add_stream(codec, rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        for path in paths:
            with Image.open(path) as image:
                canvas = Image.new("RGB", (width, height))
                canvas.paste(image.convert("RGB"), (0, 0))
            for packet in stream.encode(av.VideoFrame.from_image(canvas)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return {
        "codec": codec,
        "preview_fps": fps,
        "timing": "uniform sampled-frame playback; source PTS remain in video.json",
    }
