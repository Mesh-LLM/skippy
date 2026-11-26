#!/usr/bin/env python3
"""Verify saved cache-off suite cells and report bounded descriptive metrics."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import statistics

from lib import profile_scheduler_suite as suite


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(record, root, fixtures):
    require(record["complete"] and "error" not in record, "job incomplete or failed")
    require(record["identity"]["source_sha"] == suite.SOURCE, "baseline source drift")
    require(record["candidate"]["patch_sha256"] == suite.PATCH, "candidate drift")
    require(record["prefix_cache"] is False, "prefix cache enabled")
    expected = {(r, arm) for r in record["rounds"] for arm in suite.ARMS}
    actual = [(cell["round"], cell["arm"]) for cell in record["cells"]]
    require(len(actual) == len(expected) and set(actual) == expected, "missing/duplicate cells")
    summary = runpy.run_path(str(Path(__file__).with_name("profile-kv-scheduler-summary.py")))
    prompts = {p["id"]: p for p in fixtures["load"]}
    results, outputs = [], {}
    for cell in record["cells"]:
        label, r = cell["arm"], cell["round"]
        directory = root / Path(cell["directory"]).name
        require(cell["complete"], f"incomplete cell {directory}")
        preflight = record["preflight"][label]
        argv = list(cell["launch"]["argv"])
        require(argv == preflight["argv"], f"timed/preflight configuration drift {label}")
        native = suite.resolved(root / f"preflight-{label}" / "server.log", label, record["lanes"])
        require(native == preflight["resolved"], "native settings evidence mismatch")
        for boundary in ("before", "after"):
            require(cell[boundary]["native_thermal_pressure_level"] == 0, "thermal boundary failed")
        if label.startswith("skippy"):
            queue = record.get("generation_queue_capacity")
            flag = "--generation-queue-capacity"
            if queue is None:
                require(flag not in argv, "unregistered queue override")
            else:
                require(queue == 256 and record["model"]["key"] == "qwen3-coder-30b-a3b-q4-k-m"
                        and record["phase"] == "load" and record["offered_concurrency"] == 256,
                        "unregistered queue experiment")
                require(flag in argv and argv[argv.index(flag) + 1] == str(queue),
                        "queue override differs from registered setting")
            for flag in ("--prefix-cache", "--kv-cache-disk"):
                require(argv[argv.index(flag) + 1] == "off", f"{flag} enabled")
        else:
            require("--no-cache-prompt" in argv and argv[argv.index("--cache-ram") + 1] == "0",
                    "vanilla prefix caching enabled")
        bench = cell["benchmark"]
        if record["phase"] == "load":
            require(bench["kind"] == "capacity", "expected load capacity wrapper")
            measurement = bench["data"]["measurement"]
            require(measurement.startswith("ev_") and "/" not in measurement, "invalid measurement ID")
            bench = json.loads((directory / "evidence" / measurement / "evidence.json").read_text())
            require(bench["evidence_id"] == measurement and bench["kind"] == "serve_benchmark",
                    "load measurement identity mismatch")
        require(bench["coverage"]["complete"], f"inferencekit coverage incomplete {directory}")
        require(bench["provenance"]["tool_version"] == "0.1.1", "inferencekit version drift")
        evidence = directory / "evidence" / bench["evidence_id"]
        request_path = evidence / "requests.jsonl"
        artifact = next(a for a in bench["artifacts"] if a["role"] == "requests")
        require(sha(request_path) == artifact["sha256"], "request evidence digest mismatch")
        requests = [json.loads(line) for line in request_path.read_text().splitlines()]
        count = 4 if record["phase"] == "screen" else record["offered_concurrency"]
        require(len(requests) == count, f"expected {count} requests, got {len(requests)}")
        require(all(q["ok"] and not q["error"] and q["finish_reason"] == "length"
                    and q["n_tokens"] == 32 and not q.get("count_mismatch") for q in requests),
                "request failure, length, or accounting mismatch")
        require(len({q["id"] for q in requests}) == count, "duplicate request prompts")
        saved = [json.loads(p.read_text()) for p in (directory / "outputs" / label).rglob("*.json")
                 if p.name != "index.json"]
        require(len(saved) == count, f"missing saved output records {label}")
        require({q["id"] for q in saved} == {q["id"] for q in requests}, "output/request ID mismatch")
        cached = []
        for q in saved:
            require(q["id"] in prompts and q.get("salt") in (None, ""), "unexpected prompt/salt")
            require(q["usage"]["prompt_tokens"] == prompts[q["id"]]["native_prompt_tokens"],
                    f"input token count mismatch {q['id']}")
            require(q["usage"]["completion_tokens"] == 32, "output usage mismatch")
            cached.append((q["usage"].get("prompt_tokens_details") or {}).get("cached_tokens"))
            outputs[(r, label, "inferencekit", q["id"])] = hashlib.sha256(q["text"].encode()).hexdigest()
        require(all(value in (None, 0) for value in cached), "cached prompt tokens observed")
        waves = {}
        for name, wave in cell.get("waves", {}).items():
            for request_id, q in wave["requests"].items():
                require(q["finish_reason"] == "length", "mixed request incomplete")
                require(q["cached_tokens"] in (None, 0), "mixed prompt cache hit")
                require(hashlib.sha256(q["text"].encode()).hexdigest() == q["text_sha256"],
                        "mixed output digest mismatch")
                cached.append(q["cached_tokens"])
                outputs[(r, label, name, request_id)] = q["text_sha256"]
            waves[name] = summary["summarize_wave"](wave["requests"], "short-8" if name == "mixed-c4" else "short-0")
        end_to_end = [q["e2e_ms"] for q in requests]
        wall_ms = (max(q["t_end_ns"] for q in requests) - min(q["t_send_ns"] for q in requests)) / 1e6
        results.append({"round": r, "arm": label, "evidence_id": bench["evidence_id"],
            "requests": count, "cache_counter_verdict": "PASS" if all(v == 0 for v in cached) else "UNPROVEN",
            "ttft_p50_ms": statistics.median(q["ttft_ms"] for q in requests),
            "ttft_p95_ms": summary["percentile"]([q["ttft_ms"] for q in requests], 0.95),
            "e2e_p50_ms": statistics.median(end_to_end),
            "e2e_p95_ms": summary["percentile"](end_to_end, 0.95),
            "tpot_p50_ms": statistics.median(q["tpot_ms"] for q in requests),
            "output_tok_s_over_client_span": 1000 * sum(q["n_tokens"] for q in requests) / wall_ms,
            "max_send_lag_ms": max((q.get("send_lag_ms") or 0 for q in requests)),
            "diagnostic_waves": waves})
    differences = []
    for (r, arm, wave, prompt), digest in outputs.items():
        if arm != "skippy-base":
            continue
        for other in ("skippy-patched", "llama-slots", "llama-unified"):
            target = (r, other, wave, prompt)
            require(target in outputs, f"missing paired output {target}")
            if outputs[target] != digest:
                differences.append({"round": r, "reference": arm, "candidate": other,
                                    "wave": wave, "prompt": prompt})
    repeats = []
    for r in record["rounds"]:
        for arm in suite.ARMS:
            a, b = [(r, arm, wave, "short-0") for wave in ("serial-short-a", "serial-short-b")]
            if a in outputs:
                repeats.append({"round": r, "arm": arm, "exact": outputs[a] == outputs[b]})
    return {"model": record["model"]["key"], "phase": record["phase"], "cells": results,
            "exact_output_mismatches": differences, "same_process_repeat_controls": repeats,
            "quality": "UNPROVEN; exact text checks are a screen, not full quality qualification",
            "comparison": "Descriptive until all four registered rounds and correctness gates pass"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = json.loads((args.artifacts / "suite.json").read_text())
    fixtures = json.loads((args.artifacts / "fixtures.json").read_text())
    result = audit(record, args.evidence_root, fixtures)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Audited {len(result['cells'])} cells, {len(result['exact_output_mismatches'])} output mismatches")


if __name__ == "__main__":
    main()
