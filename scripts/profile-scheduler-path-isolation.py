#!/usr/bin/env python3
"""Fresh-process, counterbalanced observation controls; no performance claims."""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
import hashlib
import json
from pathlib import Path
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


FIXTURE_SHA = "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e"


def guarded(request, env, pid):
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(host.post, "/v1/completions", request)
        while not pending.done():
            wait([pending], timeout=0.5)
            if host.thermal_pressure() or host.competing_work(env, pid):
                raise host.HostUnavailable("path isolation thermal/contention guard")
        return pending.result()


def run(args, root, report):
    identity, candidate, roots, model, replay = suite.inputs(args.cache, args.model, root)
    report.update(identity=identity, candidate=candidate, model=model)
    path = args.cache / f"scheduler-suite-v1-{args.model}.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != FIXTURE_SHA:
        raise ValueError("registered screen fixture changed")
    fixture = json.loads(path.read_text())
    # Each prompt/mode starts in its own fresh process. Reverse arm order in
    # the second block, then alternate modes to expose request-history effects.
    for first in ("normal", "logprobs"):
        order = ("skippy-base",) if args.trace_driver else ("skippy-base", "llama-slots")
        if first == "logprobs":
            order = tuple(reversed(order))
        modes = (first, "logprobs" if first == "normal" else "normal") * 2
        for arm in order:
            for name in (("long-2",) if args.trace_driver else ("short-0", "long-2")):
                row = fixture["mixed"][name]
                count = suite.CORRECTNESS_CASES[args.model][name]
                directory = root / f"{first}-first-{arm}-{name}"
                directory.mkdir()
                env = host.clean_env(directory)
                suite.nominal(env, soak=60)
                engine, argv = suite.settings(roots[arm], model, arm, 16)
                if args.batch_size != 512:
                    argv[argv.index("--batch-size") + 1] = str(args.batch_size)
                if args.microbatch_size != 512:
                    argv[argv.index("--microbatch-size") + 1] = str(args.microbatch_size)
                if args.trace_driver:
                    env["SKIPPY_TELEMETRY_STDERR"] = "1"
                    argv += ["--telemetry-level", "debug", "--telemetry-queue-capacity", "65536"]
                cell = {"arm": arm, "prompt": name, "first": first,
                        "complete": False, "responses": []}
                report["cells"].append(cell)
                print("PATH_ISOLATION", first, arm, name, flush=True)
                with host.server(roots[arm], model, engine, directory, env, 16,
                                 diagnostic=True, argv=argv) as child:
                    cell["resolved"] = suite.resolved(directory / "server.log", arm, 16, args.batch_size, args.microbatch_size)
                    suite.verify_effective_tokens(model, engine, replay, fixture, establish=False)
                    for index, mode in enumerate(modes):
                        suite.nominal(env, child.pid, 30)
                        request = host.body(model, row["text"], count)
                        if mode == "logprobs":
                            request["logprobs"] = 5
                        filename = f"{index}-{mode}.json"
                        host.save(directory / f"{index}-{mode}-request.json", request)
                        start_ns = time.time_ns()
                        response = guarded(request, env, child.pid)
                        end_ns = time.time_ns()
                        host.save(directory / filename, response)
                        choice = response["choices"][0]
                        usage = response.get("usage", {})
                        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                        if cached is None:
                            cached = (response.get("timings") or {}).get("cache_n")
                        if (usage.get("prompt_tokens") != row["tokens"] or
                                usage.get("completion_tokens") != count or
                                choice.get("finish_reason") != "length" or cached != 0):
                            raise ValueError("path isolation token/cache/completion gate failed")
                        if mode == "logprobs" and not choice.get("logprobs"):
                            raise ValueError("requested logprobs missing")
                        cell["responses"].append({"mode": mode, "file": str(Path(directory.name) / filename),
                            "start_unix_ns": start_ns, "end_unix_ns": end_ns,
                            "sha256": hashlib.sha256((directory / filename).read_bytes()).hexdigest(),
                            "text_sha256": hashlib.sha256(choice["text"].encode()).hexdigest()})
                        host.save(args.output / "path-isolation.json", report)
                cell["complete"] = True
                host.save(args.output / "path-isolation.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=("qwen3.6-35b-a3b-q4-k-m",), required=True)
    parser.add_argument("--trace-driver", action="store_true")
    parser.add_argument("--batch-size", type=int, choices=(512, 4096), default=512)
    parser.add_argument("--microbatch-size", type=int, choices=(512, 455, 511), default=512)
    args = parser.parse_args()
    if args.batch_size != 512 and not args.trace_driver:
        parser.error("batch4096 control requires the registered trace-only experiment")
    if args.microbatch_size != 512 and (not args.trace_driver or args.batch_size != 512):
        parser.error("microbatch control requires trace-only with logical batch512")
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-path-isolation-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "fixture_sha256": FIXTURE_SHA,
              "trace_driver": args.trace_driver, "batch_size": args.batch_size,
              "microbatch_size": args.microbatch_size,
              "cells": [], "scope": "Observation mode and request-order isolation on pinned binaries; no tolerance relaxation or performance claims"}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "path-isolation.json", report)
        with tarfile.open(args.output / "path-isolation-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
