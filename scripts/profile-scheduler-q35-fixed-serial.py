#!/usr/bin/env python3
"""Replay two Qwen3.5 prompts with the qualified fixed Metal component."""

import argparse
import json
from pathlib import Path
import runpy
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


ARMS = ("native-fixed", "combined-fixed")
PROMPTS = ("load-0190", "load-0100")


def run(cache, output, root, report):
    digest = runpy.run_path("scripts/profile-scheduler-native-probe.py")["digest"]
    component = runpy.run_path("scripts/profile-scheduler-q4-retest.py")["component"]
    materialize = runpy.run_path("scripts/profile-scheduler-serving-bundle.py")["materialize"]
    replay = runpy.run_path("scripts/profile-kv-scheduler.py")
    inputs = json.loads(Path("q35-fixed-serial-inputs.json").read_text())
    for name, expected in inputs["files"].items():
        if digest(Path(name)) != expected:
            raise ValueError("sealed input drift: " + name)
    build = json.loads(Path("component-build.json").read_text())
    combined = json.loads(Path("combined-build.json").read_text())
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())["qwen35-4b"]
    old_manifest = cache / "native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json"
    if digest(old_manifest) != inputs["original_manifest_sha256"]:
        raise ValueError("original runtime manifest drift")
    original = json.loads(old_manifest.read_text())
    fixture_path = cache / "scheduler-suite-v1-qwen3.5-4b-q4-k-m.json"
    if digest(fixture_path) != inputs["fixture_sha256"]:
        raise ValueError("fixture drift")
    fixture = json.loads(fixture_path.read_text())
    snapshot = (Path("/Users/Shared/jianyang/model-store/huggingface/hub") /
                ("models--" + pin["repo"].replace("/", "--")) / "snapshots" / pin["revision"])
    if len(pin["files"]) != 1:
        raise ValueError("unexpected model shard count")
    shard = pin["files"][0]
    model_path = snapshot / shard["path"]
    if model_path.stat().st_size != shard["size"] or digest(model_path) != shard["sha256"]:
        raise ValueError("model shard drift")
    model = {"key": "qwen3.5-4b-q4-k-m", "repo": pin["repo"], "revision": pin["revision"],
             "path": str(model_path), "files": [{"path": str(model_path), "sha256": shard["sha256"]}]}
    variant = next(v for v in build["variants"] if v["composition"]["arm"] == "fixed")
    bundle = component(cache, variant, original, digest)
    roots = {}
    report.update(inputs=inputs, build=build, combined=combined, pin=pin, model=model,
                  fixture_sha256=digest(fixture_path), variant=variant, arms={})
    for arm in ARMS:
        binary = cache / "skippy" if arm == "native-fixed" else Path(combined["candidate_path"]) / "skippy"
        expected = inputs["original_binary_sha256"] if arm == "native-fixed" else combined["binary_sha256"]
        if digest(binary) != expected:
            raise ValueError("serving binary drift: " + arm)
        linked = root / ("runtime-" + arm)
        linked.mkdir()
        (linked / "skippy").symlink_to(binary)
        destination = linked / "native-runtimes/skippy-native-runtime-darwin-aarch64-metal"
        destination.parent.mkdir()
        materialize(bundle, destination, inputs["serving_manifest_sha256"])
        roots[arm] = linked
        report["arms"][arm] = {"binary": str(binary), "binary_sha256": expected,
                               "bundle": str(bundle), "serving_manifest_sha256": digest(destination / "manifest.json")}
    for prompt in PROMPTS:
        subject = next(row for row in fixture["load"] if row["id"] == prompt)
        if subject["native_prompt_tokens"] != 512 or subject["max_tokens"] != 32:
            raise ValueError("fixture token drift")
        row = {"text": subject["messages"][0]["content"], "tokens": 512}
        for repeat in (0, 1):
            for arm in ARMS if repeat == 0 else tuple(reversed(ARMS)):
                directory = root / f"{prompt}-r{repeat}-{arm}"
                directory.mkdir()
                environment = host.clean_env(directory)
                suite.nominal(environment, soak=60)
                argv = host.launch_args(roots[arm], model, "skippy", 16) + ["--kv-cache-disk", "off"]
                cell = {"prompt": prompt, "repeat": repeat, "arm": arm, "complete": False,
                        "directory": directory.name}
                report["cells"].append(cell)
                print("FIXED_SERIAL", prompt, repeat, arm, flush=True)
                with host.server(roots[arm], model, "skippy", directory, environment, 16,
                                 diagnostic=True, argv=argv) as child:
                    cell["resolved"] = suite.resolved(directory / "server.log", "skippy-base", 16)
                    if cell["resolved"]["gpu_layers"] != 33:
                        raise ValueError("full Metal offload drift")
                    ids = replay["tokenize"](model, "skippy", row["text"])
                    if replay["digest"](json.dumps(ids).encode()) != subject["token_ids_sha256"]:
                        raise ValueError("tokenizer drift")
                    suite.nominal(environment, child.pid, 30)
                    request = host.body(model, row["text"], 32, stream=True)
                    host.save(directory / "request.json", request)
                    reading = replay["wave"](model, {"subject": row}, (("subject", 0, 32),), False,
                                             environment, child.pid, capture_text=True)
                    response = reading["requests"]["subject"]
                    if response["cached_tokens"] != 0 or response["usage"]["completion_tokens"] != 32:
                        raise ValueError("response cache/token drift")
                    host.save(directory / "response.json", reading)
                    cell["response_sha256"] = digest(directory / "response.json")
                cell["complete"] = True
                host.save(output / "q35-fixed-serial.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"q35-fixed-serial-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "cells": [],
              "scope": "Fresh fixed-Metal serial controls for two exact prompts; no loaded or timing claim."}
    try:
        run(args.cache, args.output, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "q35-fixed-serial.json", report)
        with tarfile.open(args.output / "q35-fixed-serial-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if (path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log")
                        and not str(path.relative_to(root)).startswith("runtime-")):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
