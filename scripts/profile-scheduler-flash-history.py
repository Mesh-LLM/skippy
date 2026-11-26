#!/usr/bin/env python3
"""Reverse fresh-process serial/mixed Flash waves without changing native settings."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


MODEL = "qwen3.8-flash-next-iq4-xs"
FIXTURE_SHA = "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e"
SCHEDULES = {
    "serial": (("short-9", 0.85, 48),),
    "mixed": (("short-8", 0, 128), ("long-2", 0.65, 24),
              ("long-3", 0.75, 24), ("short-9", 0.85, 48)),
}


def run(args, root, report):
    identity, candidate, roots, model, replay = suite.inputs(args.cache, MODEL, root)
    report.update(identity=identity, candidate=candidate, model=model)
    path = args.cache / f"scheduler-suite-v1-{MODEL}.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != FIXTURE_SHA:
        raise ValueError("registered fixture changed")
    fixture = json.loads(path.read_text())
    rows = dict(fixture["mixed"], **{"short-8": fixture["mixed"]["short-0"]})
    if SCHEDULES["mixed"] != replay["WAVES"]["mixed-c4"]:
        raise ValueError("original mixed schedule changed")
    for first in ("serial", "mixed"):
        order = suite.ARMS if first == "serial" else tuple(reversed(suite.ARMS))
        labels = (first, "mixed" if first == "serial" else "serial") * 2
        for arm in order:
            directory = root / f"{first}-first-{arm}"
            directory.mkdir()
            env = host.clean_env(directory)
            suite.nominal(env, soak=60)
            engine, argv = suite.settings(roots[arm], model, arm, 16)
            if arm.startswith("skippy"):
                argv[argv.index("--startup-timeout-secs") + 1] = "1800"
            cell = {"arm": arm, "first": first, "complete": False, "waves": []}
            report["cells"].append(cell)
            print("FLASH_HISTORY", first, arm, flush=True)
            with host.server(roots[arm], model, engine, directory, env, 16,
                             diagnostic=True, argv=argv, startup_timeout=1860) as child:
                cell["resolved"] = suite.resolved(directory / "server.log", arm, 16)
                suite.verify_effective_tokens(model, engine, replay, fixture, establish=False)
                for index, label in enumerate(labels):
                    suite.nominal(env, child.pid, 30)
                    schedule = SCHEDULES[label]
                    requests = {name: host.body(model, rows[name]["text"], count, stream=True)
                                for name, _, count in schedule}
                    host.save(directory / f"{index}-requests.json", requests)
                    reading = replay["wave"](model, rows, schedule, False, env,
                                              child.pid, capture_text=True)
                    if any(r["cached_tokens"] != 0 for r in reading["requests"].values()):
                        raise ValueError("cache hit or missing cache counter")
                    filename = f"{index}-{label}.json"
                    host.save(directory / filename, reading)
                    cell["waves"].append({"index": index, "label": label,
                        "schedule": schedule, "file": filename, "sha256": hashlib.sha256(
                            (directory / filename).read_bytes()).hexdigest()})
                    host.save(args.output / "flash-history.json", report)
            cell["complete"] = True
            host.save(args.output / "flash-history.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=(MODEL,), required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-flash-history-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "fixture_sha256": FIXTURE_SHA,
              "cells": [], "scope": "Fresh-process serial/mixed order isolation; no generation warmup. "
                  "No native batch/operator attribution, performance claim or serial load-0001 clearance."}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "flash-history.json", report)
        with tarfile.open(args.output / "flash-history-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
