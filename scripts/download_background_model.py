"""Download a pinned public SD1.5 Inpainting snapshot for optional offline generation."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

REPOSITORY = "stable-diffusion-v1-5/stable-diffusion-inpainting"
REVISION = "8a4288a76071f7280aedbdb3253bdb9e9d5d84bb"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Local model directory, outside Git")
    args = parser.parse_args()
    # Only public downloads; do not read the user's Hugging Face or VLM credentials.
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import HfApi, hf_hub_download

    started = time.monotonic()
    model = HfApi(token=False).model_info(REPOSITORY, revision=REVISION, files_metadata=True)
    files = [item for item in model.siblings
             if item.rfilename.endswith(".json") or item.rfilename.endswith("merges.txt")
             or item.rfilename in {"README.md", "LICENSE"}
             or "fp16" in item.rfilename and item.rfilename.endswith(".safetensors")]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    def download(item):
        name = Path(item.rfilename)
        if name.is_absolute() or ".." in name.parts:
            raise ValueError("invalid model file path")
        path = Path(hf_hub_download(REPOSITORY, item.rfilename, revision=REVISION,
                                    local_dir=output, token=False))
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        value = digest.hexdigest()
        if item.lfs and value != item.lfs.sha256:
            raise ValueError(f"download hash mismatch: {item.rfilename}")
        print(f"Downloaded and hashed: {item.rfilename}", flush=True)
        return {"path": item.rfilename, "size": path.stat().st_size, "sha256": value}

    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(download, files))
    manifest = {"repository": REPOSITORY, "revision": REVISION, "files": records,
                "download_seconds": time.monotonic() - started,
                "credentials": "none; public anonymous download; token=False",
                "license": "creativeml-openrail-m; model card retained"}
    (output / "envcreator-model.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Local model ready: {output}", flush=True)


if __name__ == "__main__":
    main()
