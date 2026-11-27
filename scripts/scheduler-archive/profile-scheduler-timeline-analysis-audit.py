#!/usr/bin/env python3
"""Audit private-host derived trace windows against verified capture metadata."""

import argparse
import json
import math
from pathlib import Path

from lib import profile_scheduler_suite as suite


def audit(artifacts, capture):
    analysis = json.loads((artifacts / "trace-analysis.json").read_text())
    record = json.loads((capture / "timeline.json").read_text())
    metadata = json.loads((capture / "audit.json").read_text())
    assert not metadata["exact_output_mismatches"]
    results = analysis["results"]
    assert len(results) == 4
    assert {Path(r["capture"]).parent.parent.name for r in results} == set(suite.ARMS)
    rows = []
    for result in results:
        assert "error" not in result
        arm = Path(result["capture"]).parent.parent.name
        assert Path(record["private_root"]) in Path(result["capture"]).parents
        cell = next(c for c in record["cells"] if c["arm"] == arm)
        pid = str(result["target_pid"])
        assert result["gpu_process_row_counts"][pid] > 0
        assert result["cpu_process_row_counts"][pid] > 0
        assert result["gpu_unattributed_rows"] == result["cpu_unattributed_rows"] == 0
        assert result["gpu_active_interval_union_ns"] > 0 and result["cpu_weight_ns"] > 0
        requests = list(cell["wave"]["requests"].items())
        assert len(result["requests"]) == len(requests) == 4
        for derived, (name, saved) in zip(result["requests"], requests):
            assert derived["complete_in_trace"] and derived["usage"] == saved["usage"]
            assert derived["event_count"] == len(saved["events_ms"])
            phases = derived["phases"]
            expected = {"prefill_to_first_event": saved["events_ms"][0] - saved["send_ms"],
                        "decode_events": saved["events_ms"][-1] - saved["events_ms"][0]}
            for phase, wall in expected.items():
                data = phases[phase]
                assert math.isclose(data["wall_ns"] / 1e6, wall, abs_tol=0.001)
                assert 0 <= data["gpu_active_union_ns"] <= data["wall_ns"]
                assert 0 <= data["gpu_idle_fraction"] <= 1
                assert math.isclose(data["gpu_idle_fraction"], 1 - data["gpu_active_union_ns"] / data["wall_ns"], abs_tol=1e-12)
        late = next(q for q in result["requests"] if q["usage"]["completion_tokens"] == 48)
        phase = late["phases"]["prefill_to_first_event"]
        row = {"arm": arm, "requests_verified": 4,
               "target_gpu_rows": result["gpu_process_row_counts"][pid],
               "target_cpu_rows": result["cpu_process_row_counts"][pid],
               "late_short_ttft_ms": phase["wall_ns"] / 1e6,
               "late_short_wait_gpu_busy_fraction": 1 - phase["gpu_idle_fraction"]}
        if arm.startswith("skippy"):
            assert not cell["scheduler"]["dropped_events"]
            assert set(cell["links"]["linked_requests"]) == set(cell["wave"]["requests"])
            wave = cell["scheduler"]["waves"]["mixed-c4"]
            gap = wave["max_anchor_gap"]
            steps = [s for s in cell["links"]["steps"] if "short-9" in s["requests"]]
            first = min(s["start_ms"] for s in steps)
            row.update(late_short_first_linked_step_ms=first,
                       late_short_send_ms=cell["wave"]["requests"]["short-9"]["send_ms"],
                       scheduler_overhead_ms=wave["scheduler_overhead_ms_total"],
                       anchor_max_gap_ms=gap["end_ms"] - gap["start_ms"],
                       anchor_gap_metal_active_ms=gap["gpu_active_ms"],
                       attention_geometry_counts=wave["attention_geometry_counts"])
        rows.append(row)
    return {"model": record["model"]["key"], "trace_coverage": "PASS", "cells": rows,
            "scope": "One profiled mixed-c4 capture per arm. Busy fractions are target-PID command/encoder interval unions across all overlapping requests, not per-request GPU work or shader timing. Request-link starts include execution and do not alone measure queue admission. No unprofiled speed or full-quality claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.capture)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(result["model"], result["trace_coverage"], len(result["cells"]), "captures")
