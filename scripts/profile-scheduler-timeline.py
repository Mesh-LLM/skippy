#!/usr/bin/env python3
"""Private CPU/Metal mixed-request captures; export bounded diagnostic evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import resource
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def run(args, root, report):
    identity, candidate, roots, model, replay = suite.inputs(args.cache, args.model, root)
    report.update(identity=identity, candidate=candidate, model=model)
    fixture_path = args.cache / f"scheduler-suite-v1-{args.model}.json"
    fixture = json.loads(fixture_path.read_text())
    report["fixture_sha256"] = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    host.save(args.output / "fixtures.json", fixture)
    with (args.output / "doctor.json").open("w") as out:
        host.command(host.IK + ["doctor", "--json"], host.clean_env(root), stdout=out, timeout=120)
    for arm in suite.ARMS:
        directory = root / arm
        private = directory / "private"
        private.mkdir(parents=True)
        env = host.clean_env(directory)
        suite.nominal(env, soak=120)
        engine, argv = suite.settings(roots[arm], model, arm, 16)
        if engine == "skippy":
            env["SKIPPY_TELEMETRY_STDERR"] = "1"
            env["SKIPPY_TELEMETRY_OTLP_FILE"] = str(private / "scheduler-otlp.jsonl")
            argv += ["--telemetry-level", "debug", "--telemetry-queue-capacity", "65536"]
        cell = {"arm": arm, "complete": False, "private": str(private), "argv": argv}
        report["cells"].append(cell)
        host.save(args.output / "timeline.json", report)
        print("TIMELINE", args.model, arm, flush=True)
        with host.server(roots[arm], model, engine, directory, env, 16,
                         diagnostic=True, argv=argv) as child:
            cell["resolved"] = suite.resolved(directory / "server.log", arm, 16)
            suite.verify_effective_tokens(model, engine, replay, fixture, establish=False)
            rows = dict(fixture["mixed"], **{"short-8": fixture["mixed"]["short-0"]})
            cell["warmup"] = replay["wave"](model, rows, (("short-2", 0, 8),),
                                              False, env, child.pid, capture_text=True)
            suite.nominal(env, child.pid, 120)
            reading = replay["trace_wave"](model, rows, replay["WAVES"]["mixed-c4"],
                env, child.pid, private, seconds=60, capture_text=True)
            if any(q["cached_tokens"] != 0 for q in reading["requests"].values()):
                raise ValueError("capture cache counter missing or nonzero")
            cell["wave"] = reading
        if engine == "skippy":
            cell["scheduler"] = replay["skippy_trace"](directory / "server.log", {"mixed-c4": reading})
            cell["links"] = replay["linked_schedule"](private / "scheduler-otlp.jsonl", reading)
        cell["before"] = json.loads((directory / "before.json").read_text())
        cell["after"] = json.loads((directory / "after.json").read_text())
        cell["complete"] = True
        host.save(args.output / "timeline.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=("qwen3.5-4b-q4-k-m", "qwen3-coder-30b-a3b-q4-k-m"), required=True)
    args = parser.parse_args()
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < 2048:
        resource.setrlimit(resource.RLIMIT_NOFILE, (2048, hard))
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-timeline-{args.model}-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "cells": [], "prefix_cache": False,
              "scope": "Instrumented mixed-c4 diagnostic, not speed evidence; raw captures and OTLP remain private"}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "timeline.json", report)
        with tarfile.open(args.output / "timeline-evidence.tar.gz", "w:gz") as archive:
            for arm in suite.ARMS:
                for name in ("launch.json", "before.json", "after.json", "server.log"):
                    path = root / arm / name
                    if path.is_file() and not path.is_symlink():
                        archive.add(path, arcname=f"{arm}/{name}")


if __name__ == "__main__":
    main()
