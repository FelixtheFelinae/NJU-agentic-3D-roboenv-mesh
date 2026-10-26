"""Merge a focused supplemental perception run into one primary evidence bundle."""

import argparse
import json
import shutil
import time
from pathlib import Path

from roboenv_perception.io import read_json, sha256, write_json


def rewrite_refs(value, source_asset, target_asset):
    if isinstance(value, dict):
        return {key: rewrite_refs(item, source_asset, target_asset) for key, item in value.items()}
    if isinstance(value, list):
        return [rewrite_refs(item, source_asset, target_asset) for item in value]
    if isinstance(value, str):
        return value.replace(f"objects/{source_asset}/", f"objects/{target_asset}/")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, required=True)
    parser.add_argument("--supplemental", type=Path, required=True)
    parser.add_argument("--supplemental-asset", required=True)
    parser.add_argument("--target-asset", required=True)
    parser.add_argument("--drop", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    primary, supplemental, output = args.primary.resolve(), args.supplemental.resolve(), args.output.resolve()
    for root in (primary, supplemental):
        run = read_json(root / "run.json")
        if run.get("status") != "completed":
            raise ValueError(f"source run is not completed: {root}")
        index = read_json(root / "artifacts.json")
        for ref, digest in index.items():
            if sha256(root / ref) != digest:
                raise ValueError(f"source artifact changed: {root / ref}")
    if output.exists():
        raise FileExistsError("output already exists")
    shutil.copytree(primary, output)

    inventory = read_json(output / "objects.json")
    by_asset = {entry["asset_id"]: entry for entry in inventory["objects"]}
    if args.target_asset not in by_asset:
        raise ValueError("target asset is absent from the primary inventory")
    supplemental_evidence = read_json(
        supplemental / "objects" / args.supplemental_asset / "evidence.json"
    )
    target = output / "objects" / args.target_asset
    shutil.rmtree(target)
    shutil.copytree(supplemental / "objects" / args.supplemental_asset, target)
    supplemental_evidence = rewrite_refs(
        supplemental_evidence, args.supplemental_asset, args.target_asset
    )
    supplemental_evidence["asset_id"] = args.target_asset
    supplemental_evidence["object_id"] = by_asset[args.target_asset]["object_id"]
    supplemental_evidence["evidence_source"] = {
        "kind": "supplemental_focused_perception",
        "path_at_creation": str(supplemental),
        "artifact_index_sha256": sha256(supplemental / "artifacts.json"),
        "source_asset_id": args.supplemental_asset,
    }
    write_json(target / "evidence.json", supplemental_evidence)
    by_asset[args.target_asset]["status"] = supplemental_evidence["status"]

    dropped = set(args.drop)
    for asset in dropped:
        shutil.rmtree(output / "objects" / asset, ignore_errors=True)
    inventory["objects"] = [
        entry for entry in inventory["objects"] if entry["asset_id"] not in dropped
    ]
    inventory["evidence_merge"] = {
        "primary": str(primary),
        "supplemental": str(supplemental),
        "supplemental_asset": args.supplemental_asset,
        "target_asset": args.target_asset,
        "dropped_assets": sorted(dropped),
    }
    write_json(output / "objects.json", inventory)

    run = read_json(output / "run.json")
    run.update(
        stage="object_evidence_merged",
        status="completed",
        merged_sources={
            "primary": str(primary),
            "supplemental": str(supplemental),
        },
        ended_unix=time.time(),
    )
    run.pop("artifact_index_sha256", None)
    write_json(output / "run.json", run)
    index = {
        str(path.relative_to(output)): sha256(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.relative_to(output).as_posix() not in {"artifacts.json", "run.json"}
    }
    write_json(output / "artifacts.json", index)
    run["artifact_index_sha256"] = sha256(output / "artifacts.json")
    write_json(output / "run.json", run)
    print(json.dumps({"output": str(output), "objects": len(inventory["objects"])}))


if __name__ == "__main__":
    main()
