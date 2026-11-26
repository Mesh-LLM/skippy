#!/usr/bin/env python3
"""Analyze private Metal/CPU traces on their host; publish only derived summaries."""

import argparse
import collections
import datetime
import json
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from lib.profile_kv_host import clean_env, save
from lib.profile_kv_xml import Table, overlap_ns, union_ns


def export(trace, directory, schema, env):
    path = directory / (schema + ".xml")
    xpath = '/trace-toc/run[@number="1"]/data/table[@schema="' + schema + '"]'
    with path.open("wb") as stream:
        subprocess.run(["xcrun", "xctrace", "export", "--input", str(trace), "--xpath", xpath],
                       stdout=stream, stderr=subprocess.PIPE, env=env, timeout=180, check=True)
    return Table(path)


def analyze(capture_path, directory, env):
    metadata = json.loads(capture_path.read_text())
    trace = Path(metadata["trace"])
    argv = metadata["argv"]
    target_pid = int(argv[argv.index("--attach") + 1])
    toc_text = subprocess.check_output(["xcrun", "xctrace", "export", "--input", str(trace), "--toc"],
                                       env=env, timeout=120, text=True)
    toc = ET.fromstring(toc_text)
    dates = {tag: [node.text for node in toc.iter(tag)] for tag in ("start-date", "end-date", "duration")}
    gpu = export(trace, directory, "metal-gpu-intervals", env)
    cpu = export(trace, directory, "time-profile", env)
    intervals = []
    depths, states = collections.Counter(), collections.Counter()
    gpu_processes, cpu_processes = collections.Counter(), collections.Counter()
    gpu_unknown, cpu_unknown = 0, 0
    for row in gpu.rows:
        fields = gpu.fields(row)
        start, duration = gpu.number(fields.get("start")), gpu.number(fields.get("duration"))
        state = gpu.text(fields.get("state"))
        depths[gpu.text(fields.get("event-depth"))] += 1
        states[state] += 1
        pid = gpu.pid(fields.get("process"))
        gpu_processes[str(pid)] += 1
        gpu_unknown += pid is None
        if pid == target_pid and start is not None and duration is not None and state.lower() == "active":
            intervals.append((start, start + duration))
    cpu_samples, leaves = [], collections.Counter()
    stack_examples = []
    for row in cpu.rows:
        fields = cpu.fields(row)
        pid = cpu.pid(fields.get("process")) or cpu.pid(fields.get("thread"))
        cpu_processes[str(pid)] += 1
        cpu_unknown += pid is None
        if pid != target_pid:
            continue
        timestamp, weight = cpu.number(fields.get("time")), cpu.number(fields.get("weight"))
        frames = cpu.frames(fields.get("stack"))
        if frames and len(stack_examples) < 3:
            stack_examples.append(frames[:8])
        leaf = frames[0] if frames else "<symbol unavailable>"
        if timestamp is not None and weight is not None:
            cpu_samples.append((timestamp, weight, leaf))
            leaves[leaf] += weight
    result = {"capture": str(capture_path), "trace": str(trace), "dates": dates, "target_pid": target_pid,
              "gpu_process_row_counts": dict(gpu_processes), "cpu_process_row_counts": dict(cpu_processes),
              "gpu_unattributed_rows": gpu_unknown, "cpu_unattributed_rows": cpu_unknown,
              "gpu_rows": len(gpu.rows), "gpu_states": dict(states), "gpu_depths": dict(depths),
              "gpu_active_interval_union_ns": union_ns(intervals),
              "gpu_active_interval_sum_ns": sum(b-a for a,b in intervals),
              "cpu_rows": len(cpu.rows), "cpu_weight_ns": sum(x[1] for x in cpu_samples),
              "cpu_leaf_weight_ns_top": leaves.most_common(20), "cpu_stack_examples": stack_examples,
              "requests": [], "limitations": [
                  "All durations observed under tracing; not end-to-end performance evidence.",
                  "GPU rows are command/encoder intervals, not individual shader kernel timings.",
                  "CPU weights sum across threads: CPU resource footprint, not wall-time shares or critical path.",
                  "Only rows attributed to the attached PID enter phase metrics; unknown process rows require an attribution audit.",
                  "Absolute trace-start/wall-clock and per-request monotonic offsets define the derived phase mapping; audit request completeness."]}
    if intervals:
        result["gpu_active_span_ns"] = max(b for _,b in intervals) - min(a for a,_ in intervals)
    if dates["start-date"]:
        start = dates["start-date"][0]
        try:
            trace_epoch_ns = int(datetime.datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp() * 1e9)
        except (ValueError, TypeError):
            trace_epoch_ns = None
        if trace_epoch_ns is not None:
            duration_ns = int(float(dates["duration"][0]) * 1e9) if dates["duration"] else None
            for request in metadata.get("requests", []):
                events = request.get("events_ns", [])
                if not events:
                    continue
                offset = request["start_wall_ns"] - request["start_monotonic_ns"]
                boundaries = (request["start_wall_ns"] - trace_epoch_ns,
                              events[0] + offset - trace_epoch_ns, events[-1] + offset - trace_epoch_ns)
                request_end_ns = request["end_wall_ns"] - trace_epoch_ns
                complete = duration_ns is not None and boundaries[0] >= 0 and request_end_ns <= duration_ns
                if not complete:
                    result["requests"].append({"usage": request.get("usage"), "complete_in_trace": False,
                                               "reason": "request is not entirely inside the bounded trace window",
                                               "start_ns": boundaries[0], "last_event_ns": boundaries[2],
                                               "request_end_ns": request_end_ns})
                    continue
                phases = {}
                for name, a, b in (("prefill_to_first_event", boundaries[0], boundaries[1]),
                                   ("decode_events", boundaries[1], boundaries[2])):
                    samples = [(t,w,leaf) for t,w,leaf in cpu_samples if a <= t <= b]
                    cpu_top = collections.Counter()
                    for _, weight, leaf in samples:
                        cpu_top[leaf] += weight
                    busy = overlap_ns(intervals, a, b)
                    phases[name] = {"start_ns": a, "end_ns": b, "wall_ns": b-a,
                                    "gpu_active_union_ns": busy,
                                    "gpu_idle_fraction": 1-busy/(b-a) if b>a else None,
                                    "cpu_weight_ns": sum(w for _,w,_ in samples),
                                    "cpu_leaf_weight_ns_top": cpu_top.most_common(10)}
                result["requests"].append({"usage": request.get("usage"), "phases": phases,
                                           "event_count": len(events), "complete_in_trace": True,
                                           "request_end_ns": request_end_ns})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private = args.capture_root.parent / ("trace-analysis-" + str(time.time_ns()))
    private.mkdir()
    env = clean_env(private)
    results = []
    for index, capture_path in enumerate(args.capture_root.rglob("capture.json")):
        directory = private / str(index)
        directory.mkdir()
        try:
            result = analyze(capture_path, directory, env)
        except Exception as error:
            result = {"capture": str(capture_path), "error": str(error)}
        results.append(result)
        save(args.output / "trace-analysis.json", {"private_exports": str(private), "results": results})
        print("ANALYZED", capture_path.parent.parent.name, result.get("gpu_rows"), result.get("cpu_rows"), result.get("error"), flush=True)


if __name__ == "__main__":
    main()
