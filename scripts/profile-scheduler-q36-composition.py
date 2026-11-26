#!/usr/bin/env python3
"""Isolate server and Q4 Metal composition on the two failed Qwen3.6 prompts."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys

from lib import profile_scheduler_suite as suite


MODEL = "qwen3.6-35b-a3b-q4-k-m"
ARMS = ("skippy-original-fixed", "skippy-combined-original")
CASES = {"short-0": 128, "long-2": 24}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = json.loads(Path("composition-inputs.json").read_text())
    for name, expected in inputs["files"].items():
        if digest(Path(name)) != expected:
            raise ValueError("sealed input changed: " + name)
    if inputs["model"] != MODEL or inputs["cases"] != CASES:
        raise ValueError("registered model or cases changed")
    original_inputs = suite.inputs
    build = json.loads(Path("component-build.json").read_text())
    combined = json.loads(Path("combined-build.json").read_text())
    fixed = next(v for v in build["variants"] if v["composition"]["arm"] == "fixed")
    if digest(Path("component-audit.json")) != inputs["component_audit_sha256"]:
        raise ValueError("qualified component audit changed")
    if combined["binary_sha256"] != inputs["combined_binary_sha256"] or \
            fixed["manifest_sha256"] != inputs["fixed_manifest_sha256"]:
        raise ValueError("registered composition changed")
    component = runpy.run_path("scripts/profile-scheduler-q4-retest.py")["component"]
    materialize = runpy.run_path("scripts/profile-scheduler-serving-bundle.py")["materialize"]

    def composition_inputs(cache, model_key, directory):
        identity, _, _, model, replay = original_inputs(cache, model_key, directory)
        if model_key != MODEL or identity["files"]["skippy"] != inputs["original_binary_sha256"] or \
                identity["files"]["native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json"] != \
                inputs["original_manifest_sha256"]:
            raise ValueError("original binary/native identity changed")
        pin = json.loads(Path("ci/inference-artifacts.json").read_text())["qwen36-35b-a3b"]
        if model["repo"] != pin["repo"] or model["revision"] != pin["revision"] or \
                [(f["sha256"], f["expected_bytes"]) for f in model["files"]] != \
                [(f["sha256"], f["size"]) for f in pin["files"]]:
            raise ValueError("exact CI model shard drift")
        source = cache / "native-runtimes/skippy-native-runtime-darwin-aarch64-metal"
        if digest(source / "manifest.json") != inputs["original_manifest_sha256"]:
            raise ValueError("original native manifest drift")
        binary = Path(combined["candidate_path"]) / "skippy"
        if digest(binary) != inputs["combined_binary_sha256"]:
            raise ValueError("combined server binary drift")
        bundle = component(cache, fixed, json.loads((source / "manifest.json").read_text()), digest)
        original_fixed = directory / ARMS[0]
        original_fixed.mkdir()
        (original_fixed / "skippy").symlink_to(cache / "skippy")
        destination = original_fixed / "native-runtimes/skippy-native-runtime-darwin-aarch64-metal"
        destination.parent.mkdir()
        materialize(bundle, destination, inputs["fixed_serving_manifest_sha256"])
        combined_original = directory / ARMS[1]
        combined_original.mkdir()
        (combined_original / "skippy").symlink_to(binary)
        (combined_original / "native-runtimes").symlink_to(cache / "native-runtimes")
        roots = {ARMS[0]: original_fixed, ARMS[1]: combined_original}
        composition = {
            ARMS[0]: {"server_sha256": inputs["original_binary_sha256"],
                      "native_manifest_sha256": inputs["fixed_serving_manifest_sha256"]},
            ARMS[1]: {"server_sha256": inputs["combined_binary_sha256"],
                      "native_manifest_sha256": inputs["original_manifest_sha256"]},
        }
        (args.output / "composition.json").write_text(json.dumps({
            "model": model, "pin": pin, "arms": composition,
            "fixed_component": fixed, "combined_build": combined,
        }, indent=2) + "\n")
        return identity, composition, roots, model, replay

    suite.inputs = composition_inputs
    suite.ARMS = ARMS
    suite.CORRECTNESS_CASES = {MODEL: CASES}
    sys.argv = ["profile-scheduler-correctness.py", "--cache", str(args.cache),
                "--output", str(args.output), "--model", MODEL,
                "--startup-timeout", "1800"]
    runpy.run_path("scripts/profile-scheduler-correctness.py", run_name="__main__")


if __name__ == "__main__":
    main()
