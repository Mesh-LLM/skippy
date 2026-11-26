#!/usr/bin/env python3
"""Fresh-process serial and logprob controls for qualification screen mismatches."""

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
                raise host.HostUnavailable("correctness request thermal/contention guard")
        return pending.result()


def run(args, root, report):
    identity, candidate, roots, model, replay = suite.inputs(args.cache, args.model, root)
    report.update(identity=identity, candidate=candidate, model=model)
    path = args.cache / f"scheduler-suite-v1-{args.model}.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != args.fixture_sha256:
        raise ValueError("registered screen fixture changed")
    fixture = json.loads(path.read_text())
    cases = suite.CORRECTNESS_CASES[args.model]
    prompts = [(row["id"], row["messages"][0]["content"], row["native_prompt_tokens"], cases[row["id"]])
               for row in fixture["load"] if row["id"] in cases]
    for name, count in cases.items():
        if name.startswith("load-"):
            continue
        row = fixture["mixed"][name]
        prompts.append((name, row["text"], row["tokens"], count))
    if {p[0] for p in prompts} != set(cases) or len(prompts) != len(cases):
        raise ValueError("registered correctness prompts missing or duplicated")
    report["prompts"] = [{"id": name, "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                           "input_tokens": n, "output_tokens": out} for name, text, n, out in prompts]
    for repeat, order in enumerate((suite.ARMS, tuple(reversed(suite.ARMS)))):
        for arm in order:
            directory = root / f"r{repeat}-{arm}"
            directory.mkdir()
            env = host.clean_env(directory)
            suite.nominal(env, soak=60)
            engine, argv = suite.settings(roots[arm], model, arm, 16)
            if arm.startswith("skippy"):
                argv[argv.index("--startup-timeout-secs") + 1] = str(args.startup_timeout)
            cell = {"arm": arm, "repeat": repeat, "complete": False, "responses": []}
            report["cells"].append(cell)
            print("CORRECTNESS", repeat, arm, flush=True)
            with host.server(roots[arm], model, engine, directory, env, 16,
                             diagnostic=True, argv=argv, startup_timeout=args.startup_timeout + 60) as child:
                cell["resolved"] = suite.resolved(directory / "server.log", arm, 16)
                suite.verify_effective_tokens(model, engine, replay, fixture, establish=False)
                for name, text, input_tokens, count in prompts:
                    suite.nominal(env, child.pid, 30)
                    for mode in ("normal", "normal-repeat", "logprobs", "logprobs-repeat"):
                        request = host.body(model, text, count)
                        if mode.startswith("logprobs"):
                            request["logprobs"] = 5
                        if args.stream_normal and mode.startswith("normal"):
                            reading = replay["wave"](model, {name: {"text": text, "tokens": input_tokens}},
                                ((name, 0, count),), False, env, child.pid, capture_text=True)
                            item = reading["requests"][name]
                            response = {"usage": item["usage"], "timings": item["native_timings"],
                                "choices": [{"text": item["text"], "finish_reason": item["finish_reason"]}],
                                "stream_evidence": reading}
                        else:
                            response = guarded(request, env, child.pid)
                        filename = f"{name}-{mode}.json"
                        host.save(directory / filename, response)
                        usage = response.get("usage", {})
                        choice = response["choices"][0]
                        if usage.get("prompt_tokens") != input_tokens or usage.get("completion_tokens") != count:
                            raise ValueError("correctness control token count mismatch")
                        if choice.get("finish_reason") != "length":
                            raise ValueError("correctness control incomplete")
                        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                        if cached is None:
                            cached = (response.get("timings") or {}).get("cache_n")
                        if cached != 0:
                            raise ValueError(f"correctness cache counter not zero: {cached}")
                        if mode.startswith("logprobs") and not choice.get("logprobs"):
                            raise ValueError("missing requested logprobs")
                        cell["responses"].append({"prompt": name, "mode": mode, "file": filename,
                            "sha256": hashlib.sha256((directory / filename).read_bytes()).hexdigest(),
                            "text_sha256": hashlib.sha256(choice["text"].encode()).hexdigest()})
                        host.save(args.output / "correctness-controls.json", report)
            cell["complete"] = True
            host.save(args.output / "correctness-controls.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=tuple(suite.CORRECTNESS_CASES), required=True)
    parser.add_argument("--stream-normal", action="store_true")
    parser.add_argument("--startup-timeout", type=int, choices=(300, 1800), default=300)
    parser.add_argument("--fixture-sha256", default=FIXTURE_SHA)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-correctness-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "fixture_sha256": args.fixture_sha256,
              "startup_timeout_s": args.startup_timeout,
              "normal_transport": "streaming" if args.stream_normal else "non-streaming",
              "cells": [], "scope": "Registered saved failing prompts; two fresh processes per arm; no tolerance relaxation or speed claims"}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "correctness-controls.json", report)
        with tarfile.open(args.output / "correctness-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
