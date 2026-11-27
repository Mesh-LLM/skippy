#!/usr/bin/env python3
"""Snapshot small audited scheduler results for review without target/ links."""

import csv
import hashlib
import io
import json
import math
from pathlib import Path


DEST = Path("evals/kv-profiling/pr-evidence")
SOURCES = {
    "qwen35-c128-audit.json": Path("target/scheduler-fixes/artifacts/q35-c128-fixed/audit.json"),
    "qwen35-c128-statistics.md": Path("target/scheduler-fixes/artifacts/q35-c128-fixed/statistics.md"),
    "coder-c256-audit.json": Path("target/scheduler-fixes/artifacts/coder-c256-queue256/audit.json"),
    "coder-c256-report.md": Path("target/scheduler-fixes/artifacts/coder-c256-queue256/REPORT.md"),
    "coder-current-candidate-audit.json": Path("target/scheduler-fixes/artifacts/coder-current-correctness-v3/audit.json"),
    "coder-current-balanced-audit.json": Path("target/scheduler-fixes/artifacts/coder-current-screen-v1/audit.json"),
    "coder-single-historical.json": Path("target/scheduler-four-models/statistics.json"),
    "qwen36-original-r0-audit.json": Path("target/scheduler-four-models/artifacts/qwen36-r0/audit.json"),
    "flash-original-r0-audit.json": Path("target/scheduler-four-models/artifacts/flash-screen-r0/audit.json"),
    "qwen35-fixed-serial-audit.json": Path("target/scheduler-fixes/artifacts/q35-fixed-serial/audit.json"),
    "qwen35-vanilla-concurrency-audit.json": Path("target/scheduler-fixes/artifacts/q35-vanilla-concurrency/audit.json"),
    "qwen36-combined-fix-audit.json": Path("target/scheduler-fixes/artifacts/combined-logprob-retest/audit.json"),
    "qwen36-current-candidate-audit.json": Path("target/scheduler-fixes/artifacts/qwen36-current-correctness/audit.json"),
    "qwen36-composition-audit.json": Path("target/scheduler-fixes/artifacts/qwen36-composition-isolation/audit.json"),
    "flash-history-audit.json": Path("target/scheduler-fixes/artifacts/flash-history/audit.json"),
    "flash-current-candidate-audit.json": Path("target/scheduler-fixes/artifacts/flash-current-correctness/audit.json"),
}
SUITES = {
    "qwen35-c128": Path("target/scheduler-fixes/artifacts/q35-c128-fixed/suite.json"),
    "coder-c256": Path("target/scheduler-fixes/artifacts/coder-c256-queue256/suite.json"),
    "qwen36-original-r0": Path("target/scheduler-four-models/artifacts/qwen36-r0/suite.json"),
    "flash-original-r0": Path("target/scheduler-four-models/artifacts/flash-screen-r0/suite.json"),
}
AUDITS = {
    "qwen35-c128": "qwen35-c128-audit.json",
    "coder-c256": "coder-c256-audit.json",
    "qwen36-original-r0": "qwen36-original-r0-audit.json",
    "flash-original-r0": "flash-original-r0-audit.json",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def quantile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (position - low) * (values[high] - values[low])


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    ledger = json.loads(Path("target/scheduler-fixes/campaign.json").read_text())
    metadata = {"schema": "scheduler-pr-review-evidence-v1", "scope":
                "Audited historical cells and fixed retests; no four-model no-regression clearance.",
                "snapshots": {}, "suite_roots": {}, "jobs": {}}
    for name, source in SOURCES.items():
        original = source.read_bytes()
        data = original
        if name == "qwen35-c128-statistics.md":
            # The checked-in review copy removes one trailing space; retain
            # the full source digest in the manifest.
            data = original.replace(b"all four rounds. \n", b"all four rounds.\n")
            if len(original) - len(data) != 1:
                raise ValueError("unexpected Qwen3.5 statistics projection")
        elif name == "qwen36-combined-fix-audit.json":
            report = json.loads(original)
            data = (json.dumps({field: report[field] for field in
                                ("responses_verified", "fixed_responses_verified",
                                 "expected_baseline_errors", "controls", "baseline_errors", "scope")},
                               indent=2) + "\n").encode()
        elif name == "coder-single-historical.json":
            report = json.loads(original)
            group = next(g for g in report["groups"] if g["label"] == "Coder-30B single")
            data = (json.dumps({"label": group["label"], "cells": group["cells"],
                                "summary": group["summary"],
                                "comparisons": [c for c in group["comparisons"]
                                                if c["comparison"] == "skippy-patched" and
                                                c["baseline"] in ("llama-slots", "llama-unified")],
                                "exact_output_mismatches": group["exact_output_mismatches"],
                                "scope": "Historical original-source single-user screen; not current candidate."},
                               indent=2) + "\n").encode()
        if any(marker in data for marker in (b"/Users/", b"Authorization:", b"api_key", b"PRIVATE KEY")):
            raise ValueError("private or credential-like content in " + str(source))
        if name.endswith(".json"):
            json.loads(data)
        target = DEST / name
        if target.exists() and target.read_bytes() != data:
            raise ValueError("review snapshot changed; inspect source before replacing " + name)
        target.write_bytes(data)
        metadata["snapshots"][name] = {"sha256": digest(data), "bytes": len(data),
                                       "source": str(source), "source_sha256": digest(original),
                                       "projection": data != original}
    projections = {}
    timing_rows = []
    metadata["request_sources"] = {}
    for name, path in SUITES.items():
        raw = path.read_bytes()
        suite = json.loads(raw)
        if not suite["complete"]:
            raise ValueError("incomplete suite: " + name)
        metadata["suite_roots"][name] = {"sha256": digest(raw), "source": str(path),
                                         "model": suite["model"]["key"],
                                         "phase": suite["phase"], "rounds": suite["rounds"]}
        audit = json.loads((DEST / AUDITS[name]).read_text())
        audit_cells = {(cell["round"], cell["arm"]): cell for cell in audit["cells"]}
        if len(audit_cells) != len(suite["cells"]):
            raise ValueError("suite/audit cell count differs: " + name)
        projections[name] = []
        for cell in suite["cells"]:
            key = cell["round"], cell["arm"]
            audited = audit_cells[key]
            measurement_id = cell["benchmark"]["data"].get(
                "measurement", cell["benchmark"]["evidence_id"])
            if audited["evidence_id"] != measurement_id:
                raise ValueError("audit/source evidence ID differs: " + name)
            requests_path = (path.parent / "evidence" / f"r{cell['round']}-{cell['arm']}" /
                             "evidence" / measurement_id / "requests.jsonl")
            request_bytes = requests_path.read_bytes()
            requests = [json.loads(line) for line in request_bytes.splitlines()]
            if len(requests) != audited["requests"] or not all(
                row["ok"] and row["status"] == 200 and row["error"] is None
                for row in requests
            ):
                raise ValueError("request accounting differs: " + name)
            for field, source_field in (("ttft_p95_ms", "ttft_ms"),
                                        ("e2e_p95_ms", "e2e_ms")):
                if not math.isclose(quantile([r[source_field] for r in requests], .95),
                                    audited[field], rel_tol=0, abs_tol=1e-6):
                    raise ValueError("audited percentile differs from requests: " + name)
            tpot_p95 = quantile([r["tpot_ms"] for r in requests], .95)
            span = (max(r["t_end_ns"] for r in requests) -
                    min(r["t_send_ns"] for r in requests)) / 1e9
            throughput = sum(r["n_tokens"] for r in requests) / span
            if not math.isclose(throughput, audited["output_tok_s_over_client_span"],
                                rel_tol=0, abs_tol=1e-9):
                raise ValueError("audited throughput differs from requests: " + name)
            source_key = f"{name}/r{cell['round']}-{cell['arm']}"
            metadata["request_sources"][source_key] = {"sha256": digest(request_bytes),
                "evidence_id": measurement_id, "requests": len(requests)}
            for row in requests:
                timing_rows.append((name, cell["round"], cell["arm"], measurement_id,
                                    row["ttft_ms"], row["e2e_ms"], row["tpot_ms"],
                                    row["n_tokens"], row["t_send_ns"], row["t_end_ns"]))
            projections[name].append({"round": cell["round"], "arm": cell["arm"],
                "evidence_id": measurement_id,
                "derived_capacity_evidence_id": (cell["benchmark"]["evidence_id"]
                                                if measurement_id != cell["benchmark"]["evidence_id"] else None),
                "kind": cell["benchmark"]["kind"],
                "audited_metrics": {field: value for field, value in audited.items()
                                    if field in ("requests", "ttft_p50_ms", "ttft_p95_ms",
                                                 "e2e_p50_ms", "e2e_p95_ms", "tpot_p50_ms",
                                                 "tpot_p95_ms", "output_tok_s_over_client_span")},
                "linear_tpot_p95_ms": tpot_p95,
                "inferencekit_metrics": cell["benchmark"]["metrics"],
                "curve": cell["benchmark"]["data"].get("curve"),
                "request_accounting": cell["benchmark"]["data"].get("request_accounting")})
    projected = json.dumps(projections, indent=2).encode() + b"\n"
    (DEST / "cell-metrics.json").write_bytes(projected)
    metadata["cell_metrics_sha256"] = digest(projected)
    table = io.StringIO()
    writer = csv.writer(table)
    writer.writerow(("model_scope", "round", "arm", "evidence_id", "ttft_ms", "e2e_ms",
                     "tpot_ms", "output_tokens", "t_send_ns", "t_end_ns"))
    writer.writerows(timing_rows)
    timing_data = table.getvalue().encode()
    (DEST / "request-timings.csv").write_bytes(timing_data)
    metadata["request_timings_sha256"] = digest(timing_data)
    source_attestation = DEST / "source-attestation.json"
    if source_attestation.exists():
        identity = json.loads(source_attestation.read_text())
        if not identity["rust_files_exactly_match_tested_patch"]:
            raise ValueError("source attestation did not verify the Rust patch")
        metadata["source_attestation_sha256"] = digest(source_attestation.read_bytes())
    table_audit = DEST / "table-audit.json"
    if table_audit.exists():
        table_result = json.loads(table_audit.read_text())
        if not table_result["passed"] or table_result["request_timings_sha256"] != metadata["request_timings_sha256"]:
            raise ValueError("PR table audit does not match timing samples")
        metadata["table_audit_sha256"] = digest(table_audit.read_bytes())
    for phase in ("q35-c128-fixed", "coder-c256-queue256"):
        row = next(job for job in ledger["jobs"] if job["phase"] == phase)
        metadata["jobs"][phase] = {"job_id": row["job_id"], "spec_sha256": row["sha256"],
                                   "status": row["status"], "cost_usd": row["cost_usd"]}
    historical = json.loads(Path("target/scheduler-four-models/campaign.json").read_text())
    for name, model in (("qwen36-original-r0", "qwen3.6-35b-a3b-q4-k-m"),
                        ("flash-original-r0", "qwen3.8-flash-next-iq4-xs")):
        matches = [job for job in historical["jobs"] if job.get("phase") == "screen" and
                   job.get("model") == model and job.get("status") == "succeeded"]
        if len(matches) != 1:
            raise ValueError("historical screen job identity ambiguous: " + name)
        job = matches[0]
        metadata["jobs"][name] = {"job_id": job["job_id"], "spec_sha256": job["sha256"],
                                  "status": job["status"], "cost_usd": job.get("cost_usd", 0)}
    manifest = json.dumps(metadata, indent=2).encode() + b"\n"
    (DEST / "manifest.json").write_bytes(manifest)
    print("review evidence", len(SOURCES), "source snapshots,", len(SUITES), "suite projections,",
          "manifest SHA256", digest(manifest))


if __name__ == "__main__":
    main()
