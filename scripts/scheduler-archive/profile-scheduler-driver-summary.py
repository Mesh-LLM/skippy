#!/usr/bin/env python3
"""Summarize audited driver events without treating wrapper calls as native batches."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    root = args.artifacts
    audit = json.loads((root / "audit.json").read_text())
    record = json.loads((root / "path-isolation.json").read_text())
    assert record["complete"] and record["trace_driver"] and audit["responses_verified"] == 8
    rows = []
    for trace in audit["driver_traces"]:
        by_event = lambda name: [e["attributes"] for e in trace["events"] if e["event"] == name]
        iterations = by_event("stage.scheduler_iteration")
        chunks = [i["skippy.scheduler.prefill_tokens"] for i in iterations
                  if i["skippy.scheduler.prefill_tokens"] > 0]
        feature = by_event("stage.openai_prefill")
        assert len(feature) == (trace["driver"] == "feature")
        feature_tokens = sum(e["llama_stage.prefill_token_count"] for e in feature)
        feature_iterations = by_event("stage.scheduler_feature_iteration")
        feature_iteration_tokens = [i["skippy.scheduler.token_count"] for i in feature_iterations]
        decode = sum(i["skippy.scheduler.decode_tokens"] for i in iterations)
        # 4096 prompt tokens and 23 fed-back output tokens produce 24 outputs.
        assert sum(chunks) + feature_tokens + decode + sum(feature_iteration_tokens) == 4119
        assert len(by_event("stage.openai_decode_token")) == len(feature_iterations)
        rows.append({"first": trace["first"], "index": trace["index"], "mode": trace["mode"],
                     "driver": trace["driver"], "builtin_prefill_chunks": chunks,
                     "builtin_decode_tokens": decode, "feature_prefill_tokens": feature_tokens,
                     "feature_iteration_tokens": feature_iteration_tokens})
    drop_samples = 0
    for log in (root / "evidence").glob("*/server.log"):
        for line in log.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            attrs = event.get("attributes", {})
            if "skippy.otel_dropped_events" in attrs:
                drop_samples += 1
                assert attrs["skippy.otel_dropped_events"] == 0
                assert attrs.get("skippy.otel_export_errors") == 0
    assert drop_samples > 0 and len(rows) == 8
    result = {"batch_size": record.get("batch_size", 512), "responses": rows,
              "zero_drop_counter_samples": drop_samples,
              "all_text_matches_archived_mode": all(x["text_matches_archived"] for x in audit["archived_comparisons"]),
              "controls": audit["controls"],
              "sources": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in ("audit.json", "path-isolation.json", "path-isolation-evidence.tar.gz")},
              "limits": "Observed serving-driver steps, not native operator/ubatch tracing. Feature prefill chunk_count is a wrapper counter. Token accounting and sampled zero-drop counters do not prove every event was exported. No causal fix or timing claim."}
    (root / "driver-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
