"""Download a complete official RBO representation, resume files, verify official MD5.

No extraction: video archives can expand far beyond their download size. The default
downloads all CSV/image sequences, object models, sensor model and interaction index;
--include-rosbags additionally downloads the redundant ROS representation.
"""

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

RECORD = "https://zenodo.org/api/records/1036660"


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def checksum(path, algorithm="md5"):
    value = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def download_file(row, destination, *, reserve_bytes, timeout=60):
    name = row["key"]
    if Path(name).name != name:
        raise ValueError("unsafe remote filename")
    final = destination / name
    algorithm, expected = row["checksum"].split(":", 1)
    if final.exists():
        if final.stat().st_size != row["size"] or checksum(final, algorithm) != expected:
            raise ValueError("existing file fails official checksum: " + name)
        return {"file": name, "status": "verified", "bytes": row["size"]}
    partial = final.with_name(name + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > row["size"]:
        raise ValueError("oversized partial file: " + name)
    if offset < row["size"]:
        # The current canonical records URL works where the legacy API content
        # endpoint can fail on redirects. Retain the API URL in the manifest.
        request = urllib.request.Request(
            "https://zenodo.org/records/1036660/files/" + name + "?download=1",
            headers={
                "User-Agent": "EnvCreator-dataset-download/1.0",
                **({"Range": f"bytes={offset}-"} if offset else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 206:
                content_range = response.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {offset}-") or content_range.split("/")[
                    -1
                ] != str(row["size"]):
                    raise ValueError("incorrect HTTP resume range: " + name)
            elif response.status == 200:
                offset = 0  # A server ignoring Range must never be appended to.
            else:
                raise ValueError("unexpected HTTP response: " + str(response.status))
            downloaded = offset
            with partial.open("ab" if offset else "wb") as stream:
                while block := response.read(4 * 1024 * 1024):
                    if shutil.disk_usage(destination).free - len(block) < reserve_bytes:
                        raise OSError("disk reserve reached; partial download preserved")
                    if downloaded + len(block) > row["size"]:
                        raise ValueError("response exceeds official size: " + name)
                    stream.write(block)
                    downloaded += len(block)
    if partial.stat().st_size < row["size"]:
        raise urllib.error.URLError("response ended early; resume preserved: " + name)
    if partial.stat().st_size != row["size"] or checksum(partial, algorithm) != expected:
        raise ValueError("download fails official size/checksum: " + name)
    partial.replace(final)
    return {"file": name, "status": "verified", "bytes": row["size"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--include-rosbags", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--status", action="store_true", help="Read local progress without network or mutation"
    )
    parser.add_argument("--reserve-gib", type=float, default=12)
    parser.add_argument("--workers", type=int, choices=[1, 2, 3, 4], default=2)
    args = parser.parse_args(argv)
    if args.reserve_gib < 0:
        parser.error("reserve must be nonnegative")
    destination = args.destination.resolve()
    if args.status:
        state = json.loads((destination / "download-status.json").read_text())
        print(
            json.dumps(
                {
                    "status": state["status"],
                    "expected_files": state["expected_files"],
                    "expected_bytes": state["expected_bytes"],
                    "verified_files": len(state["completed"]),
                    "verified_bytes": sum(r["bytes"] for r in state["completed"]),
                    "partial_bytes": sum(p.stat().st_size for p in destination.glob("*.part")),
                    "errors": state["errors"],
                    "free_bytes": shutil.disk_usage(destination).free,
                },
                indent=2,
            )
        )
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    # Exclude concurrent writers to the same destination; fcntl is available on this host.
    import fcntl

    with (destination / ".download.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status_path = destination / "download-status.json"
        if status_path.exists():
            stamp = time.time_ns()
            shutil.copyfile(status_path, destination / f"download-status.{stamp}.json")
        index = destination / "official-record.json"
        if not index.exists():
            with urllib.request.urlopen(RECORD, timeout=30) as response:
                write_json(index, json.load(response))
        record = json.loads(index.read_text())
        if str(record["id"]) != "1036660":
            raise ValueError("unexpected official record identity")
        rows = [r for r in record["files"] if args.include_rosbags or not r["key"].endswith(".bag")]
        if len([r for r in rows if r["key"].endswith("_o.tar.gz")]) != 358:
            raise ValueError("official inventory does not contain all 358 sequences")
        total = sum(r["size"] for r in rows)
        state = {
            "record": RECORD,
            "representation": "all" if args.include_rosbags else "csv-images-complete",
            "expected_files": len(rows),
            "expected_bytes": total,
            "reserve_bytes": int(args.reserve_gib * 2**30),
            "completed": [],
            "errors": [],
            "status": "prepared",
            "updated_unix": time.time(),
        }
        write_json(destination / "download-manifest.json", {"record": RECORD, "files": rows})
        write_json(destination / "download-status.json", state)
        print(
            json.dumps(
                {
                    k: state[k]
                    for k in ["representation", "expected_files", "expected_bytes", "status"]
                }
            ),
            flush=True,
        )
        if args.prepare_only:
            return 0
        remaining = sum(
            max(
                0,
                r["size"]
                - min(
                    r["size"],
                    max(
                        (
                            (destination / r["key"]).stat().st_size
                            if (destination / r["key"]).exists()
                            else 0
                        ),
                        (
                            (destination / (r["key"] + ".part")).stat().st_size
                            if (destination / (r["key"] + ".part")).exists()
                            else 0
                        ),
                    ),
                ),
            )
            for r in rows
        )
        if remaining + state["reserve_bytes"] > shutil.disk_usage(destination).free:
            state.update(status="blocked_storage", remaining_bytes=remaining)
            write_json(destination / "download-status.json", state)
            print("Insufficient storage for the entire selection plus disk reserve.", flush=True)
            return 2
        state["status"] = "downloading"
        write_json(destination / "download-status.json", state)
        # Small models first, then all video archives. This is an ordering, not a subset.
        rows.sort(key=lambda row: ("_o." in row["key"], row["size"]))

        def obtain(row):
            failures = 0
            while failures < 3:
                partial = destination / (row["key"] + ".part")
                before = partial.stat().st_size if partial.exists() else 0
                try:
                    return download_file(row, destination, reserve_bytes=state["reserve_bytes"])
                except (urllib.error.URLError, TimeoutError) as error:
                    after = partial.stat().st_size if partial.exists() else 0
                    failures = 0 if after > before else failures + 1
                    if failures == 3:
                        raise
                    print(
                        json.dumps(
                            {
                                "file": row["key"],
                                "consecutive_no_progress": failures,
                                "partial_bytes": after,
                                "error": str(error),
                            }
                        ),
                        flush=True,
                    )
                    time.sleep(2)

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = {pool.submit(obtain, row): row for row in rows}
            for future in concurrent.futures.as_completed(pending):
                try:
                    result = future.result()
                    state["completed"].append(result)
                except Exception as error:
                    result = {"file": pending[future]["key"], "error": str(error)}
                    state["errors"].append(result)
                state["updated_unix"] = time.time()
                write_json(destination / "download-status.json", state)
                print(json.dumps(result), flush=True)
        state["status"] = (
            "complete"
            if not state["errors"] and len(state["completed"]) == len(rows)
            else "incomplete"
        )
        state["verified_bytes"] = sum(r["bytes"] for r in state["completed"])
        write_json(destination / "download-status.json", state)
        return 0 if state["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
