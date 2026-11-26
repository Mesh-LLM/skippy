#!/usr/bin/env python3
"""Check all four PR draft tables against reviewer-visible timing samples."""

from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics


EVIDENCE = Path("evals/kv-profiling/pr-evidence")
DRAFT = Path("evals/kv-profiling/SCHEDULER_PR_DESCRIPTION_DRAFT.md")
TABLES = {
    "qwen35-c128": ("### Qwen3.5-4B", True),
    "coder-c256": ("### Qwen3-Coder-30B-A3B", True),
    "qwen36-original-r0": ("### Qwen3.6-35B-A3B", False),
    "flash-original-r0": ("### Qwen3.8-Flash-Next", False),
}
ARMS = {
    "qwen35-c128": ("Skippy original + fixed Metal", "Skippy combined + fixed Metal",
                     "Vanilla separate slots", "Vanilla unified"),
    "coder-c256": ("Skippy base, queue 256", "Skippy patched, queue 256",
                   "Vanilla separate slots", "Vanilla unified"),
    "qwen36-original-r0": ("Skippy base", "Skippy patched",
                            "Vanilla separate slots", "Vanilla unified"),
    "flash-original-r0": ("Skippy base", "Skippy patched",
                           "Vanilla separate slots", "Vanilla unified"),
}
ARM_KEYS = ("skippy-base", "skippy-patched", "llama-slots", "llama-unified")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quantile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (position - low) * (values[high] - values[low])


def parse_tables(text):
    groups = {}
    for scope, (heading, _) in TABLES.items():
        segment = text.split(heading, 1)[1].split("\n### ", 1)[0]
        rows = [line for line in segment.splitlines() if line.startswith("| ")]
        if len(rows) != 5:
            raise ValueError(f"missing four data rows: {scope}")
        groups[scope] = [[part.strip() for part in line.strip().strip("|").split("|")]
                         for line in rows[1:]]
    return groups


def main():
    manifest = json.loads((EVIDENCE / "manifest.json").read_text())
    timing_file = EVIDENCE / "request-timings.csv"
    if digest(timing_file) != manifest["request_timings_sha256"]:
        raise ValueError("timing snapshot hash changed")
    samples = defaultdict(list)
    with timing_file.open(newline="") as handle:
        for row in csv.DictReader(handle):
            samples[row["model_scope"], row["arm"], int(row["round"])].append(row)
    tables = parse_tables(DRAFT.read_text())
    checked = {}
    for scope, (_, is_load) in TABLES.items():
        checked[scope] = []
        for index, arm in enumerate(ARM_KEYS):
            rounds = sorted(r for (name, a, r) in samples if name == scope and a == arm)
            if rounds != (list(range(4)) if is_load else [0]):
                raise ValueError("missing balanced rounds: " + scope + "/" + arm)
            values = []
            for round_id in rounds:
                rows = samples[scope, arm, round_id]
                span = (max(int(row["t_end_ns"]) for row in rows) -
                        min(int(row["t_send_ns"]) for row in rows)) / 1e9
                values.append({
                    "requests": len(rows),
                    "ttft_p95_ms": quantile([float(row["ttft_ms"]) for row in rows], .95),
                    "e2e_p95_ms": quantile([float(row["e2e_ms"]) for row in rows], .95),
                    "tpot_p95_ms": quantile([float(row["tpot_ms"]) for row in rows], .95),
                    "output_tok_s": sum(int(row["output_tokens"]) for row in rows) / span,
                })
            observed = {key: statistics.median(row[key] for row in values)
                        for key in ("ttft_p95_ms", "e2e_p95_ms", "tpot_p95_ms", "output_tok_s")}
            complete = sum(row["requests"] for row in values)
            if complete != (512 if scope == "qwen35-c128" else 1024 if scope == "coder-c256" else 4):
                raise ValueError("request count changed: " + scope)
            pass_rounds = sum(row["ttft_p95_ms"] <= 30000 and row["tpot_p95_ms"] <= 1000
                              for row in values)
            label = ARMS[scope][index]
            expected = ([label, f"{complete:,}/{complete:,}",
                         f"{observed['ttft_p95_ms']/1000:,.2f} s",
                         f"{observed['e2e_p95_ms']/1000:,.2f} s",
                         f"{observed['tpot_p95_ms']:,.1f} ms",
                         f"{observed['output_tok_s']:,.2f}"] if is_load else
                        [label, f"{complete}/{complete}",
                         f"{observed['ttft_p95_ms']:,.1f} ms",
                         f"{observed['e2e_p95_ms']:,.1f} ms",
                         f"{observed['tpot_p95_ms']:,.2f} ms",
                         f"{observed['output_tok_s']:,.2f}"])
            if is_load:
                expected.append(f"{pass_rounds}/4")
            actual = tables[scope][index]
            if actual != expected:
                raise ValueError(f"PR table mismatch {scope}/{arm}: actual={actual}, expected={expected}")
            checked[scope].append({"arm": arm, "rounds": len(rounds), "complete": complete,
                                   "values": observed, "slo_pass_rounds": pass_rounds})
    result = {"passed": True, "draft_sha256": digest(DRAFT),
              "request_timings_sha256": digest(timing_file), "tables": checked,
              "scope": "Verifies four-table arithmetic and the historical Coder single-user quote; does not pass correctness or non-inferiority."}
    historical = json.loads((EVIDENCE / "coder-single-historical.json").read_text())
    comparison = next(c for c in historical["comparisons"]
                      if c["baseline"] == "llama-slots" and c["comparison"] == "skippy-patched")
    ratio = comparison["metrics"]["ttft_p95_ms"]
    quoted = re.search(r"TTFT p95 ratio was ([\d.]+) with paired\s+95% interval ([\d.]+)–([\d.]+)",
                       DRAFT.read_text())
    if not quoted or tuple(quoted.groups()) != (
        f"{ratio['median_round_ratio']:.4f}",
        f"{ratio['paired_percentile_95'][0]:.4f}",
        f"{ratio['paired_percentile_95'][1]:.4f}",
    ):
        raise ValueError("historical Coder single-user ratio quote differs")
    result["coder_single_historical_sha256"] = digest(EVIDENCE / "coder-single-historical.json")
    path = EVIDENCE / "table-audit.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print("four PR draft tables match 6,176 reviewer-visible request timing samples", digest(path))


if __name__ == "__main__":
    main()
