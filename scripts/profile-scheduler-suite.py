#!/usr/bin/env python3
"""Four-model cache-off screens and inferencekit load cells on the pinned Mac."""

import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def monitored(argv, env, directory, pid, timeout=1800):
    deadline = time.monotonic() + timeout
    with (directory / "bench.json").open("w") as out, (directory / "bench.stderr").open("w") as err:
        child = subprocess.Popen(argv, env=env, stdout=out, stderr=err, start_new_session=True)
        try:
            while child.poll() is None:
                pressure = host.thermal_pressure()
                competitors = host.competing_work(env, pid)
                if pressure or competitors:
                    raise host.HostUnavailable(f"measurement guard: {pressure=}, {competitors=}")
                if time.monotonic() > deadline:
                    raise TimeoutError("inferencekit cell exceeded cap")
                time.sleep(0.5)
            if child.returncode:
                raise RuntimeError(f"inferencekit exited {child.returncode}, see bench.stderr")
        finally:
            host.stop(child)
    result = json.loads((directory / "bench.json").read_text())
    if "evidence_id" not in result:
        raise ValueError("missing inferencekit evidence")
    return result


def benchmark(args, model, arm, directory, env, pid, prompt_path):
    command = host.IK + ["bench", "serve", "--base-url", host.URL, "--model", model["key"],
        "--api", "completions", "--engine-kind", "llamacpp", "--prompts", str(prompt_path),
        "--rounds", "1", "--warmup", "0", "--seed", "42", "--token-ids", "off",
        "--gpu-name", "Apple M1 Ultra", "--gpu-count", "1", "--gpu-price-usd-h", "0",
        "--engine-version", (suite.SOURCE + ("+" + suite.PATCH if arm == "skippy-patched" else "")
                            if arm.startswith("skippy") else "8954f194837d2ec4d5f67f20c81b84abe63883ba"),
        "--label", arm, "--run-dir", str(directory), "--extra-body",
        json.dumps({"seed": 42, "top_p": 1, "top_k": 0, "min_p": 0,
                    "repeat_penalty": 1, "cache_prompt": False}), "--timeout-s", "1200", "--json"]
    if args.phase == "screen":
        command += ["--mode", "single", "--limit-per-bucket", "4"]
    else:
        command += ["--mode", "load", "--concurrency", str(args.concurrency),
                    "--duration-s", "30", "--max-requests-per-level", str(args.concurrency),
                    "--ref-e2e-ms", str(args.ref_e2e_ms), "--sweep-all",
                    "--slo-ttft-p95-ms", "30000", "--slo-tpot-p95-ms", "1000"]
    host.save(directory / "benchmark-command.json", command)
    return monitored(command, env, directory, pid)


def server_settings(args, roots, model, arm):
    engine, argv = suite.settings(roots[arm], model, arm, args.lanes)
    if engine == "skippy":
        argv[argv.index("--startup-timeout-secs") + 1] = str(args.startup_timeout)
        if args.generation_queue_capacity is not None:
            argv += ["--generation-queue-capacity", str(args.generation_queue_capacity)]
    return engine, argv


def preflight(args, root, report, roots, model, replay):
    fixtures_path = args.cache / f"scheduler-suite-v1-{model['key']}.json"
    fixture = json.loads(fixtures_path.read_text()) if fixtures_path.exists() else None
    for arm in ("llama-slots", "skippy-base", "skippy-patched", "llama-unified"):
        directory = root / ("preflight-" + arm)
        directory.mkdir()
        env = host.clean_env(directory)
        suite.nominal(env, soak=args.cooldown)
        engine, argv = server_settings(args, roots, model, arm)
        print("SETTINGS", model["key"], arm, flush=True)
        with host.server(roots[arm], model, engine, directory, env, args.lanes,
                         diagnostic=True, argv=argv, startup_timeout=args.startup_timeout + 60):
            if fixture is None:
                fixture = {"mixed": replay["fixtures"](model),
                           "load": suite.make_load_prompts(model, replay)}
                host.save(fixtures_path, fixture)
            suite.verify_prompts(model, engine, replay, fixture["load"])
            for name, row in fixture["mixed"].items():
                tokens = replay["tokenize"](model, engine, row["text"])
                if replay["digest"](json.dumps(tokens).encode()) != row["sha256"]:
                    raise ValueError(f"mixed tokenizer mismatch: {name}")
            effective = suite.verify_effective_tokens(model, engine, replay, fixture,
                                                      establish=arm == "llama-slots")
            report["preflight"][arm] = {"argv": argv,
                "effective_prompt_tokens": effective,
                "resolved": suite.resolved(directory / "server.log", arm, args.lanes)}
        host.save(args.output / "suite.json", report)
    host.save(fixtures_path, fixture)
    return fixture


