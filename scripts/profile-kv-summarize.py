#!/usr/bin/env python3
"""Summarize derived trace exports and fresh paired client observations."""

import argparse
import collections
import hashlib
import json
import random
import statistics
from pathlib import Path


def trace_summary(paths):
    summaries = []
    for path in paths:
        artifact = json.loads(path.read_text())
        for trace in artifact["results"]:
            if trace.get("error"):
                raise ValueError(f"trace export failed: {trace}")
            parts = Path(trace["capture"]).parts
            requests = [r for r in trace["requests"] if r.get("complete_in_trace")]
            if not requests or not trace["gpu_active_interval_union_ns"] or not trace["cpu_weight_ns"]:
                raise ValueError(f"trace lacks usable attached-process evidence: {trace['capture']}")
            phases = {}
            for phase in ("prefill_to_first_event", "decode_events"):
                rows = [r["phases"][phase] for r in requests]
                leaves = collections.Counter()
                for row in rows:
                    leaves.update(dict(row["cpu_leaf_weight_ns_top"]))
                phases[phase] = {
                    "request_wall_ms_median": statistics.median(r["wall_ns"] / 1e6 for r in rows),
                    "gpu_active_union_fraction_median": statistics.median(
                        r["gpu_active_union_ns"] / r["wall_ns"] for r in rows),
                    "cpu_leaf_weight_ms_top": [(name, weight / 1e6) for name, weight in leaves.most_common(5)],
                }
            summaries.append({"model": parts[-4], "engine": parts[-3].removeprefix("profile-"),
                              "bucket": parts[-2], "complete_requests": len(requests),
                              "excluded_boundary_requests": len(trace["requests"]) - len(requests),
                              "target_pid": trace["target_pid"], "phases": phases,
                              "artifact": str(path), "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return sorted(summaries, key=lambda x: (x["model"], x["bucket"], x["engine"]))


def timing_summary(root):
    models = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("qwen")):
        cells = {}
        token_rows = {}
        evidence = []
        for cell in sorted(directory.glob("b*-p*-*")):
            block, position, engine = cell.name.split("-", 2)
            round_index = int(block[1:])
            order = ("llama", "skippy") if round_index % 2 == 0 else ("skippy", "llama")
            if position not in ("p0", "p1") or engine != order[int(position[1:])]:
                raise ValueError(f"cell violates registered alternating order: {cell}")
            record = json.loads((cell / "bench.json").read_text())
            request_path = root / "evidence" / record["evidence_id"] / "requests.jsonl"
            rows = [json.loads(line) for line in request_path.read_text().splitlines()]
            # --extend records contain ALL earlier rounds. Select this fresh cell only.
            rows = [row for row in rows if row["round"] == round_index]
            if len(rows) != 4 or any(not r["ok"] or r.get("count_mismatch") for r in rows):
                raise ValueError(f"invalid current-round request observations: {cell}")
            expected_ids = {"p512-0", "p512-1", "p4096-0", "p4096-1"}
            if {row["id"] for row in rows} != expected_ids:
                raise ValueError(f"registered prompts are missing or duplicated: {cell}")
            for row in rows:
                expected = 256 if row["bucket"] == "p512" else 64
                expected_salt = f"ik-r{round_index}" if round_index else None
                if row["n_tokens"] != expected or row["salt"] != expected_salt:
                    raise ValueError(f"wrong generated token count: {cell}")
            before = json.loads((cell / "before.json").read_text())
            after = json.loads((cell / "after.json").read_text())
            if before["native_thermal_pressure_level"] or after["native_thermal_pressure_level"]:
                raise ValueError(f"non-nominal cell boundary: {cell}")
            if (cell / "contention.json").exists() or (cell / "thermal-failure.json").exists():
                raise ValueError(f"invalid host conditions: {cell}")
            counts = json.loads((cell / "prompt-token-counts.json").read_text())["rows"]
            if len(counts) != 4 or {r["id"] for r in counts} != expected_ids or any(
                    r["round"] != round_index or r["salt"] != expected_salt for r in counts):
                raise ValueError(f"native token audit describes a different round: {cell}")
            if (round_index, engine) in cells:
                raise ValueError(f"duplicate cell in paired round: {cell}")
            token_rows[round_index, engine] = counts
            cells[round_index, engine] = rows
            evidence.append({"cell": cell.name, "evidence_id": record["evidence_id"],
                             "current_round_requests": len(rows), "input_tokens": counts})
        if set(cells) != {(r, e) for r in range(6) for e in ("llama", "skippy")}:
            raise ValueError(f"all six paired rounds are required: {directory}")
        for round_index in range(6):
            if token_rows[round_index, "llama"] != token_rows[round_index, "skippy"]:
                raise ValueError(f"tagged tokenizer evidence disagrees: {directory}, round{round_index}")
        results = []
        for bucket in ("p512", "p4096"):
            for metric in ("ttft_ms", "tpot_ms", "e2e_ms"):
                medians = {engine: [statistics.median(r[metric] for r in cells[i, engine]
                                                     if r["bucket"] == bucket) for i in range(6)]
                           for engine in ("llama", "skippy")}
                ratios = [b / a for a, b in zip(medians["llama"], medians["skippy"])]
                rng = random.Random(42)
                draws = sorted(statistics.median(rng.choices(ratios, k=6)) for _ in range(10000))
                results.append({"bucket": bucket, "metric": metric, "paired_cell_medians": medians,
                                "paired_skippy_over_llama_ratios": ratios,
                                "median_ratio": statistics.median(ratios),
                                "ci95_percentile": [draws[249], draws[9749]],
                                "quality_verdict": "UNPROVEN; descriptive timings only"})
        models.append({"model": directory.name, "timed_requests": 48, "cells": evidence, "metrics": results})
    if len(models) != 4:
        raise ValueError("all four registered models are required")
    return models


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-artifact", type=Path, action="append", default=[])
    parser.add_argument("--timing-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"traces": trace_summary(args.trace_artifact), "timing": timing_summary(args.timing_root)
              if args.timing_root else None, "limitations": [
                  "Trace durations include recording overhead and do not establish speed.",
                  "CPU weights sum across threads; leaf ranks are resource footprint, not wall shares.",
                  "Metal rows are command/encoder intervals, not shader-family timings.",
                  "Correctness and general task quality are UNPROVEN; no qualified speedup claim."]}
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
