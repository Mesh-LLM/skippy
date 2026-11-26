#!/usr/bin/env python3
"""Replay Qwen3.5 load-0100 with unchanged vanilla layouts at c1/c16."""

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


MODEL = "qwen3.5-4b-q4-k-m"
PROMPT = "load-0100"
ARMS = ("llama-slots", "llama-unified")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, root, report):
    provenance = json.loads(Path("q35-vanilla-concurrency-inputs.json").read_text())
    for name, expected in provenance["files"].items():
        if digest(Path(name)) != expected:
            raise ValueError("sealed input drift: " + name)
    identity, candidate, roots, model, replay = suite.inputs(args.cache, MODEL, root)
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())["qwen35-4b"]
    if (model["repo"] != pin["repo"] or model["revision"] != pin["revision"]
            or len(model["files"]) != 1 or model["files"][0]["sha256"] != pin["files"][0]["sha256"]
            or identity["files"]["llama-server"] != provenance["vanilla_binary_sha256"]):
        raise ValueError("model or vanilla binary identity drift")
    fixture_path = args.cache / f"scheduler-suite-v1-{MODEL}.json"
    if digest(fixture_path) != provenance["fixture_sha256"]:
        raise ValueError("fixture drift")
    fixture = json.loads(fixture_path.read_text())
    subject = next(row for row in fixture["load"] if row["id"] == PROMPT)
    if subject["native_prompt_tokens"] != 512 or subject["max_tokens"] != 32:
        raise ValueError("fixture token drift")
    row = {"text": subject["messages"][0]["content"], "tokens": 512}
    request = host.body(model, row["text"], 32, stream=True)
    report.update(provenance=provenance, identity=identity, candidate=candidate,
                  model=model, pin=pin, fixture_sha256=digest(fixture_path), request=request)
    for first in (1, 16):
        sizes = (first, 16 if first == 1 else 1) * 2
        for arm in ARMS if first == 1 else tuple(reversed(ARMS)):
            directory = root / f"c{first}-first-{arm}"
            directory.mkdir()
            environment = host.clean_env(directory)
            suite.nominal(environment, soak=60)
            engine, argv = suite.settings(roots[arm], model, arm, 16)
            cell = {"arm": arm, "first": first, "directory": directory.name,
                    "waves": [], "complete": False}
            report["cells"].append(cell)
            print("VANILLA_CONCURRENCY", first, arm, flush=True)
            with host.server(roots[arm], model, engine, directory, environment, 16,
                             diagnostic=True, argv=argv) as child:
                cell["resolved"] = suite.resolved(directory / "server.log", arm, 16)
                suite.verify_effective_tokens(model, engine, replay, fixture, establish=False)
                for index, size in enumerate(sizes):
                    suite.nominal(environment, child.pid, 30)
                    schedule = tuple((f"subject-{number:02d}", 0, 32) for number in range(size))
                    rows = {name: row for name, _, _ in schedule}
                    host.save(directory / f"{index}-requests.json", {name: request for name in rows})
                    reading = replay["wave"](model, rows, schedule, False, environment,
                                             child.pid, capture_text=True)
                    if any(response["cached_tokens"] != 0 for response in reading["requests"].values()):
                        raise ValueError("cache counter changed")
                    name = f"{index}-c{size}.json"
                    host.save(directory / name, reading)
                    cell["waves"].append({"index": index, "concurrency": size,
                                          "file": name, "sha256": digest(directory / name)})
                    host.save(args.output / "vanilla-concurrency.json", report)
            cell["complete"] = True
            host.save(args.output / "vanilla-concurrency.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"q35-vanilla-concurrency-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "cells": [],
              "scope": "Unchanged vanilla load-0100 c1/c16 identical-peer history; no timing or broad quality claim."}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "vanilla-concurrency.json", report)
        with tarfile.open(args.output / "vanilla-concurrency-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
