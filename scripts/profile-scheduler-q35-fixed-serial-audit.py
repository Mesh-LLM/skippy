#!/usr/bin/env python3
"""Audit fresh fixed-Metal Qwen3.5 serial controls and compare saved c128 text."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


ARMS = ("native-fixed", "combined-fixed")
PROMPTS = ("load-0190", "load-0100")


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, label):
    if not condition:
        raise ValueError(label)


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and path.parts[0] != "runtime-",
                    "unsafe archive path")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile() and target.suffix in (".json", ".log"), "unsafe archive entry")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as handle:
                    target.write_bytes(handle.read())


def loaded_text(root, arm, prompt):
    candidates = tuple((root / "evidence" / f"r0-{arm}" / "evidence").glob(
        f"*/outputs/{arm}/load-c128/p512-o32/{prompt}.json"))
    require(len(candidates) == 1, "missing c128 comparator")
    return read(candidates[0])["text"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--loaded", type=Path, required=True)
    parser.add_argument("--prior-serving", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    root = args.artifacts
    extract(root / "q35-fixed-serial-evidence.tar.gz", root / "evidence")
    report = read(root / "q35-fixed-serial.json")
    inputs = read(args.spec.with_suffix(".inputs.json"))
    spec_sha = digest(args.spec)
    require(report["complete"] and "error" not in report and report["inputs"] == inputs,
            "incomplete or unsealed probe")
    require(len(report["cells"]) == 8 and
            [(cell["prompt"], cell["repeat"], cell["arm"]) for cell in report["cells"]] ==
            [(prompt, repeat, arm) for prompt in PROMPTS for repeat in (0, 1)
             for arm in (ARMS if repeat == 0 else tuple(reversed(ARMS)))], "process order")
    build = read(args.build / "metal-components.json")
    combined = read(Path("target/scheduler-fixes/artifacts/combined-logprob-build/scheduler-fix-build.json"))
    pin = read(Path("ci/inference-artifacts.json"))["qwen35-4b"]
    fixture = read(args.loaded / "fixtures.json")
    require(report["build"] == build and report["combined"] == combined and report["pin"] == pin,
            "build or pin drift")
    require(inputs["component_audit_sha256"] == digest(args.build / "audit.json") and
            inputs["serving_audit_sha256"] == digest(args.prior_serving / "audit.json") and
            inputs["loaded_audit_sha256"] == digest(args.loaded / "audit.json"), "prior audit drift")
    require(report["fixture_sha256"] == inputs["fixture_sha256"], "fixture drift")
    require(report["model"]["repo"] == pin["repo"] and report["model"]["revision"] == pin["revision"]
            and report["model"]["files"][0]["sha256"] == pin["files"][0]["sha256"], "model pin")
    fixed_variant = next(v for v in build["variants"] if v["composition"]["arm"] == "fixed")
    require(report["variant"] == fixed_variant, "component variant")
    comparisons = {}
    previous = read(args.prior_serving / "q4-serving.json")
    for cell in report["cells"]:
        prompt, repeat, arm = cell["prompt"], cell["repeat"], cell["arm"]
        directory = root / "evidence" / cell["directory"]
        require(cell["directory"] == f"{prompt}-r{repeat}-{arm}" and cell["complete"], "cell identity")
        metadata = report["arms"][arm]
        expected_binary = (inputs["original_binary_sha256"] if arm == "native-fixed" else
                           combined["binary_sha256"])
        require(metadata["binary_sha256"] == expected_binary and
                metadata["serving_manifest_sha256"] == inputs["serving_manifest_sha256"] and
                metadata["bundle"] == fixed_variant["bundle"], "binary or runtime substitution")
        launch = read(directory / "launch.json")
        require("--kv-cache-disk" in launch["argv"] and
                launch["argv"][launch["argv"].index("--kv-cache-disk") + 1] == "off", "disk cache")
        resolved = suite.resolved(directory / "server.log", "skippy-base", 16)
        require(resolved == cell["resolved"] and resolved["gpu_layers"] == 33,
                "native resolution or offload")
        before, after = (read(directory / name) for name in ("before.json", "after.json"))
        require(before["native_thermal_pressure_level"] == after["native_thermal_pressure_level"] == 0,
                "thermal pressure")
        subject = next(row for row in fixture["load"] if row["id"] == prompt)
        expected_body = host.body(report["model"], subject["messages"][0]["content"], 32, stream=True)
        require(read(directory / "request.json") == expected_body, "request body")
        response_path = directory / "response.json"
        require(digest(response_path) == cell["response_sha256"], "response file hash")
        response = read(response_path)["requests"]["subject"]
        require(response["usage"]["prompt_tokens"] == 512 and
                response["usage"]["completion_tokens"] == 32 and response["cached_tokens"] == 0 and
                response["finish_reason"] == "length" and
                hashlib.sha256(response["text"].encode()).hexdigest() == response["text_sha256"],
                "response token/cache/text integrity")
        key = f"{prompt}/{arm}"
        comparisons.setdefault(key, []).append(response["text"])
        if prompt == "load-0190":
            old_dir = args.prior_serving / "evidence" / f"c1-first-{arm}"
            old_text = read(old_dir / "0-c1.json")["requests"]["subject-00"]["text"]
            require(response["text"] == old_text, "load-0190 fixed serial calibration")
    matrix = {}
    for prompt in PROMPTS:
        matrix[prompt] = {}
        for arm in ARMS:
            texts = comparisons[f"{prompt}/{arm}"]
            require(len(texts) == 2 and texts[0] == texts[1], "fresh-process serial drift")
            c128_arm = "skippy-base" if arm == "native-fixed" else "skippy-patched"
            matrix[prompt][arm] = {
                "serial_sha256": hashlib.sha256(texts[0].encode()).hexdigest(),
                "serial_equals_c128": texts[0] == loaded_text(args.loaded, c128_arm, prompt),
                "prefix": texts[0][:80],
            }
    result = {"job_id": "job_rjhtn7rec3h8neg", "spec_sha256": spec_sha,
              "response_count": 8, "fresh_processes": 8, "full_metal": True,
              "cache_off": True, "model_pin_exact": True, "thermal_nominal": True,
              "matrix": matrix,
              "scope": "Fixed-Metal Qwen3.5 serial controls only. Cross-engine loaded, all-model quality and timing gates remain open."}
    (root / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(matrix, indent=2))


if __name__ == "__main__":
    main()
