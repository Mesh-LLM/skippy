#!/usr/bin/env python3
"""Summarize complete four-round suites without pooling requests across rounds."""

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import runpy
import statistics


ARMS = ("skippy-base", "skippy-patched", "llama-slots", "llama-unified")
GROUPS = {
    "Qwen3.5-4B single": ("qwen3.5-4b-r0", "qwen35-4b-r123"),
    "Qwen3.5-4B c64": ("qwen35-c64",),
    "Qwen3.5-4B c128": ("qwen35-c128-retry",),
    "Coder-30B single": ("coder30b-r0", "coder30b-r123"),
    "Coder-30B c64": ("coder30b-c64",),
    "Coder-30B c128": ("coder30b-c128-retry",),
}
METRICS = ("ttft_p50_ms", "ttft_p95_ms", "e2e_p50_ms", "e2e_p95_ms",
           "tpot_p50_ms", "tpot_p95_ms", "output_tok_s_over_client_span")


def quantile(values, fraction):
    values = sorted(values)
    pos = (len(values) - 1) * fraction
    low = int(pos)
    high = min(low + 1, len(values) - 1)
    return values[low] + (pos - low) * (values[high] - values[low])


def source(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def occupancy(requests):
    events = sorted((q[key], delta) for q in requests
                    for key, delta in (("t_send_ns", 1), ("t_end_ns", -1)))
    current = peak = 0
    for _, delta in events:
        current += delta
        assert current >= 0, "negative client occupancy"
        peak = max(peak, current)
    assert current == 0
    span = events[-1][0] - events[0][0]
    average = sum(q["t_end_ns"] - q["t_send_ns"] for q in requests) / span
    return peak, average


def cells_for(path, auditor, sources):
    record = json.loads((path / "suite.json").read_text())
    fixtures = json.loads((path / "fixtures.json").read_text())
    audited = auditor["audit"](record, path / "evidence", fixtures)
    sources.extend(source(path / name) for name in ("suite.json", "fixtures.json"))
    for cell in audited["cells"]:
        request_path = (path / "evidence" / f"r{cell['round']}-{cell['arm']}"
                        / "evidence" / cell["evidence_id"] / "requests.jsonl")
        requests = [json.loads(line) for line in request_path.read_text().splitlines()]
        assert all(q["t_send_ns"] < q["t_end_ns"] for q in requests)
        lag = [q["send_lag_ms"] for q in requests if q["send_lag_ms"] is not None]
        assert len(lag) == len(requests) or record["phase"] == "screen"
        peak, average = occupancy(requests)
        cell.update(tpot_p95_ms=quantile([q["tpot_ms"] for q in requests], .95),
                    peak_client_inflight=peak, time_mean_client_inflight=average,
                    send_lag_p95_ms=quantile(lag, .95) if lag else None,
                    max_send_lag_ms=max(lag) if lag else None)
        cell["latency_slo_pass"] = cell["ttft_p95_ms"] <= 30000 and cell["tpot_p95_ms"] <= 1000
        sources.append(source(request_path))
    return audited


def paired_ratio(base, other):
    # Enumerate the full empirical paired bootstrap (4**4 equally likely samples).
    ratios = [b / a for a, b in zip(base, other)]
    samples = [statistics.median(ratios[i] for i in indices)
               for indices in itertools.product(range(4), repeat=4)]
    point = statistics.median(ratios)
    lower, upper = quantile(samples, .025), quantile(samples, .975)
    return {"median_round_ratio": point, "paired_percentile_95": [lower, upper],
            "round_ratios": ratios,
            "excludes_parity_and_at_least_2pct": (upper < 1 or lower > 1) and abs(point - 1) >= .02}


def summarize(label, audits):
    cells = [c for audit in audits for c in audit["cells"]]
    expected = {(r, arm) for r in range(4) for arm in ARMS}
    assert len(cells) == 16 and {(c["round"], c["arm"]) for c in cells} == expected
    by_arm = {arm: sorted((c for c in cells if c["arm"] == arm), key=lambda c: c["round"])
              for arm in ARMS}
    summaries = {}
    for arm, rows in by_arm.items():
        summaries[arm] = {metric: statistics.median(c[metric] for c in rows)
                          for metric in METRICS + ("time_mean_client_inflight",)}
        lag = [c["send_lag_p95_ms"] for c in rows if c["send_lag_p95_ms"] is not None]
        summaries[arm]["send_lag_p95_ms"] = statistics.median(lag) if lag else None
        max_lag = [c["max_send_lag_ms"] for c in rows if c["max_send_lag_ms"] is not None]
        summaries[arm].update(completed=sum(c["requests"] for c in rows),
                             peak_client_inflight_range=[min(c["peak_client_inflight"] for c in rows),
                                                         max(c["peak_client_inflight"] for c in rows)],
                             max_send_lag_ms=max(max_lag) if max_lag else None,
                             latency_slo_pass_rounds=sum(c["latency_slo_pass"] for c in rows))
    comparisons = []
    for base, other in (("skippy-base", "skippy-patched"), ("llama-slots", "llama-unified"),
                        ("llama-slots", "skippy-patched"), ("llama-unified", "skippy-patched")):
        comparisons.append({"baseline": base, "comparison": other,
                            "metrics": {metric: paired_ratio([c[metric] for c in by_arm[base]],
                                                               [c[metric] for c in by_arm[other]])
                                        for metric in METRICS}})
    mixed = {}
    for arm, rows in by_arm.items():
        if not rows[0]["diagnostic_waves"]:
            continue
        waves = [c["diagnostic_waves"]["mixed-c4"] for c in rows]
        mixed[arm] = {
            "late_ttft_ms": [w["requests"]["short-9"]["ttft_ms"] for w in waves],
            "long2_e2e_ms": [w["requests"]["long-2"]["e2e_ms"] for w in waves],
            "long3_e2e_ms": [w["requests"]["long-3"]["e2e_ms"] for w in waves],
            "anchor_max_gap_ms": [w["anchor"]["event_gap_max_ms"] for w in waves],
            "wave_tok_s": [w["completion_tokens_per_second"] for w in waves],
            "dispatch_slip_ms": [w["max_abs_dispatch_slip_ms"] for w in waves],
        }
    mixed_comparison = {k: paired_ratio(mixed["skippy-base"][k], mixed["skippy-patched"][k])
                        for k in mixed.get("skippy-base", {})}
    return {"label": label, "cells": cells, "summary": summaries, "comparisons": comparisons,
            "mixed_round_metrics": mixed, "mixed_patched_over_base": mixed_comparison,
            "exact_output_mismatches": [x for a in audits for x in a["exact_output_mismatches"]]}


def markdown(groups):
    lines = ["# Completed four-round scheduler statistics", "",
             "Descriptive medians of four fresh-process round metrics; not pooled request percentiles. "
             "95% intervals enumerate all 256 paired round bootstrap samples. Four rounds provide limited "
             "uncertainty coverage and do not establish generalization. All requests in these tables completed; "
             "Coder c256 is excluded because its completion gate failed. Qwen3.5 c128 retains nine exact-output "
             "mismatches against vanilla; its cross-engine timings do not establish equivalent-work speedups.", "",
             "Client in-flight counts include queued HTTP requests, not active GPU sequences. "
             "Load labels are bounded Poisson bursts with frozen baseline latency, not production capacity. "
             "SLO counts use TTFT p95 ≤30,000 ms and TPOT p95 ≤1,000 ms and are separate from completion/correctness gates.", ""]
    for group in groups:
        lines += [f"## {group['label']}", "",
                  "| Arm | Completed | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | Output tok/s | Peak client in-flight range | Mean client in-flight | Lag p95/max ms | SLO rounds |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for arm, row in group["summary"].items():
            pair = lambda name: f"{row[name + '_p50_ms']:.1f}/{row[name + '_p95_ms']:.1f}"
            peak = row["peak_client_inflight_range"]
            lag = (f"{row['send_lag_p95_ms']:.2f}/{row['max_send_lag_ms']:.2f}"
                   if row['send_lag_p95_ms'] is not None else "N/A")
            lines.append(f"| {arm} | {row['completed']} | {pair('ttft')} | {pair('e2e')} | {pair('tpot')} | "
                         f"{row['output_tok_s_over_client_span']:.2f} | {peak[0]}–{peak[1]} | "
                         f"{row['time_mean_client_inflight']:.2f} | {lag} | "
                         f"{row['latency_slo_pass_rounds']}/4 |")
        lines += ["", "Ratios below are patched/base; below 1 favors patched latency, above 1 favors patched throughput. "
                  "All four pairings and per-round values are retained in the JSON.", "",
                  "| Metric | Median paired ratio | Paired 95% interval | Excludes parity and ≥2% effect |",
                  "|---|---:|---:|---|"]
        for metric, value in group["comparisons"][0]["metrics"].items():
            lo, hi = value["paired_percentile_95"]
            lines.append(f"| {metric} | {value['median_round_ratio']:.3f} | {lo:.3f}–{hi:.3f} | "
                         f"{'Yes' if value['excludes_parity_and_at_least_2pct'] else 'No'} |")
        lines.append("")
        if group["mixed_round_metrics"]:
            lines += ["Unprofiled mixed-c4 custom-client diagnostics, four-round medians. "
                      "Dispatch slip is the maximum across rounds; it measures deviation from the fixed arrival offsets.", "",
                      "| Arm | Late short TTFT ms | Long-2 E2E ms | Long-3 E2E ms | Anchor max token gap ms | Wave tok/s | Max dispatch slip ms |",
                      "|---|---:|---:|---:|---:|---:|---:|"]
            for arm, metrics in group["mixed_round_metrics"].items():
                values = [max(v) if k == "dispatch_slip_ms" else statistics.median(v)
                          for k, v in metrics.items()]
                lines.append("| " + arm + " | " + " | ".join(f"{v:.2f}" for v in values) + " |")
            lines += ["", "Mixed diagnostic patched/base ratios (paired 95% intervals):", ""]
            for metric, value in group["mixed_patched_over_base"].items():
                lo, hi = value["paired_percentile_95"]
                lines.append(f"- {metric}: {value['median_round_ratio']:.3f} ({lo:.3f}–{hi:.3f}).")
            lines.append("")
    lines += ["Unprofiled mixed-screen diagnostics (including every long-request latency and anchor gap) "
              "remain in each JSON cell's `diagnostic_waves`; they use a separate custom client, not inferencekit.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("target/scheduler-four-models/artifacts"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    auditor = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-suite-audit.py")))
    sources, groups = [], []
    for label, paths in GROUPS.items():
        groups.append(summarize(label, [cells_for(args.root / p, auditor, sources) for p in paths]))
    result = {"method": "Four-round medians, exact empirical paired bootstrap 256 samples, percentile95; descriptive only",
              "sources": sources, "groups": groups}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    args.markdown.write_text(markdown(groups))
    print(f"Verified and summarized {sum(len(g['cells']) for g in groups)} cells in {len(groups)} groups")


if __name__ == "__main__":
    main()
