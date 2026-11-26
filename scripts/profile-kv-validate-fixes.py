#!/usr/bin/env python3
"""Replay the three observed regressions against two local Skippy builds."""

import argparse
from functools import partial
import hashlib
import json
from pathlib import Path
import runpy
import time
from urllib.parse import urlparse
import socket

import lib.profile_kv_host as host


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def model_record(path, key):
    literal = path.resolve(strict=True)
    return {"path": str(literal), "key": key, "files": [{"sha256": digest(literal)}]}


def exact_prompt(model, tokenize, name, length):
    # Each prefix starts with a distinct subject. Preserve token IDs and shapes
    # across builds even if a case uses a different local model revision.
    subject = name.replace("-", " ")
    text = f"Field journal for {subject}. Continue the journal.\n" + (
        "The orchard keeper records the rain and checks the trees, tools and paths each morning.\n"
        * 400
    )
    lo, hi = 0, len(text)
    while lo <= hi:
        middle = (lo + hi) // 2
        tokens = tokenize(model, "skippy", text[:middle])
        if len(tokens) == length:
            return {"text": text[:middle], "tokens": length,
                    "sha256": hashlib.sha256(json.dumps(tokens).encode()).hexdigest()}
        if len(tokens) < length:
            lo = middle + 1
        else:
            hi = middle - 1
    raise ValueError(f"could not build {length}-token prompt {name}")


def prepare(arms, model, workload, output, max_pressure):
    lengths = {name: 384 if name.startswith("short") else 4096
               for wave in workload["WAVES"].values() for name, _, _ in wave}
    cache_tokens = 2048 if max_pressure else 4096
    lengths.update({f"cache-{i}": cache_tokens for i in range(8)})
    rows, resolved = {}, {}
    for arm, cache in arms.items():
        directory = output / f"preflight-{model['key']}-{arm}"
        directory.mkdir()
        env = host.clean_env(directory)
        host.require_idle(env, max_pressure=max_pressure)
        engine, argv = workload["settings"](cache, model, "skippy", False)
        with host.server(cache, model, engine, directory, env, lanes=4, diagnostic=True, argv=argv):
            if not rows:
                rows = {name: exact_prompt(model, workload["tokenize"], name, length)
                        for name, length in lengths.items()}
            resolved[arm] = workload["read_native_settings"](directory / "server.log", "skippy", True)
    host.save(output / f"{model['key']}-fixtures.json", rows)
    return rows, resolved


def require_free_port():
    address = urlparse(host.URL)
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((address.hostname, address.port))


