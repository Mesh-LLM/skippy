#!/usr/bin/env python3
"""Replay the scheduler and resident-KV fixes on the pinned Metal builds."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import tempfile
import time

import lib.profile_kv_host as host


ARMS = ("skippy-base", "skippy-patched", "llama-slots", "llama-unified")
ORDERS = ((0, 1, 3, 2), (1, 2, 0, 3), (2, 3, 1, 0), (3, 0, 2, 1))
MODEL_KEYS = {"cold": "qwen3.5-4b-q4-k-m", "cache-mixed": "qwen3.5-4b-q4-k-m",
              "cache": "qwen3-4b-q4-k-m"}


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def qualified_arms(base, patch, root):
    identity = json.loads((base / "build-identity.json").read_text())
    if base.name != identity["source_sha"]:
        raise ValueError("baseline source identity disagrees with cache path")
    files = identity["files"]
    for name, digest in files.items():
        path = base / ("llama-build/bin/llama-server" if name == "llama-server" else name)
        if file_digest(path) != digest:
            raise ValueError(f"baseline artifact changed: {name}")
    candidate = base / f"scheduler-fix-{patch}"
    built = json.loads((candidate / "scheduler-fix-build.json").read_text())
    if (built["source"] != identity["source_sha"] or built["patch_sha256"] != patch
            or file_digest(candidate / "skippy") != built["binary_sha256"]):
        raise ValueError("wrong candidate build")
    linked = root / "candidate"
    linked.mkdir()
    (linked / "skippy").symlink_to(candidate / "skippy")
    (linked / "native-runtimes").symlink_to(base / "native-runtimes")
    return identity, built, {label: linked if label == "skippy-patched" else base for label in ARMS}


def preflight(replay, roots, model, fixture, cache_enabled, directory, arms):
    resolved = {}
    order = ARMS if fixture is not None else ("llama-slots", "skippy-base", "skippy-patched", "llama-unified")
    for label in order:
        if label not in arms and fixture is not None:
            continue
        print("NATIVE SETTINGS", model["key"], label, flush=True)
        arm = "skippy" if label.startswith("skippy-") else label
        location = directory / label
        location.mkdir()
        env = host.clean_env(location)
        host.require_idle(env)
        engine, argv = replay["settings"](roots[label], model, arm, cache_enabled)
        with host.server(roots[label], model, engine, location, env, lanes=4,
                         diagnostic=True, argv=argv):
            if fixture is None:
                fixture = replay["fixtures"](model)
            for name, row in fixture.items():
                tokens = replay["tokenize"](model, engine, row["text"])
                if replay["digest"](json.dumps(tokens).encode()) != row["sha256"]:
                    raise ValueError(f"fixture token mismatch: {label} {name}")
            observed = replay["read_native_settings"](location / "server.log", arm, True)
        resolved[label] = {"argv": argv, "settings": observed}
    return fixture, resolved


def run(args, root):
    scripts = Path(__file__).parent
    replay = runpy.run_path(str(scripts / "profile-kv-scheduler.py"))
    args.output.mkdir(parents=True, exist_ok=True)
    identity, build, roots = qualified_arms(args.cache, args.patch, root)
    models = {m["key"]: m for m in json.loads((args.cache / "models.json").read_text())}
    report = {"identity": identity, "candidate": build, "preflight": {}, "cells": [],
              "mode": args.mode, "rounds": args.rounds, "mixed_anchor": True,
              "wave_cooldown_seconds": args.wave_cooldown}
    arms = set(args.arms or ARMS)
    key = MODEL_KEYS[args.mode]
    model = models[key]
    if file_digest(Path(model["path"])) != model["files"][0]["sha256"]:
        raise ValueError("model bytes changed")
    fixture_path = args.cache / f"scheduler-kv-fixtures-v1-{key}.json"
    fixture = json.loads(fixture_path.read_text()) if fixture_path.is_file() else None
    preflight_root = root / "preflight"
    preflight_root.mkdir()
    fixture, report["preflight"] = preflight(replay, roots, model, fixture,
                                              args.mode.startswith("cache"), preflight_root, arms)
    report["model"] = model
    report["fixtures"] = {name: {k: v for k, v in row.items() if k != "text"}
                          for name, row in fixture.items()}
    report_path = args.output / "qualification.json"
    host.save(report_path, report)
    for round_index in args.rounds:
        for index in ORDERS[round_index]:
            label = ARMS[index]
            if label not in arms:
                continue
            arm = "skippy" if label.startswith("skippy-") else label
            directory = root / f"r{round_index}-{label}"
            directory.mkdir()
            env = host.clean_env(directory)
            time.sleep(args.cell_cooldown)
            host.require_idle(env)
            print("QUALIFICATION", args.mode, round_index, label, flush=True)
            options = argparse.Namespace(cache=roots[label], mode=args.mode, focus_mixed=True,
                                         wave_cooldown=args.wave_cooldown)
            _, settings, waves, _ = replay["replay"](
                options, model, arm, fixture, None, directory, directory)
            before = json.loads((directory / "before.json").read_text())
            after = json.loads((directory / "after.json").read_text())
            levels = [r["native_thermal_pressure_level"] for r in (before, after)]
            if any(levels):
                raise host.HostUnavailable(f"{label} thermal boundaries: {levels}")
            report["cells"].append({"arm": label, "round": round_index, "waves": waves,
                                    "directory": directory.name, "resolved": settings, "model": key,
                                    "thermal_before": levels[0], "thermal_after": levels[1],
                                    "swap_before": before["sysctl vm.swapusage"],
                                    "swap_after": after["sysctl vm.swapusage"]})
            host.save(report_path, report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=MODEL_KEYS, required=True)
    parser.add_argument("--round", dest="rounds", type=int, choices=range(4),
                        action="append", required=True)
    parser.add_argument("--arm", dest="arms", choices=ARMS, action="append")
    parser.add_argument("--cell-cooldown", type=int, default=12)
    parser.add_argument("--wave-cooldown", type=int, default=4)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="scheduler-kv-qualification-", dir=os.getcwd()) as scratch:
        run(args, Path(scratch))


if __name__ == "__main__":
    main()
