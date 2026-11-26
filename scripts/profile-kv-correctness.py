#!/usr/bin/env python3
"""Bounded fresh-process controls for the saved hybrid-MoE divergence."""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError

import lib.profile_kv_host as host


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", action="append", choices=("llama-base", "skippy-base", "llama-layout", "skippy-ordinary"))
    parser.add_argument("--top-logprobs", type=int, default=5)
    parser.add_argument("--reuse-verified-model", action="store_true")
    parser.add_argument("--skip-tool-inventory", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / ("followup-correctness-" + str(time.time_ns()))
    root.mkdir()
    env = host.clean_env(root)
    models = json.loads((args.cache / "models.json").read_text())
    model = next(m for m in models if m["key"] == "qwen3.5-35b-a3b-q4-k-m")
    digest = model["files"][0]["sha256"] if args.reuse_verified_model else hashlib.file_digest(
        Path(model["path"]).open("rb"), "sha256").hexdigest()
    if digest != model["files"][0]["sha256"]:
        raise ValueError("pinned model hash changed")
    prompts = [json.loads(line) for line in
               (args.reference_root / model["key"] / "prompts.jsonl").read_text().splitlines()]
    prompt = next(p for p in prompts if p["id"] == "p512-1")
    host.save(args.output / "prompt.json", prompt)
    identity = json.loads((args.cache / "build-identity.json").read_text())
    actual_llama = args.cache / "llama-build/bin/llama-server"
    if hashlib.file_digest(actual_llama.open("rb"), "sha256").hexdigest() != identity["files"]["llama-server"]:
        raise ValueError("actual upstream executable differs from the sealed copy")
    inventory = {}
    for tool in (() if args.skip_tool_inventory else ("gpucapture", "gpudebug", "gputrace", "metal", "xctrace")):
        try:
            result = subprocess.run(["xcrun", tool, "--help"], env=env, capture_output=True,
                                    text=True, timeout=20)
            inventory[tool] = {"exit_code": result.returncode,
                               "help": (result.stdout + result.stderr)[:24000]}
        except subprocess.TimeoutExpired:
            inventory[tool] = {"error": "help timed out after20seconds"}
    host.save(args.output / "metal-tools.json", inventory)
    result = {"private_root": str(root), "identity": identity, "model_sha256": digest,
              "model_verification": "reused verified immutable model-store blob from initial follow-up controls" if args.reuse_verified_model else "sha256 recomputed in this job",
              "prompt_id": prompt["id"], "cases": [], "scope":
              "Diagnostic fixed-prompt A/A and physical-layout/attention-path controls; no speed claims or tolerance relaxation."}
    original = host.launch_args
    variants = (("llama-base", "llama"), ("skippy-base", "skippy"),
                ("llama-layout", "llama"), ("skippy-ordinary", "skippy"))
    for variant, engine in variants:
        if args.variant and variant not in args.variant:
            continue
        for repeat in range(2):
            label = f"{variant}-r{repeat}"
            directory = root / label
            directory.mkdir()
            def launch(cache, selected, selected_engine, lanes=1):
                argv = original(cache, selected, selected_engine, lanes)
                if variant == "llama-layout":
                    argv[argv.index("-np") + 1] = "2"
                    argv.append("--kv-unified")
                if variant == "skippy-ordinary":
                    argv.append("--kv-row-attention=false")
                return argv
            host.launch_args = launch
            print("CONTROL", label, flush=True)
            time.sleep(30)
            host.require_idle(env)
            case = {"label": label, "variant": variant, "repeat": repeat, "engine": engine}
            try:
                with host.server(args.cache, model, engine, directory, env, diagnostic=True) as child:
                    for mode in ("normal", "normal-repeat", "logprobs"):
                        host.require_idle(env, child.pid)
                        request = host.body(model, prompt["messages"][0]["content"], 256)
                        if mode == "logprobs":
                            request["logprobs"] = args.top_logprobs
                        try:
                            response = host.post("/v1/completions", request)
                            host.save(args.output / f"{label}-{mode}.json", response)
                            if response.get("usage", {}).get("completion_tokens") != 256:
                                raise ValueError(f"wrong output length: {response.get('usage')}")
                            case[mode] = {"file": f"{label}-{mode}.json", "usage": response["usage"],
                                          "logprobs_present": response["choices"][0].get("logprobs") is not None}
                        except HTTPError as error:
                            case[mode] = {"error": str(error), "response_body": error.read().decode(errors="replace")[:4000]}
                        except Exception as error:
                            case[mode] = {"error": str(error)}
                    if host.thermal_pressure():
                        raise host.HostUnavailable("non-nominal thermal pressure after correctness case")
            finally:
                host.launch_args = original
                for name in ("launch.json", "before.json", "after.json", "server.log"):
                    if (directory / name).exists():
                        shutil.copy2(directory / name, args.output / (label + "-" + name))
                result["cases"].append(case)
                host.save(args.output / "correctness-controls.json", result)
    print("CONTROLS_COMPLETE", root, flush=True)


if __name__ == "__main__":
    main()
