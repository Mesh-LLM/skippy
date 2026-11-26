#!/usr/bin/env python3
"""Audit saved native settings and complete trace windows without inference."""

import argparse
import datetime
import hashlib
import json
import re
from pathlib import Path


def native_messages(path):
    messages = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            messages.append(line)
        else:
            messages.append(row.get("data", {}).get("message", ""))
    return "\n".join(messages)


def audit_settings(root):
    rows = []
    for path in sorted(root.glob("*/settings-*/server.log")):
        text = native_messages(path)
        parameters = {}
        for name in ("n_ctx", "n_batch", "n_ubatch", "n_seq_max", "flash_attn"):
            values = sorted(set(re.findall(r"llama_context:\s+" + name + r"\s+=\s+(\w+)", text)))
            parameters[name] = values
        offload = sorted(set(re.findall(r"offloaded (\d+)/(\d+) layers to GPU", text)))
        kv = sorted(set(re.findall(r"llama_kv_cache: size =\s*([^\n]+)", text)))
        recurrent = sorted(set(re.findall(r"llama_memory_recurrent: size =\s*([^\n]+)", text)))
        expected = {"n_ctx": ["8192"], "n_batch": ["512"], "n_ubatch": ["512"], "flash_attn": ["enabled"]}
        if any(parameters[key] != value for key, value in expected.items()):
            raise ValueError(f"unexpected native settings: {path}, {parameters}")
        if not offload or any(a != b for a, b in offload) or not kv:
            raise ValueError(f"missing full GPU offload/KV evidence: {path}")
        if any("K (f16)" not in line or "V (f16)" not in line for line in kv):
            raise ValueError(f"wrong attention KV precision: {path}")
        rows.append({"model": path.parts[-3], "engine": path.parts[-2][9:],
                     "resolved": parameters, "offloaded_layers": offload,
                     "attention_kv": kv, "recurrent_state": recurrent,
                     "source": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if len(rows) != 8:
        raise ValueError("all eight model/engine native settings logs are required")
    return rows


def audit_windows(artifact, root):
    rows = []
    for trace in json.loads(artifact.read_text())["results"]:
        relative = Path(*Path(trace["capture"]).parts[-4:])
        metadata_path = root / relative
        metadata = json.loads(metadata_path.read_text())
        start = datetime.datetime.fromisoformat(trace["dates"]["start-date"][0])
        start_ns = int(start.timestamp() * 1e9)
        stop_ns = start_ns + int(float(trace["dates"]["duration"][0]) * 1e9)
        complete = [r["start_wall_ns"] >= start_ns and r["end_wall_ns"] <= stop_ns
                    for r in metadata["requests"]]
        prior = [r["complete_in_trace"] for r in trace["requests"]]
        if complete != prior:
            raise ValueError(f"last-event completeness differs from whole HTTP request window: {metadata_path}")
        rows.append({"model": relative.parts[0], "engine": relative.parts[1][8:],
                     "bucket": relative.parts[2], "complete_http_requests": sum(complete),
                     "excluded_boundary_requests": len(complete) - sum(complete),
                     "all_complete_phases_have_gpu_rows": all(
                         all(p["gpu_active_union_ns"] > 0 for p in r["phases"].values())
                         for r in trace["requests"] if r["complete_in_trace"]),
                     "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest()})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen-root", type=Path, required=True)
    parser.add_argument("--recovery-root", type=Path, required=True)
    parser.add_argument("--screen-analysis", type=Path, required=True)
    parser.add_argument("--recovery-analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"native_settings": audit_settings(args.screen_root), "trace_windows":
              audit_windows(args.screen_analysis, args.screen_root) +
              audit_windows(args.recovery_analysis, args.recovery_root), "limitations": [
                  "Single logical request lane in both engines; hybrid Skippy reserves two native recurrent sequence slots while vanilla reserves one.",
                  "Hybrid recurrent state is F32 on both, with twice the allocation in Skippy; attention KV is F16 on both.",
                  "Whole HTTP request start/end fits each accepted trace window; trace clock precision and wall/monotonic mapping remain measurement limits.",
                  "Native settings are from separate untimed screen startup checks, not proof of general correctness or quality."]}
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
