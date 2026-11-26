#!/usr/bin/env python3
"""Replay the recorded Qwen3.6/Flash serial failures on the combined candidate."""

import argparse
import json
from pathlib import Path
import runpy
import sys

from lib import profile_scheduler_suite as suite


CASES = {
    "qwen3.6-35b-a3b-q4-k-m": {"short-0": 128, "long-2": 24},
    "qwen3.8-flash-next-iq4-xs": {"load-0001": 32, "short-9": 48},
    "qwen3-coder-30b-a3b-q4-k-m": {"load-0001": 32, "short-9": 48},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screen", action="store_true")
    args = parser.parse_args()
    inputs = json.loads(Path("current-correctness-inputs.json").read_text())
    key = inputs["model"]
    if key not in CASES:
        raise ValueError("unregistered model")
    if args.screen and key != "qwen3-coder-30b-a3b-q4-k-m":
        raise ValueError("current balanced screen registered only for Coder")
    digest = runpy.run_path("scripts/profile-scheduler-native-probe.py")["digest"]
    for name, expected in inputs["files"].items():
        if digest(Path(name)) != expected:
            raise ValueError("sealed input changed: " + name)
    build = json.loads(Path("component-build.json").read_text())
    combined = json.loads(Path("combined-build.json").read_text())
    original_manifest = args.cache / "native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json"
    if digest(original_manifest) != inputs["original_manifest_sha256"]:
        raise ValueError("original native manifest changed")
    original = json.loads(original_manifest.read_text())
    component = runpy.run_path("scripts/profile-scheduler-q4-retest.py")["component"]
    materialize = runpy.run_path("scripts/profile-scheduler-serving-bundle.py")["materialize"]
    fixed = next(v for v in build["variants"] if v["composition"]["arm"] == "fixed")
    bundle = component(args.cache, fixed, original, digest)
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())[inputs["pin_key"]]
    admission = json.loads(Path("admission.json").read_text())
    admitted = next(m for m in admission["models"] if m["key"] == key)
    if not admitted["verified"] or admitted["repo"] != pin["repo"] or admitted["revision"] != pin["revision"]:
        raise ValueError("model admission identity changed")
    if [(f["sha256"], f["expected_bytes"]) for f in admitted["files"]] != [
        (f["sha256"], f["size"]) for f in pin["files"]
    ]:
        raise ValueError("model admission shards changed")
    original_inputs = suite.inputs

    def candidate_inputs(cache, model_key, directory):
        identity, _, roots, model, replay = original_inputs(cache, model_key, directory)
        if model_key != key or model["repo"] != pin["repo"] or model["revision"] != pin["revision"]:
            raise ValueError("model pin changed")
        if len(model["files"]) != len(pin["files"]):
            raise ValueError("model shard changed")
        for index, (file, pinned) in enumerate(zip(model["files"], pin["files"])):
            path = Path(file.get("path", model["path"] if index == 0 else ""))
            if (file["sha256"] != pinned["sha256"] or
                    file.get("expected_bytes", pinned["size"]) != pinned["size"] or
                    path.name != pinned["path"] or path.stat().st_size != pinned["size"]):
                raise ValueError("model shard changed")
            # Older Coder admission records contain only name and SHA. The
            # original suite has already hashed the bytes; bind the missing
            # size to the exact CI pin for the common report/audit schema.
            file["expected_bytes"] = pinned["size"]
        if identity["files"]["skippy"] != inputs["original_binary_sha256"] or \
                identity["files"]["llama-server"] != inputs["vanilla_binary_sha256"]:
            raise ValueError("original binary changed")
        binary = Path(combined["candidate_path"]) / "skippy"
        if digest(binary) != combined["binary_sha256"]:
            raise ValueError("combined binary changed")
        view = directory / "combined-fixed"
        view.mkdir()
        (view / "skippy").symlink_to(binary)
        destination = view / "native-runtimes/skippy-native-runtime-darwin-aarch64-metal"
        destination.parent.mkdir()
        materialize(bundle, destination, inputs["serving_manifest_sha256"])
        roots["skippy-patched"] = view
        if args.screen:
            suite.PATCH = combined["patch_sha256"]
        return identity, combined, roots, model, replay

    suite.inputs = candidate_inputs
    suite.CORRECTNESS_CASES = {key: CASES[key]}
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = args.cache / f"scheduler-suite-v1-{key}.json"
    if digest(fixture) != inputs["fixture_sha256"]:
        raise ValueError("archived screen fixture changed")
    (args.output / "current-composition.json").write_text(json.dumps({
        "model": key, "pin_key": inputs["pin_key"], "pin": pin,
        "admission_sha256": inputs["admission_sha256"],
        "fixed_component": fixed, "combined_binary": combined,
        "serving_manifest_sha256": inputs["serving_manifest_sha256"],
        "cases": CASES[key],
    }, indent=2) + "\n")
    if args.screen:
        sys.argv = ["profile-scheduler-suite.py", "--cache", str(args.cache),
                    "--output", str(args.output), "--model", key, "--phase", "screen",
                    "--cooldown", "120", "--startup-timeout", "1800"]
        for round_index in range(4):
            sys.argv += ["--round", str(round_index)]
        runpy.run_path("scripts/profile-scheduler-suite.py", run_name="__main__")
    else:
        sys.argv = ["profile-scheduler-correctness.py", "--cache", str(args.cache),
                    "--output", str(args.output), "--model", key,
                    "--startup-timeout", "1800", "--fixture-sha256", inputs["fixture_sha256"]]
        runpy.run_path("scripts/profile-scheduler-correctness.py", run_name="__main__")


if __name__ == "__main__":
    main()
