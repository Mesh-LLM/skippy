#!/usr/bin/env python3
"""Derive cache placement and maintenance observations from private Skippy traces."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

from lib.profile_kv_host import save
from lib.profile_kv_xml import overlap_ns


def trace_events(path):
    with path.open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name = row.get("event", "")
            native_cache = name.startswith("native.skippy_session_") and any(
                part in name for part in ("resident", "compact", "memory"))
            if (native_cache or name.startswith(("llama.kv_", "stage.openai_kv_")) or
                name in ("llama.set_inputs", "llama.memory_update", "stage.scheduler_feature_iteration",
                         "stage.scheduler_iteration", "mtl.command_buffer")):
                yield row


def summarize_wave(events, wave):
    start = wave["start_unix_ns"]
    begin = start + round(min(row["send_ms"] for row in wave["requests"].values()) * 1e6)
    end = start + round(max(row["end_ms"] for row in wave["requests"].values()) * 1e6)
    by_name = defaultdict(list)
    cells = []
    shapes = Counter()
    attention_extents = Counter()
    decisions = Counter()
    for row in events:
        name, a, b = row["event"], row["start_time_unix_nanos"], row["end_time_unix_nanos"]
        if a >= end or b <= begin:
            continue
        by_name[name].append((a, b))
        attrs = row.get("attributes", {})
        if "skippy.kv.used_cells" in attrs:
            cells.append(attrs["skippy.kv.used_cells"])
        if name.startswith("stage.openai_kv_") and "skippy.kv.decision" in attrs:
            decisions[(name, attrs["skippy.kv.decision"])] += 1
        if name == "llama.kv_rows":
            shapes.update((int(cell), int(runs)) for cell, runs in
                          re.findall(r"(\d+):(\d+)", attrs.get("skippy.native.detail", "")))
        if name == "mtl.command_buffer" and "skippy.attention.kv_cells" in attrs:
            attention_extents[(attrs["skippy.attention.kv_cells"],
                               attrs.get("skippy.attention.row_list_nodes"))] += 1
    return {"wall_ms": (end - begin) / 1e6,
            "occupied_cells_sample_count": len(cells),
            "occupied_cells_min_max": [min(cells), max(cells)] if cells else None,
            "actual_cells_and_contiguous_runs": shapes.most_common(),
            "padded_attention_extent_and_row_list_nodes": attention_extents.most_common(),
            "decisions": decisions.most_common(),
            "spans": {name: {"count": len(values), "union_ms": overlap_ns(values, begin, end) / 1e6}
                      for name, values in sorted(by_name.items()) if name != "mtl.command_buffer"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.capture_root.is_dir():
        raise ValueError("could not find the private capture root")
    result = {}
    for path in sorted(args.capture_root.glob("*-skippy*/waves.json")):
        events = list(trace_events(path.with_name("server.log")))
        result[path.parent.name] = {"waves": {name: summarize_wave(events, wave) for name, wave in
                                              json.loads(path.read_text()).items()}}
    if not result:
        raise ValueError("no Skippy cache traces found")
    save(args.output / "cache-mechanism.json", result)


if __name__ == "__main__":
    main()