def compare_cases(cases, summarize, pairs):
    observations = []
    for mode in dict.fromkeys(case["mode"] for case in cases):
        for pair in range(pairs):
            base, candidate = (
                next(case["waves"] for case in cases
                     if case["mode"] == mode and case["pair"] == pair and case["arm"] == arm)
                for arm in ("base", "candidate")
            )
            mismatches = [
                [name, key] for name, wave in base.items() for key, row in wave["requests"].items()
                if row["text_sha256"] != candidate[name]["requests"][key]["text_sha256"]
            ]
            metrics = {}
            for arm, waves in (("base", base), ("candidate", candidate)):
                if mode == "cache":
                    metrics[arm] = {
                        name: summarize(waves[name]["requests"], None)
                        for name in ("cold-before", "reuse", "reuse-after-churn", "cold-after")
                        if name in waves
                    }
                elif mode == "cache-anchor":
                    metrics[arm] = summarize(waves["serial-short"]["requests"], "short-0")
                else:
                    metrics[arm] = summarize(waves["mixed-c4"]["requests"], "short-8")
            observations.append({"mode": mode, "pair": pair, "output_mismatches": mismatches,
                                 "metrics": metrics})
    return observations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--hybrid-model", type=Path, required=True)
    parser.add_argument("--dense-model", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=("cold", "cache-mixed", "cache-anchor", "cache"), action="append")
    parser.add_argument("--pairs", type=int, choices=(1, 2), default=2)
    parser.add_argument("--diagnostic", action="store_true",
                        help="Check real outputs and scheduling under local load without timing qualification.")
    args = parser.parse_args()
    require_free_port()
    # This local replay tolerates an installed Ollama daemon only while the
    # loopback inventory confirms it has no models loaded.
    guard = partial(host.competing_work, allow_idle_ollama=True)
    if args.diagnostic:
        def inference_competitors(env, allowed_pid=None):
            inference = {"skippy", "llama-server", "llama-cli", "llama-bench", "ollama"}
            return [row for row in guard(env, allowed_pid) if row["program"] in inference]
        host.competing_work = inference_competitors
    else:
        host.competing_work = guard
    max_pressure = 2 if args.diagnostic else 0
    args.output.mkdir(parents=True)
    replay = runpy.run_path(str(Path(__file__).with_name("profile-kv-scheduler.py")))
    arms = {}
    runtime = args.runtime_dir.resolve(strict=True)
    report = {"patch_sha256": digest(args.patch),
              "runtime_manifests": {m.parent.name: digest(m) for m in runtime.glob("*/manifest.json")},
              "programs": {}, "models": {}, "cases": [], "diagnostic": args.diagnostic,
              "max_native_thermal_pressure": max_pressure}
    if not report["runtime_manifests"]:
        raise ValueError("no runtime manifest in the supplied bundle root")
    for arm, binary in (("base", args.baseline), ("candidate", args.candidate)):
        directory = args.output / arm
        directory.mkdir()
        (directory / "skippy").symlink_to(binary.resolve(strict=True))
        (directory / "native-runtimes").symlink_to(runtime, target_is_directory=True)
        report["programs"][arm] = digest(binary)
        arms[arm] = directory
    models = {"cache": model_record(args.dense_model, "qwen3-4b-local-q4-k-m"),
              "hybrid": model_record(args.hybrid_model, "qwen3.5-4b-q4-k-m")}
    fixtures = {}
    for mode in args.case or ("cold", "cache-mixed", "cache"):
        model = models["cache" if mode == "cache" else "hybrid"]
        key = model["key"]
        if key not in fixtures:
            fixtures[key], resolved = prepare(arms, model, replay, args.output, max_pressure)
            report["models"][key] = {"model": model, "resolved_settings": resolved}
        # Fresh AB/BA arms with the same fixture and cooldown in both positions.
        order = ("base", "candidate", "candidate", "base")[: 2 * args.pairs]
        for index, arm in enumerate(order):
            directory = args.output / f"{mode}-{index}-{arm}"
            directory.mkdir()
            time.sleep(12)
            trace_mode = "cache-mixed-trace" if args.diagnostic and mode == "cache-mixed" else mode
            options = argparse.Namespace(cache=arms[arm], mode=trace_mode, focus_mixed=True,
                                         wave_cooldown=6 if mode == "cache" else 2,
                                         compact_cache_check=args.diagnostic,
                                         capture_text=True,
                                         max_pressure=max_pressure)
            print(mode, index, arm, flush=True)
            _, resolved, waves, trace = replay["replay"](
                options, model, "skippy", fixtures[key], None, directory, directory
            )
            report["cases"].append({"mode": mode, "arm": arm, "pair": index // 2,
                                    "resolved": resolved, "waves": waves, "trace": trace,
                                    "thermal_before": json.loads((directory / "before.json").read_text())["native_thermal_pressure_level"],
                                    "thermal_after": json.loads((directory / "after.json").read_text())["native_thermal_pressure_level"]})
            host.save(args.output / "validation.json", report)
    summarize = runpy.run_path(str(Path(__file__).with_name("profile-kv-scheduler-summary.py")))
    report["comparisons"] = compare_cases(report["cases"], summarize["summarize_wave"], args.pairs)
    host.save(args.output / "validation.json", report)


if __name__ == "__main__":
    main()
