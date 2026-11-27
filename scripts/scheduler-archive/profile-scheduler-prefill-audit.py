#!/usr/bin/env python3
"""Audit the admission replay without clearing unrelated numerical failures."""

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import runpy

from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(root, prior, build):
    runner = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-prefill-retest.py")))
    trace = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-path-isolation-audit.py")))["driver_trace"]
    tokens = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-correctness-audit.py")))["tokens"]
    record, original, built = read(root / "prefill-retest.json"), read(prior / "path-isolation.json"), read(build)
    require(record["complete"] and "error" not in record, "incomplete retest")
    require(record["patch_sha256"] == runner["FIX"] == built["patch_sha256"], "wrong patch")
    require(record["fixed_candidate"] == built and built["source"] == suite.SOURCE, "fixed build identity changed")
    require(record["identity"] == original["identity"], "baseline/native identity changed")
    require(record["original_candidate"] == original["candidate"], "original candidate changed")
    require(record["model"] == original["model"] and record["model"]["key"] == runner["MODEL"], "model changed")
    require(record["fixture_sha256"] == original["fixture_sha256"] == runner["FIXTURE_SHA"], "fixture changed")
    keys = {(batch, first, arm) for batch in (4096, 512) for first in ("normal", "logprobs")
            for arm in ("skippy-base", "skippy-patched")}
    require(len(record["cells"]) == len(keys), "wrong process count")
    seen, comparisons, traces, choices, failures = set(), [], [], {}, []
    old = prior / "evidence" / "normal-first-skippy-base-long-2"
    for cell in record["cells"]:
        batch, first, arm = cell["batch_size"], cell["first"], cell["arm"]
        key = batch, first, arm
        require(key in keys and key not in seen, "unexpected/duplicate process")
        seen.add(key)
        name = f"b{batch}-{first}-first-{arm}"
        require(cell["directory"] == name, "wrong process directory")
        directory = root / "evidence" / name
        launch = copy.deepcopy(read(old / "launch.json"))
        argv = launch["argv"]
        argv[argv.index("--batch-size") + 1] = str(batch)
        if arm == "skippy-patched":
            linked = Path(record["private_root"]) / "fixed-links" / "candidate"
            argv[0] = str(linked / "skippy")
            argv[argv.index("--runtime-bundle") + 1] = str(linked / "native-runtimes" / Path(argv[argv.index("--runtime-bundle") + 1]).name)
        require(read(directory / "launch.json") == launch, "launch drift")
        native = suite.resolved(directory / "server.log", arm, 16, batch)
        require(native == cell["resolved"], "native evidence mismatch")
        old_native = suite.resolved(old / "server.log", "skippy-base", 16)
        old_native["n_batch"] = [str(batch)]
        require({k: v for k, v in native.items() if k != "kv_buffers"}
                == {k: v for k, v in old_native.items() if k != "kv_buffers"}, "native setting drift")
        pattern = r"size =\s*([\d.]+) MiB.*?K \(f16\):\s*([\d.]+) MiB, V \(f16\):\s*([\d.]+) MiB"
        buffers = [set(re.findall(pattern, "\n".join(v["kv_buffers"]))) for v in (native, old_native)]
        require(buffers[0] and buffers[0] == buffers[1], "KV allocation changed")
        for boundary in ("before", "after"):
            require(read(directory / f"{boundary}.json")["native_thermal_pressure_level"] == 0, "thermal boundary failed")
        modes = (first, "logprobs" if first == "normal" else "normal") * 2
        require(cell["complete"] and len(cell["attempts"]) == 4, "incomplete process")
        for index, (item, mode) in enumerate(zip(cell["attempts"], modes)):
            require(item["mode"] == mode and item["file"] == f"{index}-{mode}.json", "attempt order changed")
            require(item["start_unix_ns"] < item["end_unix_ns"], "invalid request window")
            path = directory / item["file"]
            require(hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], "response hash mismatch")
            old_index = 0 if mode == "normal" else 1
            require(read(directory / f"{index}-{mode}-request.json") == read(old / f"{old_index}-{mode}-request.json"), "request changed")
            response = read(path)
            if batch == 4096 and arm == "skippy-base" and mode == "logprobs":
                require(item["status"] == response["http_status"] == 400
                        and "2048-token configured iteration limit" in response["body"], "baseline failure not reproduced")
                failures.append({"batch": batch, "first": first, "index": index, "response": response})
                continue
            require(item["status"] == 200, "unexpected response failure")
            choice, usage = response["choices"][0], response["usage"]
            require(usage["prompt_tokens"] == 4096 and usage["completion_tokens"] == 24 and choice["finish_reason"] == "length", "completion/token gate")
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if cached is None:
                cached = (response.get("timings") or {}).get("cache_n")
            require(cached == 0, "cache hit or missing counter")
            if mode == "logprobs":
                rows = tokens(choice)
                require(len(rows) == 24 and all(math.isfinite(r["logprob"]) and all(math.isfinite(v) for v in r["top"].values()) for r in rows), "logprob rows invalid")
            previous = read(old / f"{old_index}-{mode}.json")["choices"][0]
            match = choice["text"] == previous["text"]
            logs_match = mode != "logprobs" or tokens(choice) == tokens(previous)
            if batch == 512:
                require(match and logs_match, "batch512 regression")
            comparisons.append({"batch": batch, "first": first, "arm": arm, "index": index,
                                "mode": mode, "archived_batch512_text_exact": match,
                                "archived_batch512_logprobs_exact": logs_match})
            choices[batch, first, arm, index] = choice
            traces.append({"batch": batch, "first": first, "arm": arm, "index": index, "mode": mode,
                           **trace(directory, item)})
    require(seen == keys and len(choices) == 28 and len(failures) == 4, "attempt accounting failed")
    controls = []
    for batch in (4096, 512):
        for mode in ("normal", "logprobs"):
            values = [choice for (b, first, arm, index), choice in choices.items()
                      if b == batch and arm == "skippy-patched" and ((index % 2 == 0) == (first == mode))]
            controls.append({"batch": batch, "mode": mode, "fixed_repeat_text_exact": all(v["text"] == values[0]["text"] for v in values),
                             "fixed_repeat_logprobs_exact": mode != "logprobs" or all(tokens(v) == tokens(values[0]) for v in values)})
    require(all(c["fixed_repeat_text_exact"] and c["fixed_repeat_logprobs_exact"] for c in controls), "fixed repeat discrepancy")
    return {"responses_verified": 28, "expected_baseline_failures_verified": 4,
            "comparisons": comparisons, "controls": controls, "baseline_failures": failures,
            "driver_traces": traces, "scope": "Admission and batch512 regression only. Original numerical and cross-engine gates remain unresolved."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.prior, args.build)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print("verified", result["responses_verified"], "responses and", result["expected_baseline_failures_verified"], "baseline rejections")


if __name__ == "__main__":
    main()
