#!/usr/bin/env python3
"""Compare cold scoring of exact reference token prefixes with decode trajectories."""

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import lib.profile_kv_host as host


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / ("followup-cold-" + str(time.time_ns()))
    root.mkdir()
    env = host.clean_env(root)
    fixture = json.loads(args.fixture.read_text())
    model = next(m for m in json.loads((args.cache / "models.json").read_text()) if m["key"] == fixture["model"])
    prompt_ids = None
    results = {"private_root": str(root), "fixture_sha256": hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
               "cases": [], "scope": "Exact-token cold-prefill control, not teacher-forced sequential decode or full quality qualification."}
    for engine in ("llama", "skippy"):
        for repeat in range(2):
            label = f"{engine}-r{repeat}"
            directory = root / label
            directory.mkdir()
            print("COLD_CONTROL", label, flush=True)
            time.sleep(30)
            host.require_idle(env)
            case = {"engine": engine, "repeat": repeat, "positions": []}
            with host.server(args.cache, model, engine, directory, env) as child:
                for position in fixture["positions"]:
                    host.require_idle(env, child.pid)
                    text = fixture["prompt"] + "".join(row["token"] for row in fixture["reference"][:position])
                    if engine == "llama":
                        actual = host.post("/tokenize", {"content": text, "add_special": False})["tokens"]
                    else:
                        sha = model["files"][0]["sha256"]
                        identity = {"model_id": model["key"], "source_model_sha256": sha,
                                    "tokenizer_id": "gguf-source-sha256:" + sha, "stage_index": 0}
                        actual = host.post("/v1/tokenize", {"text": text, "add_special": False,
                                                          "expected_identity": identity})["token_ids"]
                    if position == 0 and prompt_ids is None:
                        prompt_ids = actual
                        if hashlib.sha256(json.dumps(actual).encode()).hexdigest() != fixture["prompt_token_ids_sha256"]:
                            raise ValueError("registered prompt tokens changed")
                    expected = prompt_ids + [row["id"] for row in fixture["reference"][:position]]
                    observation = {"position": position, "token_ids_exact": actual == expected,
                                   "prompt_tokens": len(actual)}
                    if actual != expected:
                        observation["verdict"] = "UNPROVEN: text prefix did not preserve reference token IDs"
                    else:
                        request = host.body(model, text, 1)
                        request["logprobs"] = 5
                        response = host.post("/v1/completions", request)
                        name = f"{label}-p{position}.json"
                        host.save(args.output / name, response)
                        observation["response"] = name
                        if response["usage"]["prompt_tokens"] != len(expected) or response["usage"]["completion_tokens"] != 1:
                            raise ValueError("cold scoring usage disagrees with exact token context")
                    case["positions"].append(observation)
            for name in ("launch.json", "before.json", "after.json"):
                shutil.copy2(directory / name, args.output / (label + "-" + name))
            results["cases"].append(case)
            host.save(args.output / "cold-controls.json", results)


if __name__ == "__main__":
    main()
