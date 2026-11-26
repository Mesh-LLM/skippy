#!/usr/bin/env python3
"""Audit and summarize fixed-arrival scheduler and KV replays."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median


def percentile(values, fraction):
    ranked = sorted(values)
    if not ranked:
        return None
    position = (len(ranked) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ranked[lower] + (ranked[upper] - ranked[lower]) * (position - lower)


def request_metrics(row):
    arrivals = row["events_ms"]
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    count = row["usage"]["completion_tokens"]
    return {"ttft_ms": arrivals[0] - row["send_ms"],
            "e2e_ms": row["end_ms"] - row["send_ms"],
            "tpot_ms": (arrivals[-1] - arrivals[0]) / (count - 1) if count > 1 else None,
            "event_gap_p95_ms": percentile(gaps, 0.95), "event_gap_max_ms": max(gaps, default=None),
            "event_count": len(arrivals)}


def summarize_wave(requests, anchor_key):
    metrics = {name: request_metrics(row) for name, row in requests.items()}
    spends = list(metrics.values())
    makespan = max(row["end_ms"] for row in requests.values()) - min(row["send_ms"] for row in requests.values())
    cached = {name: row.get("cached_tokens", (row["usage"].get("prompt_tokens_details") or {}).get("cached_tokens"))
              for name, row in requests.items()}
    result = {"request_count": len(requests), "makespan_ms": makespan,
              "completion_tokens_per_second": 1000 * sum(r["usage"]["completion_tokens"] for r in requests.values()) / makespan,
              "ttft_ms_p50": median(row["ttft_ms"] for row in spends),
              "ttft_ms_p95": percentile([row["ttft_ms"] for row in spends], 0.95),
              "max_abs_dispatch_slip_ms": max(abs(r["send_ms"] - r["offset_ms"]) for r in requests.values()),
              "prompt_tokens": sum(row["usage"]["prompt_tokens"] for row in requests.values()),
              "cached_tokens_per_request": cached,
              "cached_tokens": sum(cached.values()) if all(value is not None for value in cached.values()) else None,
              "requests": metrics}
    if anchor_key in requests:
        anchor = requests[anchor_key]
        prefill_sends = [row["send_ms"] for name, row in requests.items() if name.startswith("long")]
        anchor_arrivals = anchor["events_ms"]
        long_overlap = [t for t in prefill_sends if anchor_arrivals[0] < t < anchor_arrivals[-1]]
        result["anchor"] = dict(metrics[anchor_key], long_arrivals_during_decode=len(long_overlap))
    return result


def summarize_record(record):
    cells = []
    anchor = {"serial-short": "short-0", "mixed-c2": "short-7", "mixed-c4": "short-8"}
    for cell in record["cells"]:
        waves = {name: value.get("requests", value) for name, value in cell["waves"].items()}
        for name, requests in waves.items():
            for request, row in requests.items():
                stamps = row["events_ms"]
                if not (row["send_ms"] <= stamps[0] <= stamps[-1] <= row["end_ms"]):
                    raise ValueError(f"invalid request timeline: {cell['directory']} {name} {request}")
        if cell.get("thermal_before", 0) or cell.get("thermal_after", 0):
            raise ValueError(f"thermal boundary failed: {cell['directory']}")
        clean = {k: v for k, v in cell.items() if k != "waves"}
        clean["waves"] = {name: summarize_wave(rows, anchor.get(name)) for name, rows in waves.items()}
        if "seed" in waves:
            clean["repeat_output_hash_agreement"] = {
                phase: {name: row["text_sha256"] == waves["seed"][name]["text_sha256"]
                        for name, row in waves[phase].items()}
                for phase in ("reuse", "reuse-after-churn")}
        if record.get("mixed_anchor") and "serial-short" in waves:
            expected = waves["serial-short"]["short-0"]["text_sha256"]
            clean["anchor_output_hash_agreement"] = {
                name: waves[name][key]["text_sha256"] == expected
                for name, key in anchor.items() if name != "serial-short" and name in waves}
        cells.append(clean)
    return {"mode": record["mode"], "matched_anchor_text": bool(record.get("mixed_anchor")),
            "wave_cooldown_seconds": record.get("wave_cooldown_seconds", 0), "cells": cells}


def comparisons(records):
    groups = defaultdict(dict)
    for record in records:
        for cell in record["cells"]:
            key = (record["mode"], record["matched_anchor_text"], record["wave_cooldown_seconds"],
                   cell["model"], cell["round"])
            if cell["arm"] in groups[key]:
                raise ValueError(f"duplicate arm in evidence: {key} {cell['arm']}")
            groups[key][cell["arm"]] = cell
    result = []
    for (mode, matched, wave_cooldown, model, round_index), arms in groups.items():
        for first, second in (("llama-slots", "llama-unified"), ("llama-slots", "skippy"),
                              ("llama-unified", "skippy"), ("skippy", "skippy-ordinary")):
            if first not in arms or second not in arms:
                continue
            phases = {}
            for name, base in arms[first]["waves"].items():
                if name == "warmup":
                    continue
                other = arms[second]["waves"][name]
                phases[name] = {metric: other[metric] / base[metric] for metric in
                                ("makespan_ms", "completion_tokens_per_second", "ttft_ms_p50", "ttft_ms_p95")}
                if "anchor" in base:
                    phases[name]["anchor_gap_max_delta_ms"] = (other["anchor"]["event_gap_max_ms"]
                                                                - base["anchor"]["event_gap_max_ms"])
            result.append({"mode": mode, "model": model, "round": round_index,
                           "wave_cooldown_seconds": wave_cooldown,
                           "matched_anchor_text": matched, "baseline": first, "comparison": second,
                           "waves": phases})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    records = [summarize_record(json.loads(p.read_text())) for p in args.evidence]
    args.output.write_text(json.dumps({"inputs": [str(p) for p in args.evidence],
                                      "runs": records, "matched_round_comparisons": comparisons(records)},
                                     indent=2) + "\n")


if __name__ == "__main__":
    main()
