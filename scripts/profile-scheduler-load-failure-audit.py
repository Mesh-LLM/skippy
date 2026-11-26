#!/usr/bin/env python3
"""Preserve bounded queue-failure evidence without relaxing the qualification audit."""

import argparse
import hashlib
import json
from pathlib import Path

from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def audit(root, reference):
    record, prior = [read(p / "suite.json") for p in (root, reference)]
    assert record["complete"] and "error" not in record
    assert record["phase"] == "load" and record["prefix_cache"] is False
    assert record["rounds"] == [0, 1, 2, 3]
    assert record["identity"] == prior["identity"]
    assert record["identity"]["source_sha"] == suite.SOURCE
    assert record["candidate"] == prior["candidate"]
    assert record["candidate"]["patch_sha256"] == suite.PATCH
    models = []
    for model in (record["model"], prior["model"]):
        normalized = dict(model)
        identity = normalized.pop("source_model_sha256", None)
        assert identity is None or identity == suite.source_identity(model["files"])
        models.append(normalized)
    assert models[0] == models[1]
    assert record["ref_e2e_ms"] == prior["ref_e2e_ms"]
    assert (root / "fixtures.json").read_bytes() == (reference / "fixtures.json").read_bytes()
    fixtures = {p["id"]: p for p in read(root / "fixtures.json")["load"]}
    expected = {(r, arm) for r in record["rounds"] for arm in suite.ARMS}
    assert len(record["cells"]) == len(expected)
    assert {(c["round"], c["arm"]) for c in record["cells"]} == expected
    rows, outputs = [], {}
    for cell in record["cells"]:
        arm, r = cell["arm"], cell["round"]
        directory = root / "evidence" / Path(cell["directory"]).name
        assert cell["complete"]
        preflight = record["preflight"][arm]
        assert preflight["effective_prompt_tokens"] == prior["preflight"][arm]["effective_prompt_tokens"]
        assert cell["launch"] == read(directory / "launch.json")
        assert cell["launch"]["argv"] == preflight["argv"]
        prior_argv = list(prior["preflight"][arm]["argv"])
        if arm == "skippy-patched":
            old, new = [v["private_root"] + "/candidate/" for v in (prior, record)]
            prior_argv = [new + v[len(old):] if v.startswith(old) else v for v in prior_argv]
        assert preflight["argv"] == prior_argv
        assert suite.resolved(root / "evidence" / f"preflight-{arm}" / "server.log", arm, record["lanes"]) == preflight["resolved"]
        native_records = []
        for native in (preflight["resolved"], prior["preflight"][arm]["resolved"]):
            normalized = dict(native)
            normalized["kv_buffers"] = [line[line.index("llama_kv_cache:"):] for line in native["kv_buffers"]]
            native_records.append(normalized)
        assert native_records[0] == native_records[1]
        for boundary in ("before", "after"):
            assert cell[boundary] == read(directory / (boundary + ".json"))
            assert cell[boundary]["native_thermal_pressure_level"] == 0
        measurement = cell["benchmark"]["data"]["measurement"]
        assert measurement.startswith("ev_") and "/" not in measurement
        evidence = directory / "evidence" / measurement
        bench = read(evidence / "evidence.json")
        assert bench["coverage"]["complete"] and bench["evidence_id"] == measurement
        assert bench["provenance"]["tool_version"] == "0.1.1"
        request_path = evidence / "requests.jsonl"
        artifact = next(a for a in bench["artifacts"] if a["role"] == "requests")
        assert hashlib.sha256(request_path.read_bytes()).hexdigest() == artifact["sha256"]
        requests = [json.loads(line) for line in request_path.read_text().splitlines()]
        assert len(requests) == record["offered_concurrency"]
        assert len({q["id"] for q in requests}) == len(requests)
        saved = [read(p) for p in (directory / "outputs" / arm).rglob("*.json") if p.name != "index.json"]
        assert len(saved) == len(requests)
        saved = {q["id"]: q for q in saved}
        assert set(saved) == {q["id"] for q in requests}
        failures = []
        for q in requests:
            out = saved[q["id"]]
            assert q["id"] in fixtures and out.get("salt") in (None, "")
            if not q["ok"]:
                assert q["status"] == 429 and "generation queue is full; retry later" in q["error"]
                assert out["error"] and not out["text"]
                failures.append(q["id"])
                continue
            assert not q["error"] and not q.get("count_mismatch")
            assert q["finish_reason"] == "length" and q["n_tokens"] == 32
            assert out["usage"]["prompt_tokens"] == fixtures[q["id"]]["native_prompt_tokens"]
            assert out["usage"]["completion_tokens"] == 32
            assert out["usage"]["prompt_tokens_details"]["cached_tokens"] == 0
            outputs[r, arm, q["id"]] = hashlib.sha256(out["text"].encode()).hexdigest()
        rows.append({"round": r, "arm": arm, "attempted": len(requests),
                     "completed": len(requests) - len(failures), "queue_rejected": len(failures),
                     "rejected_ids": failures})
    mismatches, comparisons, repeats = [], 0, []
    for (r, arm, prompt), digest in outputs.items():
        if arm == "skippy-base":
            for other in suite.ARMS:
                if other == arm or (r, other, prompt) not in outputs:
                    continue
                comparisons += 1
                if outputs[r, other, prompt] != digest:
                    mismatches.append({"round": r, "arm": other, "prompt": prompt})
        if r and (0, arm, prompt) in outputs and outputs[0, arm, prompt] != digest:
            repeats.append({"round": r, "arm": arm, "prompt": prompt})
    return {"qualification": "FAIL", "reason": "Queue rejections violate zero-failure completion gate",
            "cells": rows, "successful_pairwise_comparisons": comparisons,
            "successful_output_mismatches": mismatches, "successful_repeat_mismatches": repeats,
            "scope": "Failure diagnostic only. Rejected requests are not output matches; unequal completed work cannot establish a latency speedup. Full quality remains unproven."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.reference)
    (args.artifacts / "failure-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "cells"}, indent=2))
