#!/usr/bin/env python3
"""Pinned before/after replay of logprob prefill alignment; no timing claims."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import tarfile
import time
from urllib.error import HTTPError

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


FIX = "bd1956c5ea17cc588598b49d3c6be1d5f725d34fecaca2afb28304327b59cca0"
BINARY_SHA = "f6798db48c310477627b3366ee99e64f1b00cab2f6ede005f64aa206be42f6de"
FIXTURE_SHA = "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e"
MODEL = "qwen3.6-35b-a3b-q4-k-m"


def run(args, root, report):
    identity, original_candidate, roots, model, replay = suite.inputs(args.cache, MODEL, root)
    qualifier = runpy.run_path(str(Path(__file__).with_name("profile-kv-scheduler-qualification.py")))
    links = root / "fixed-links"
    links.mkdir()
    fixed_identity, fixed, fixed_roots = qualifier["qualified_arms"](args.cache, FIX, links)
    if fixed["binary_sha256"] != BINARY_SHA:
        raise ValueError("fixed binary differs from preregistered build")
    if fixed_identity != identity:
        raise ValueError("fixed runtime baseline identity changed")
    roots["skippy-patched"] = fixed_roots["skippy-patched"]
    report.update(identity=identity, original_candidate=original_candidate, fixed_candidate=fixed, model=model)
    path = args.cache / f"scheduler-suite-v1-{MODEL}.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != FIXTURE_SHA:
        raise ValueError("fixture changed")
    fixture = json.loads(path.read_text())
    guarded = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-path-isolation.py")))["guarded"]
    for batch in (4096, 512):
        for first in ("normal", "logprobs"):
            arms = ("skippy-base", "skippy-patched") if first == "normal" else ("skippy-patched", "skippy-base")
            for arm in arms:
                directory = root / f"b{batch}-{first}-first-{arm}"
                directory.mkdir()
                env = host.clean_env(directory)
                env["SKIPPY_TELEMETRY_STDERR"] = "1"
                suite.nominal(env, soak=60)
                engine, argv = suite.settings(roots[arm], model, arm, 16)
                argv[argv.index("--batch-size") + 1] = str(batch)
                argv += ["--telemetry-level", "debug", "--telemetry-queue-capacity", "65536"]
                cell = {"batch_size": batch, "first": first, "arm": arm,
                        "directory": directory.name, "complete": False, "attempts": []}
                report["cells"].append(cell)
                print("LOGPROB_RETEST", batch, first, arm, flush=True)
                with host.server(roots[arm], model, engine, directory, env, 16, diagnostic=True, argv=argv) as child:
                    cell["resolved"] = suite.resolved(directory / "server.log", arm, 16, batch)
                    suite.verify_effective_tokens(model, engine, replay, fixture, establish=False)
                    modes = (first, "logprobs" if first == "normal" else "normal") * 2
                    cases = [("long-2", 24, mode) for mode in modes] + [("short-0", 128, mode) for mode in modes[:2]]
                    for index, (prompt, count, mode) in enumerate(cases):
                        suite.nominal(env, child.pid, 30)
                        request = host.body(model, fixture["mixed"][prompt]["text"], count)
                        if mode == "logprobs":
                            request["logprobs"] = 5
                        host.save(directory / f"{index}-{mode}-request.json", request)
                        item = {"mode": mode, "prompt": prompt, "output_tokens": count, "start_unix_ns": time.time_ns()}
                        try:
                            response = guarded(request, env, child.pid)
                            item["status"] = 200
                        except HTTPError as error:
                            item["status"] = error.code
                            response = {"http_status": error.code, "body": error.read().decode("utf-8", errors="replace")}
                        item["end_unix_ns"] = time.time_ns()
                        filename = f"{index}-{mode}.json"
                        host.save(directory / filename, response)
                        item.update(file=filename, sha256=hashlib.sha256((directory / filename).read_bytes()).hexdigest())
                        cell["attempts"].append(item)
                        host.save(args.output / "prefill-retest.json", report)
                        expected_rejection = prompt == "long-2" and batch == 4096 and arm == "skippy-base" and mode == "logprobs"
                        if expected_rejection:
                            if item["status"] != 400 or "2048-token configured iteration limit" not in response.get("body", ""):
                                raise ValueError("baseline rejection did not reproduce exactly")
                        elif item["status"] != 200:
                            raise ValueError("unexpected HTTP failure")
                        else:
                            usage, choice = response["usage"], response["choices"][0]
                            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                            if cached is None:
                                cached = (response.get("timings") or {}).get("cache_n")
                            if usage["prompt_tokens"] != fixture["mixed"][prompt]["tokens"] or usage["completion_tokens"] != count or choice["finish_reason"] != "length" or cached != 0:
                                raise ValueError("token/completion/cache gate failed")
                            if mode == "logprobs" and not choice.get("logprobs"):
                                raise ValueError("logprobs missing")
                cell["complete"] = True
                host.save(args.output / "prefill-retest.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"logprob-prefill-retest-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "patch_sha256": FIX,
              "fixture_sha256": FIXTURE_SHA, "cells": [],
              "scope": "Cache-off logprob chunk alignment replay; cross-engine/Q35/Flash discrepancies remain separate; no performance claim"}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "prefill-retest.json", report)
        with tarfile.open(args.output / "prefill-retest-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