def run(args, root, report):
    identity, candidate, roots, model, replay = suite.inputs(args.cache, args.model, root)
    report.update(identity=identity, candidate=candidate, model=model, preflight={})
    doctor_env = host.clean_env(root)
    with (args.output / "doctor.json").open("w") as out:
        host.command(host.IK + ["doctor", "--json"], doctor_env, stdout=out, timeout=120)
    fixture = preflight(args, root, report, roots, model, replay)
    host.save(args.output / "fixtures.json", fixture)
    for r in args.rounds:
        for i in suite.ORDERS[r]:
            arm = suite.ARMS[i]
            directory = root / f"r{r}-{arm}"
            directory.mkdir()
            env = host.clean_env(directory)
            suite.nominal(env, soak=args.cooldown)
            engine, argv = server_settings(args, roots, model, arm)
            cell = {"round": r, "arm": arm, "complete": False, "directory": str(directory)}
            report["cells"].append(cell)
            host.save(args.output / "suite.json", report)
            print("CELL", args.model, args.phase, args.concurrency, r, arm, flush=True)
            with host.server(roots[arm], model, engine, directory, env, args.lanes,
                             argv=argv, startup_timeout=args.startup_timeout + 60) as child:
                rows = fixture["mixed"]
                cell["warmup"] = replay["wave"](model, rows, (("short-2", 0, 8),),
                                                 False, env, child.pid, capture_text=True)
                suite.nominal(env, child.pid, args.cooldown)
                if args.phase == "screen":
                    cell["waves"] = {}
                    schedules = {"serial-short-a": (("short-0", 0, 128),),
                                 "serial-short-b": (("short-0", 0, 128),),
                                 "serial-long": (("long-0", 0, 24),),
                                 "mixed-c4": replay["WAVES"]["mixed-c4"]}
                    mixed_rows = dict(rows, **{"short-8": rows["short-0"]})
                    for label, schedule in schedules.items():
                        print("WAVE", arm, label, flush=True)
                        cell["waves"][label] = replay["wave"](model, mixed_rows, schedule, False,
                            env, child.pid, capture_text=True)
                        host.save(args.output / "suite.json", report)
                        suite.nominal(env, child.pid, args.cooldown)
                prompt_path = directory / "prompts.jsonl"
                prompt_path.write_text("".join(json.dumps(p) + "\n" for p in fixture["load"]))
                cell["benchmark"] = benchmark(args, model, arm, directory, env, child.pid, prompt_path)
            cell["launch"] = json.loads((directory / "launch.json").read_text())
            cell["before"] = json.loads((directory / "before.json").read_text())
            cell["after"] = json.loads((directory / "after.json").read_text())
            cell["complete"] = True
            host.save(args.output / "suite.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=suite.MODELS, required=True)
    parser.add_argument("--phase", choices=("screen", "load"), required=True)
    parser.add_argument("--round", dest="rounds", type=int, choices=range(4), action="append", required=True)
    parser.add_argument("--concurrency", type=int, choices=(64, 128, 256), default=64)
    parser.add_argument("--ref-e2e-ms", type=float)
    parser.add_argument("--lanes", type=int, choices=(4, 16), default=16)
    parser.add_argument("--cooldown", type=int, default=30)
    parser.add_argument("--startup-timeout", type=int, choices=(300, 1800), default=300)
    parser.add_argument("--generation-queue-capacity", type=int, choices=(256,))
    args = parser.parse_args()
    if args.phase == "load" and (args.ref_e2e_ms is None or args.ref_e2e_ms <= 0):
        parser.error("load requires the frozen baseline screen latency")
    if args.generation_queue_capacity is not None and (
            args.model != "qwen3-coder-30b-a3b-q4-k-m" or
            args.phase != "load" or args.concurrency != 256):
        parser.error("explicit queue retest is registered only for Coder c256 load")
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < 2048:
        resource.setrlimit(resource.RLIMIT_NOFILE, (2048, hard))
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-suite-{args.model}-{args.phase}-{time.time_ns()}"
    root.mkdir()
    report = {"schema": 1, "complete": False, "phase": args.phase, "rounds": args.rounds,
              "lanes": args.lanes, "offered_concurrency": args.concurrency if args.phase == "load" else None,
              "ref_e2e_ms": args.ref_e2e_ms, "prefix_cache": False, "cooldown_s": args.cooldown,
              "startup_timeout_s": args.startup_timeout,
              "generation_queue_capacity": args.generation_queue_capacity,
              "private_root": str(root), "cells": []}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "suite.json", report)
        with tarfile.open(args.output / "suite-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".jsonl", ".log", ".stderr"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
