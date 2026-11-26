#!/usr/bin/env python3
"""Independently audit pinned current-candidate Qwen3.6/Flash serial controls."""

import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import tarfile

from lib import profile_scheduler_suite as suite


CASES = {
    "qwen36": ("qwen3.6-35b-a3b-q4-k-m", ("short-0", "long-2"),
               "target/scheduler-four-models/artifacts/qwen36-correctness"),
    "flash": ("qwen3.8-flash-next-iq4-xs", ("load-0001", "short-9"),
              "target/scheduler-four-models/artifacts/flash-correctness"),
    "coder": ("qwen3-coder-30b-a3b-q4-k-m", ("load-0001", "short-9"), None),
}
MODES = ("normal", "normal-repeat", "logprobs", "logprobs-repeat")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            parts = PurePosixPath(member.name)
            require(not parts.is_absolute() and ".." not in parts.parts, "unsafe archive path")
            target = destination.joinpath(*parts.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile() and target.suffix in (".json", ".log"), "unsafe archive entry")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as handle:
                    target.write_bytes(handle.read())


def token_rows(choice):
    value = choice["logprobs"]
    if "content" in value:
        return [(r["token"], r["logprob"], {x["token"]: x["logprob"] for x in r["top_logprobs"]})
                for r in value["content"]]
    return list(zip(value["tokens"], value["token_logprobs"], value["top_logprobs"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=tuple(CASES), required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--stem")
    args = parser.parse_args()
    key, prompts, prior_dir = CASES[args.model]
    root = args.artifacts
    stem = args.stem or f"{args.model}-current-correctness"
    require(stem in (f"{args.model}-current-correctness",
                     f"{args.model}-current-correctness-v2",
                     f"{args.model}-current-correctness-v3"), "unregistered audit stem")
    spec_path = Path("target/scheduler-fixes") / f"{stem}.spec.json"
    inputs = read(spec_path.with_suffix(".inputs.json"))
    require(inputs["model"] == key, "spec model drift")
    transport = read(spec_path.with_suffix(".transport.json"))
    for index, expected in enumerate(transport["chunks"]):
        require(read(root / f"stage-{index}/stage.json") == expected, "stage receipt drift")
    extract(root / "correctness-evidence.tar.gz", root / "evidence")
    report = read(root / "correctness-controls.json")
    composition = read(root / "current-composition.json")
    prior = read(Path(prior_dir) / "correctness-controls.json") if prior_dir else None
    fixture_name = {"qwen36": "qwen36-r0", "flash": "flash-screen-r0",
                    "coder": "coder30b-r0"}[args.model]
    fixture_path = Path("target/scheduler-four-models/artifacts") / fixture_name / "fixtures.json"
    fixture = read(fixture_path)
    require(report["complete"] and "error" not in report and report["model"]["key"] == key,
            "incomplete or wrong-model report")
    require(report["fixture_sha256"] == inputs["fixture_sha256"] and
            (prior is None or prior["fixture_sha256"] == inputs["fixture_sha256"]),
            "fixture drift")
    require(report["identity"]["source_sha"] == suite.SOURCE and
            report["identity"]["files"]["skippy"] == inputs["original_binary_sha256"] and
            report["identity"]["files"]["llama-server"] == inputs["vanilla_binary_sha256"] and
            report["identity"]["files"]["native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json"] ==
            inputs["original_manifest_sha256"], "original runtime identity")
    combined = read(Path("target/scheduler-fixes/artifacts/combined-logprob-build/scheduler-fix-build.json"))
    build = read(Path("target/scheduler-fixes/artifacts/q4-metal-components-v2/metal-components.json"))
    fixed = next(v for v in build["variants"] if v["composition"]["arm"] == "fixed")
    pin = read(Path("ci/inference-artifacts.json"))[inputs["pin_key"]]
    require(report["candidate"] == combined and composition["combined_binary"] == combined and
            composition["fixed_component"] == fixed and composition["pin"] == pin and
            composition["serving_manifest_sha256"] == inputs["serving_manifest_sha256"],
            "candidate or bundle identity")
    require(inputs["component_audit_sha256"] == digest(Path("target/scheduler-fixes/artifacts/q4-metal-components-v2/audit.json")) and
            inputs["combined_audit_sha256"] == digest(Path("target/scheduler-fixes/artifacts/combined-logprob-retest/audit.json")),
            "prior qualified build identity")
    model = report["model"]
    require(model["repo"] == pin["repo"] and model["revision"] == pin["revision"] and
            [(f["sha256"], f["expected_bytes"]) for f in model["files"]] ==
            [(f["sha256"], f["size"]) for f in pin["files"]], "exact CI model pin")
    rows = {}
    for name in prompts:
        source = (next(x for x in fixture["load"] if x["id"] == name) if name.startswith("load-")
                  else fixture["mixed"][name])
        text = source["messages"][0]["content"] if name.startswith("load-") else source["text"]
        amount = source["native_prompt_tokens"] if name.startswith("load-") else source["tokens"]
        rows[name] = (hashlib.sha256(text.encode()).hexdigest(), amount)
    require(len(report["prompts"]) == len(prompts) and
            {p["id"] for p in report["prompts"]} == set(prompts), "registered prompt selection")
    for item in report["prompts"]:
        require((item["text_sha256"], item["input_tokens"]) == rows[item["id"]] and
                item["output_tokens"] == composition["cases"][item["id"]], "prompt body or count")
    expected_order = [(repeat, arm) for repeat, arms in
                      ((0, suite.ARMS), (1, tuple(reversed(suite.ARMS)))) for arm in arms]
    require(len(report["cells"]) == 8 and
            [(cell["repeat"], cell["arm"]) for cell in report["cells"]] == expected_order,
            "fresh process order")
    values = {}
    for cell in report["cells"]:
        arm, repeat = cell["arm"], cell["repeat"]
        directory = root / "evidence" / f"r{repeat}-{arm}"
        require(cell["complete"] and len(cell["responses"]) == 8, "cell response count")
        expected_responses = [(prompt, mode) for prompt in prompts for mode in MODES]
        require([(r["prompt"], r["mode"]) for r in cell["responses"]] == expected_responses,
                "response schedule")
        require(suite.resolved(directory / "server.log", arm, 16) == cell["resolved"],
                "native settings/offload/F16 KV")
        before, after = (read(directory / name) for name in ("before.json", "after.json"))
        require(before["native_thermal_pressure_level"] == after["native_thermal_pressure_level"] == 0,
                "thermal pressure")
        argv = read(directory / "launch.json")["argv"]
        if arm.startswith("skippy"):
            require("--kv-cache-disk" in argv and argv[argv.index("--kv-cache-disk") + 1] == "off",
                    "Skippy disk cache")
        else:
            require("--no-cache-prompt" in argv and
                    ("--no-kv-unified" in argv if arm == "llama-slots" else "--kv-unified" in argv),
                    "vanilla cache/layout")
        for item in cell["responses"]:
            path = directory / item["file"]
            require(digest(path) == item["sha256"], "response file hash")
            response = read(path)
            choice = response["choices"][0]
            usage = response["usage"]
            prompt = item["prompt"]
            require(usage["prompt_tokens"] == rows[prompt][1] and
                    usage["completion_tokens"] == composition["cases"][prompt] and
                    choice["finish_reason"] == "length" and
                    hashlib.sha256(choice["text"].encode()).hexdigest() == item["text_sha256"],
                    "response token/completion/text integrity")
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if cached is None:
                cached = (response.get("timings") or {}).get("cache_n")
            require(cached == 0, "cache counter")
            if item["mode"].startswith("logprobs"):
                tokens = token_rows(choice)
                require(len(tokens) == composition["cases"][prompt] and
                        all(math.isfinite(lp) and all(math.isfinite(x) for x in top.values())
                            for _, lp, top in tokens), "top-five logprob integrity")
            values[repeat, arm, prompt, item["mode"]] = choice
    require(len(values) == 64, "total response count")
    repeat_checks = [{"arm": arm, "prompt": prompt,
                      "exact": len({values[repeat, arm, prompt, mode]["text"]
                                    for repeat in (0, 1) for mode in MODES}) == 1}
                     for arm in suite.ARMS for prompt in prompts]
    calibration = {}
    if prior is not None:
        for arm in suite.ARMS:
            for prompt in prompts:
                old = read(Path(prior_dir) / "evidence" / f"r0-{arm}" / f"{prompt}-normal.json")
                calibration[f"{arm}/{prompt}"] = (old["choices"][0]["text"] ==
                                                     values[0, arm, prompt, "normal"]["text"])
    checks = []
    divergences = []
    for prompt in prompts:
        for repeat in (0, 1):
            candidate = values[repeat, "skippy-patched", prompt, "normal"]
            for reference in ("llama-slots", "llama-unified"):
                target = values[repeat, reference, prompt, "normal"]
                checks.append({"prompt": prompt, "repeat": repeat, "reference": reference,
                               "candidate_exact": candidate["text"] == target["text"]})
                for index, (a, b) in enumerate(zip(token_rows(values[repeat, reference, prompt, "logprobs"]),
                                                   token_rows(values[repeat, "skippy-patched", prompt, "logprobs"]))):
                    if a[0] != b[0]:
                        alternative = a[2].get(b[0])
                        divergences.append({"prompt": prompt, "repeat": repeat, "reference": reference,
                                            "token_index": index, "reference_token": a[0],
                                            "candidate_token": b[0],
                                            "reference_logprob_gap": a[1] - alternative if alternative is not None else None,
                                            "candidate_in_reference_top5": alternative is not None})
                        break
    result = {"model": key, "job_spec_sha256": digest(spec_path), "responses_verified": 64,
              "fresh_processes": 8, "model_pin_exact": True, "full_metal_and_f16_kv": True,
              "caches_off": True, "thermal_nominal": True, "original_control_calibration": calibration,
              "original_control_calibration_scope": ("archived selected serial controls" if prior else
                  "not available for Coder; original suite and c256 remain historical"),
              "candidate_cross_engine": checks, "first_divergences": divergences,
              "exact_cross_engine_pass": all(c["candidate_exact"] for c in checks),
              "within_arm_repeat_and_mode": repeat_checks,
              "selected_exact_pass": (all(c["candidate_exact"] for c in checks) and
                                      all(c["exact"] for c in repeat_checks)),
              "scope": "Two selected serial prompts only; no concurrent, task quality, capacity or timing clearance."}
    (root / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"calibration": calibration, "exact_cross_engine_pass": result["exact_cross_engine_pass"],
                      "first_divergences": divergences}, indent=2))


if __name__ == "__main__":
    main()
