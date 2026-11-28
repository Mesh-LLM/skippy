"""Retain bounded native startup evidence without replacing a failure verdict."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .ci_artifacts import write_json

LIMIT = 64 * 1024


def read_evidence(path, *, tail=False):
    try:
        with Path(path).open("rb") as stream:
            if tail:
                size = stream.seek(0, 2)
                stream.seek(max(0, size - LIMIT))
                data, truncated = stream.read(LIMIT), size > LIMIT
            else:
                data = stream.read(LIMIT + 1)
                truncated = len(data) > LIMIT
        return {"text": data[:LIMIT].decode("utf-8", errors="replace"), "truncated": truncated}
    except OSError as error:
        return {"error": type(error).__name__}


def command_evidence(args):
    with tempfile.TemporaryFile() as stream:
        try:
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=stream,
                                    stderr=stream, timeout=5, check=False)
            status = {"exit_code": result.returncode}
        except (OSError, subprocess.TimeoutExpired) as error:
            status = {"error": type(error).__name__}
        size = stream.seek(0, 2)
        stream.seek(0)
        return {**status, "text": stream.read(LIMIT).decode("utf-8", errors="replace"),
                "truncated": size > LIMIT}


def resources():
    paths = ["/proc/meminfo", "/proc/self/cgroup", "/proc/self/limits", "/proc/self/status"]
    groups = {Path("/sys/fs/cgroup")}
    for row in read_evidence("/proc/self/cgroup").get("text", "").splitlines():
        if row.startswith("0::") and ".." not in Path(row[3:]).parts:
            groups.add(Path("/sys/fs/cgroup") / row[3:].lstrip("/"))
    for group in sorted(groups):
        paths += [str(group / name) for name in
                  ("memory.current", "memory.max", "memory.high", "memory.events", "memory.swap.max")]
    return {
        "files": {path: read_evidence(path) for path in paths},
        "gpu_memory": command_evidence(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,driver_version",
                                        "--format=csv"]),
        "gpu_processes": command_evidence(["nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory",
                                           "--format=csv"]),
        # Process arguments and environment values can contain credentials.
        "processes": command_evidence(["ps", "-eo", "pid,ppid,pgid,rss,comm", "--sort=-rss"]),
    }


def record_failure(output, sha, configuration, profile, error, pid=None):
    try:
        path = output / "failure-diagnostics.json"
        if path.exists():
            return  # Keep the snapshot taken before child cleanup.
        report = {"schema_version": 1, "source_sha": sha, "configuration": configuration,
                  "profile": profile, "run_id": os.environ.get("JIANYANG_RUN_ID"),
                  "server_pid": pid, "error": str(error)[:4096],
                  "server_log_tail": read_evidence(output / "server.log", tail=True),
                  "resources": resources()}
        # Existing native log forwarding retains this in Actions even when native
        # artifacts are unavailable. The controller treats all native text as data.
        print("Native failure diagnostics: " + json.dumps(report), flush=True)
        write_json(path, report)
    except Exception as diagnostic_error:
        print("Native failure diagnostics unavailable: " + type(diagnostic_error).__name__,
              file=sys.stderr, flush=True)


@contextmanager
def failure_diagnostics(output, sha, configuration, profile):
    try:
        yield
    except (Exception, KeyboardInterrupt) as error:
        record_failure(output, sha, configuration, profile, error)
        raise
