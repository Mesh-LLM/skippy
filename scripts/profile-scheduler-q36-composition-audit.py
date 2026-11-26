#!/usr/bin/env python3
"""Audit Qwen3.6 composition corners against the sealed original/current runs."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

from lib import profile_scheduler_suite as suite


ARMS = ("skippy-original-fixed", "skippy-combined-original")
PROMPTS = ("short-0", "long-2")
MODES = ("normal", "normal-repeat", "logprobs", "logprobs-repeat")
ROOT = Path("target/scheduler-fixes")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts, "unsafe archive path")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile() and target.suffix in (".json", ".log"),
                        "unsafe archive entry")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as handle:
                    target.write_bytes(handle.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    folder = args.artifacts
    spec = ROOT / "qwen36-composition-isolation.spec.json"
    inputs = read(spec.with_suffix(".inputs.json"))
    transport = read(spec.with_suffix(".transport.json"))
    for index, expected in enumerate(transport["chunks"]):
        require(read(folder / f"stage-{index}/stage.json") == expected,
                "sealed stage receipt changed")
    extract(folder / "correctness-evidence.tar.gz", folder / "evidence")
    report = read(folder / "correctness-controls.json")
    composition = read(folder / "composition.json")
    original = read(ROOT / "artifacts/qwen36-current-correctness/correctness-controls.json")
    require(report["complete"] and "error" not in report and len(report["cells"]) == 4,
            "incomplete four-process report")
    require(report["fixture_sha256"] == inputs["fixture_sha256"] and
            report["model"]["key"] == inputs["model"] and
            report["normal_transport"] == "non-streaming", "fixture/model/transport changed")
    pin = read(Path("ci/inference-artifacts.json"))["qwen36-35b-a3b"]
    model = report["model"]
    require(model["repo"] == pin["repo"] and model["revision"] == pin["revision"] and
            [(f["sha256"], f["expected_bytes"]) for f in model["files"]] ==
            [(f["sha256"], f["size"]) for f in pin["files"]], "exact CI model pin")
    require(composition["pin"] == pin and composition["model"] == model and
            composition["arms"] == report["candidate"], "composition identity")
    require(report["identity"]["files"]["skippy"] == inputs["original_binary_sha256"] and
            report["identity"]["files"]["native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json"] ==
            inputs["original_manifest_sha256"], "original binary/native identity")
    require(composition["arms"][ARMS[0]] == {
                "server_sha256": inputs["original_binary_sha256"],
                "native_manifest_sha256": inputs["fixed_serving_manifest_sha256"]} and
            composition["arms"][ARMS[1]] == {
                "server_sha256": inputs["combined_binary_sha256"],
                "native_manifest_sha256": inputs["original_manifest_sha256"]},
            "corner identities")
    fixture = read(Path("target/scheduler-four-models/artifacts/qwen36-r0/fixtures.json"))
    expected_prompts = {name: fixture["mixed"][name] for name in PROMPTS}
    require({p["id"] for p in report["prompts"]} == set(PROMPTS), "prompt selection")
    for prompt in report["prompts"]:
        source = expected_prompts[prompt["id"]]
        require(prompt["text_sha256"] == hashlib.sha256(source["text"].encode()).hexdigest() and
                prompt["input_tokens"] == source["tokens"] and
                prompt["output_tokens"] == inputs["cases"][prompt["id"]],
                "prompt text/token identity")
    require([(c["repeat"], c["arm"]) for c in report["cells"]] ==
            [(0, ARMS[0]), (0, ARMS[1]), (1, ARMS[1]), (1, ARMS[0])],
            "fresh process order")
    observed = {}
    for cell in report["cells"]:
        arm, repeat = cell["arm"], cell["repeat"]
        directory = folder / "evidence" / f"r{repeat}-{arm}"
        require(cell["complete"] and len(cell["responses"]) == 8 and
                [(item["prompt"], item["mode"]) for item in cell["responses"]] ==
                [(p, m) for p in PROMPTS for m in MODES], "cell response schedule")
        require(suite.resolved(directory / "server.log", arm, 16) == cell["resolved"] and
                cell["resolved"]["gpu_layers"] == 41, "native settings/full Metal/F16 KV")
        before, after = (read(directory / name) for name in ("before.json", "after.json"))
        require(before["native_thermal_pressure_level"] == after["native_thermal_pressure_level"] == 0,
                "thermal pressure")
        launch = read(directory / "launch.json")["argv"]
        require("--kv-cache-disk" in launch and launch[launch.index("--kv-cache-disk") + 1] == "off" and
                "--prefix-cache" in launch and launch[launch.index("--prefix-cache") + 1] == "off",
                "cache launch settings")
        for item in cell["responses"]:
            path = directory / item["file"]
            require(digest(path) == item["sha256"], "response file hash")
            response = read(path)
            choice = response["choices"][0]
            usage = response["usage"]
            require(usage["prompt_tokens"] == expected_prompts[item["prompt"]]["tokens"] and
                    usage["completion_tokens"] == inputs["cases"][item["prompt"]] and
                    choice["finish_reason"] == "length" and
                    hashlib.sha256(choice["text"].encode()).hexdigest() == item["text_sha256"],
                    "response token/completion/text integrity")
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if cached is None:
                cached = (response.get("timings") or {}).get("cache_n")
            require(cached == 0, "cache counter")
            if item["mode"].startswith("logprobs"):
                require(choice.get("logprobs"), "missing logprobs")
            observed[repeat, arm, item["prompt"], item["mode"]] = choice["text"]
    require(len(observed) == 32, "total response count")
    reference = {}
    for cell in original["cells"]:
        if cell["repeat"] != 0:
            continue
        arm = cell["arm"]
        directory = ROOT / "artifacts/qwen36-current-correctness/evidence" / f"r0-{arm}"
        for item in cell["responses"]:
            if item["mode"] == "normal":
                reference[arm, item["prompt"]] = read(directory / item["file"])["choices"][0]["text"]
    comparisons = []
    for arm in ARMS:
        for prompt in PROMPTS:
            texts = [observed[repeat, arm, prompt, mode] for repeat in (0, 1) for mode in MODES]
            comparisons.append({"arm": arm, "prompt": prompt,
                "text_sha256": hashlib.sha256(texts[0].encode()).hexdigest(),
                "within_arm_repeat_and_mode_exact": len(set(texts)) == 1,
                "matches_original_server_original_metal": texts[0] == reference["skippy-base", prompt],
                "matches_combined_server_fixed_metal": texts[0] == reference["skippy-patched", prompt],
                "matches_vanilla_slots": texts[0] == reference["llama-slots", prompt],
                "matches_vanilla_unified": texts[0] == reference["llama-unified", prompt]})
    result = {"passed_integrity_gates": True, "job_spec_sha256": digest(spec),
              "responses_verified": 32, "fresh_processes": 4,
              "corner_comparisons": comparisons,
              "scope": "Two Qwen3.6 serial prompts only; component attribution, not quality or timing clearance."}
    (folder / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(comparisons, indent=2))


if __name__ == "__main__":
    main()
