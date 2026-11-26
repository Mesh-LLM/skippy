#!/usr/bin/env python3
"""Isolate offered concurrency and preceding waves; no performance claims."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


FIXTURE_SHA = "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e"
MODEL = "qwen3.5-4b-q4-k-m"
PROMPT = "load-0190"


def run(args, root, report):
    identity, candidate, roots, model, replay = suite.inputs(args.cache, MODEL, root)
    report.update(identity=identity, candidate=candidate, model=model)
    path = args.cache / f"scheduler-suite-v1-{MODEL}.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != FIXTURE_SHA:
        raise ValueError("registered fixture changed")
    fixture = json.loads(path.read_text())
    subject = next(row for row in fixture["load"] if row["id"] == PROMPT)
    if subject["native_prompt_tokens"] != 512 or subject["max_tokens"] != 32:
        raise ValueError("registered subject counts changed")
    row = {"text": subject["messages"][0]["content"], "tokens": 512}
    request = host.body(model, row["text"], 32, stream=True)
    for first in (1, 16):
        order = ("skippy-base",) if args.trace_baseline else (suite.ARMS if first == 1 else tuple(reversed(suite.ARMS)))
        sizes = (first, 16 if first == 1 else 1) * 2
        for arm in order:
            directory = root / f"c{first}-first-{arm}"
            directory.mkdir()
            env = host.clean_env(directory)
            suite.nominal(env, soak=60)
            engine, argv = suite.settings(roots[arm], model, arm, 16)
            if args.trace_baseline:
                env["SKIPPY_TELEMETRY_STDERR"] = "1"
                argv += ["--telemetry-level", "debug", "--telemetry-queue-capacity", "65536"]
            cell = {"arm": arm, "first": first, "complete": False, "waves": []}
            report["cells"].append(cell)
            print("BATCH_HISTORY", first, arm, flush=True)
            with host.server(roots[arm], model, engine, directory, env, 16,
                             diagnostic=True, argv=argv) as child:
                cell["resolved"] = suite.resolved(directory / "server.log", arm, 16)
                suite.verify_effective_tokens(model, engine, replay, fixture, establish=False)
                # Tokenization checks above do not perform generation. There is
                # deliberately no generation warmup before the first wave.
                for index, size in enumerate(sizes):
                    suite.nominal(env, child.pid, 30)
                    schedule = tuple((f"subject-{i:02d}", 0, 32) for i in range(size))
                    rows = {name: row for name, _, _ in schedule}
                    host.save(directory / f"{index}-requests.json",
                              {name: request for name in rows})
                    reading = replay["wave"](model, rows, schedule, False, env,
                                              child.pid, capture_text=True)
                    for result in reading["requests"].values():
                        if result["cached_tokens"] != 0:
                            raise ValueError("cache hit or missing cache counter")
                    filename = f"{index}-c{size}.json"
                    host.save(directory / filename, reading)
                    cell["waves"].append({"index": index, "concurrency": size,
                        "file": filename, "sha256": hashlib.sha256(
                            (directory / filename).read_bytes()).hexdigest()})
                    host.save(args.output / "batch-history.json", report)
            cell["complete"] = True
            host.save(args.output / "batch-history.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=(MODEL,), required=True)
    parser.add_argument("--trace-baseline", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-batch-history-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "fixture_sha256": FIXTURE_SHA,
              "trace_baseline": args.trace_baseline,
              "prompt": PROMPT, "cells": [],
              "scope": "Identical streaming requests at offered concurrency 1/16, reversed fresh-process order. "
                       "Actual native batch shape is not controlled. No timing or full c128 clearance."}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "batch-history.json", report)
        with tarfile.open(args.output / "batch-history-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
