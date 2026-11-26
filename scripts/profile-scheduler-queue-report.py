#!/usr/bin/env python3
"""Audit and summarize the registered Coder queue256 retest, preserving failures."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy



def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    args = parser.parse_args()
    root, prior = args.artifacts, args.prior
    record, old = [read(p / "suite.json") for p in (root, prior)]
    for key in ("identity", "candidate", "model", "lanes", "ref_e2e_ms", "rounds", "offered_concurrency"):
        assert record[key] == old[key], f"changed registered identity/setting: {key}"
    assert record["generation_queue_capacity"] == 256 and record["cooldown_s"] == 120
    assert (root / "fixtures.json").read_bytes() == (prior / "fixtures.json").read_bytes()
    thermal = read(root / "thermal-observations-audit.json")
    assert thermal["cells_checked"] == 16 and not thermal["timing_alert_overlap"]
    stat = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-statistics.py")))
    auditor = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-suite-audit.py")))
    sources = [stat["source"](root / name) for name in
               ("thermal-observations-audit.json", "suite-evidence.tar.gz")]
    audit = stat["cells_for"](root, auditor, sources)
    assert all(c["cache_counter_verdict"] == "PASS" for c in audit["cells"])
    repeats, seen = [], {}
    for cell in record["cells"]:
        arm, round_id = cell["arm"], cell["round"]
        directory = root / "evidence" / Path(cell["directory"]).name
        launch = list(cell["launch"]["argv"])
        if arm.startswith("skippy"):
            index = launch.index("--generation-queue-capacity")
            assert launch[index + 1] == "256"
            del launch[index:index + 2]
        expected_launch = [x.replace(old["private_root"] + "/candidate/", record["private_root"] + "/candidate/")
                           for x in old["preflight"][arm]["argv"]]
        assert launch == expected_launch, "unexpected launch change"
        inputs = list((directory / "inputs" / arm).rglob("*.json"))
        assert len(inputs) == 256
        old_dir = prior / "evidence" / f"r{round_id}-{arm}"
        for path in inputs:
            assert read(path) == read(old_dir / path.relative_to(directory)), "request body changed"
        for path in (directory / "outputs" / arm).rglob("*.json"):
            if path.name == "index.json":
                continue
            response = read(path)
            key = (arm, response["id"])
            digest = hashlib.sha256(response["text"].encode()).hexdigest()
            if key in seen and seen[key] != digest:
                repeats.append({"round": round_id, "arm": arm, "prompt": response["id"]})
            seen.setdefault(key, digest)
    result = stat["summarize"]("Coder c256, explicit Skippy waiting queue256", [audit])
    result.update(sources=sources, cross_round_output_mismatches=repeats,
                  method="Median of four round metrics; 256 empirical paired bootstrap samples, percentile95. Limited four-round coverage.")
    (root / "statistics.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = ["# Coder explicit queue256 retest", "",
             "Pinned Q4_K_M Coder on M1 Ultra, full Metal, 16 active lanes, context 8192, F16 KV, caches off. "
             "Four balanced fresh-process rounds, 256 requests each, frozen reference 749.4787085ms. "
             "Queue 256 is explicit in both Skippy arms; the runtime default is unchanged.", "",
             f"Completed 4096/4096; cross-arm text mismatches {len(audit['exact_output_mismatches'])}; "
             f"cross-round mismatches {len(repeats)}. Request bodies and identities match the original failed c256 experiment.", "",
             "| Arm | Output tok/s | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | SLO rounds |",
             "|---|---:|---:|---:|---:|---:|"]
    for arm, row in result["summary"].items():
        pair = lambda name: f"{row[name + '_p50_ms']:.1f}/{row[name + '_p95_ms']:.1f}"
        lines.append(f"| {arm} | {row['output_tok_s_over_client_span']:.2f} | {pair('ttft')} | "
                     f"{pair('e2e')} | {pair('tpot')} | {row['latency_slo_pass_rounds']}/4 |")
    lines += ["", "Values are medians of round metrics, not pooled percentiles. TPS is aggregate output tokens over client span. "
              "SLO requires p95 TTFT ≤30 s and p95 TPOT ≤1 s. This bounded burst is not a production-capacity claim.", "",
              "Saved JY thermal alerts do not overlap timed client windows (including 1 s boundary uncertainty). "
              "Native settings/full offload were verified in separate diagnostic preflights, with matching timed launch arguments; timed Skippy logs omit native settings. Native boundaries were nominal and the 0.5 s thermal/contention guard completed. Sampling cannot exclude every transient.", "",
              "Patched/base paired ratios (95% empirical intervals):", ""]
    for metric, row in result["comparisons"][0]["metrics"].items():
        low, high = row["paired_percentile_95"]
        lines.append(f"- {metric}: {row['median_round_ratio']:.4f} ({low:.4f}–{high:.4f}); "
                     f"excludes parity and ≥2% effect: {row['excludes_parity_and_at_least_2pct']}.")
    lines += ["", "Exact text agreement is a correctness screen, not full quality qualification. "
              "Original queue 128 failures remain preserved. No engine fix or widened default is claimed.", ""]
    (root / "REPORT.md").write_text("\n".join(lines))
    print(json.dumps(result["summary"], indent=2))
    print(f"Cross-round mismatches: {len(repeats)}")


if __name__ == "__main__":
    main()
