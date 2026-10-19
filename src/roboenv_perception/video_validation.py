import numpy as np
from PIL import Image

from .io import read_json, relative_file, sha256
from .masks import bbox


def validate_video_contents(root, scene, errors):
    frames = scene["frames"]
    if not frames:
        errors.append("video has no processed frames")
    frame_ids = [f["frame_id"] for f in frames]
    times = [f["timestamp_seconds"] for f in frames]
    if len(frame_ids) != len(set(frame_ids)) or any(b <= a for a, b in zip(times, times[1:])):
        errors.append("video frame identity/timestamps are invalid")
    observed = {}
    for frame in frames:
        path = relative_file(root, frame["image_ref"])
        with Image.open(path) as image:
            width, height = image.size
        if sha256(path) != frame["image_sha256"]:
            errors.append("frame image hash mismatch")
        routes = read_json(relative_file(root, frame["background_ref"]))
        if set(routes["masks"]) != {"mesh", "background", "transient", "unknown"}:
            errors.append("unexpected video routing mask names")
        masks = []
        for name, ref in routes["masks"].items():
            with Image.open(relative_file(root, ref)) as image:
                array = np.array(image)
            if array.shape != (height, width) or not np.isin(array, [0, 255]).all():
                errors.append(f"invalid video routing mask: {name}")
            masks.append(array > 0)
        if len(masks) != 4 or not np.all(sum(mask.astype(int) for mask in masks) == 1):
            errors.append("video routing masks do not form a partition")
        rows = read_json(relative_file(root, frame["objects_ref"]))
        for row in rows:
            key = (row["object_id"], frame["frame_id"])
            if key in observed:
                errors.append("duplicate video track observation")
            observed[key] = row
            with Image.open(relative_file(root, row["mask_ref"])) as image:
                mask = np.array(image)
            if mask.shape != (height, width) or not np.isin(mask, [0, 255]).all() or not mask.any():
                errors.append("invalid video observation mask")
                continue
            if bbox(mask > 0) != row["bbox_xyxy"]:
                errors.append("video bbox/mask mismatch")
            x0, y0, x1, y1 = row["bbox_xyxy"]
            if row["rgba_to_frame"] != [[1, 0, x0], [0, 1, y0], [0, 0, 1]]:
                errors.append("invalid video crop transform")
            with Image.open(relative_file(root, row["rgba_ref"])) as image:
                if (
                    image.mode != "RGBA"
                    or image.size != (x1 - x0, y1 - y0)
                    or not np.array_equal(np.array(image)[:, :, 3], mask[y0:y1, x0:x1])
                ):
                    errors.append("invalid video RGBA crop/alpha")
            if not relative_file(root, row["context_ref"]).is_file():
                errors.append("missing video context crop")
    tracks = read_json(relative_file(root, scene["tracks_ref"]))
    track_ids = [track["object_id"] for track in tracks]
    if len(track_ids) != len(set(track_ids)):
        errors.append("duplicate video tracks")
    linked = set()
    for track in tracks:
        for observation in track["observations"]:
            key = (track["object_id"], observation["frame_id"])
            recorded = read_json(relative_file(root, observation["observation_ref"]))
            if key not in observed or recorded != observed[key] or key in linked:
                errors.append("track/observation reference mismatch")
            linked.add(key)
    if linked != set(observed):
        errors.append("unlinked video observations")
