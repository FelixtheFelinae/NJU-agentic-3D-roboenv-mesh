"""Host measurements. Missing GPU counters are unknown, never fabricated zeroes."""

import csv
import io
import json
import platform
import shutil
import subprocess
import threading
import time
from collections import deque

import psutil


def _number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


class Telemetry:
    def __init__(self, root, jobs):
        self.root, self.jobs = root, jobs
        self.history = deque(maxlen=300)
        self.processes = {}
        self.latest = {"at": None, "gpus": [], "processes": [], "gpu_error": "等待首次采样"}
        self.stop = threading.Event()
        self.thread = None
        self.disk_previous = None

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        psutil.cpu_percent()
        while not self.stop.is_set():
            try:
                self.latest = self.sample()
                self.history.append(self.latest)
                # Attach actual host measurements to active runs for later infra diagnosis.
                for run_id in self.jobs.active_ids():
                    path = self.root / "runs" / run_id / "resources.jsonl"
                    if not path.exists() or path.stat().st_size < 32 * 1024 * 1024:
                        with path.open("a") as stream:
                            stream.write(json.dumps(self.latest) + "\n")
            except (OSError, psutil.Error):
                pass
            self.stop.wait(2)

    def sample(self):
        now = time.time()
        memory, disk = psutil.virtual_memory(), psutil.disk_usage(self.root)
        result = {
            "at": now,
            "host": platform.node(),
            "cpu_percent": psutil.cpu_percent(),
            "cpu_count": psutil.cpu_count(),
            "load_average": list(psutil.getloadavg()),
            "memory_used": memory.used,
            "memory_total": memory.total,
            "memory_percent": memory.percent,
            "swap_used": psutil.swap_memory().used,
            "disk_free": disk.free,
            "disk_total": disk.total,
            "system_process_count": len(psutil.pids()),
            "gpus": [],
            "gpu_processes": [],
            "gpu_error": None,
            "active_jobs": self.jobs.active_ids(),
            "processes": [],
        }
        counter = psutil.disk_io_counters()
        if counter and self.disk_previous:
            elapsed = max(now - self.disk_previous[0], 0.001)
            result["disk_read_bps"] = max(0, (counter.read_bytes - self.disk_previous[1]) / elapsed)
            result["disk_write_bps"] = max(
                0, (counter.write_bytes - self.disk_previous[2]) / elapsed
            )
        if counter:
            self.disk_previous = (now, counter.read_bytes, counter.write_bytes)
        owned = self.jobs.process_ids()
        next_processes = {}
        for process in psutil.process_iter(
            ["pid", "ppid", "name", "memory_info", "status", "create_time"]
        ):
            try:
                name = process.info["name"] or ""
                if process.pid not in owned and not any(
                    x in name.lower() for x in ("blender", "isaac", "python", "node", "kit")
                ):
                    continue
                previous = self.processes.get(process.pid)
                current = (
                    previous
                    if previous and previous.create_time() == process.info["create_time"]
                    else process
                )
                next_processes[process.pid] = current
                result["processes"].append(
                    {
                        "pid": process.pid,
                        "ppid": process.info["ppid"],
                        "name": name,
                        "cpu_percent": current.cpu_percent(),
                        "rss": process.info["memory_info"].rss,
                        "status": process.info["status"],
                        "created": process.info["create_time"],
                        "run_id": owned.get(process.pid),
                        "managed": process.pid in owned,
                    }
                )
            except (psutil.Error, OSError):
                continue
        self.processes = next_processes
        result["processes"].sort(key=lambda p: (not p["managed"], -p["rss"]))
        result["managed_process_count"] = sum(p["managed"] for p in result["processes"])
        result["processes"] = result["processes"][:100]
        if not shutil.which("nvidia-smi"):
            result["gpu_error"] = "未发现 nvidia-smi；GPU 指标不可用"
            return result
        try:
            run = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            for row in csv.reader(io.StringIO(run.stdout), skipinitialspace=True):
                if len(row) != 8:
                    continue
                result["gpus"].append(
                    dict(
                        zip(
                            (
                                "index",
                                "name",
                                "utilization",
                                "memory_used_mb",
                                "memory_total_mb",
                                "temperature",
                                "power_w",
                                "power_limit_w",
                            ),
                            [row[0], row[1], *[_number(v) for v in row[2:]]],
                            strict=True,
                        )
                    )
                )
            procs = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,process_name,used_memory,gpu_uuid",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if procs.returncode == 0:
                for row in csv.reader(io.StringIO(procs.stdout), skipinitialspace=True):
                    if len(row) == 4 and row[0].isdigit():
                        result["gpu_processes"].append(
                            {
                                "pid": int(row[0]),
                                "name": row[1],
                                "memory_mb": _number(row[2]),
                                "gpu_uuid": row[3],
                            }
                        )
        except (OSError, subprocess.SubprocessError):
            result["gpu_error"] = "GPU 采集失败或超时；当前数值不可用"
        return result
