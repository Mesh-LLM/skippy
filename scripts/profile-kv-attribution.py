#!/usr/bin/env python3
"""Use existing native Metal timing callbacks; export derived summaries only."""

import argparse
import collections
import json
import shutil
import subprocess
import time
from pathlib import Path

import lib.profile_kv_host as host
from lib.profile_kv_capture import stream
from lib.profile_kv_xml import overlap_ns, union_ns


def summarize(log, requests):
    commands, dropped = [], []
    for line in log.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        attrs = event.get("attributes", {})
        dropped.extend((k, v) for k, v in attrs.items() if "drop" in k and isinstance(v, (int, float)) and v > 0)
        if event.get("event") != "mtl.command_buffer" or "skippy.gpu.execute_ms" not in attrs:
            continue
        end = event["end_time_unix_nanos"]
        start = end - int(attrs["skippy.gpu.execute_ms"] * 1e6)
        commands.append({"start": start, "end": end, "attrs": attrs})
    output = []
    for request in requests:
        offset = request["start_wall_ns"] - request["start_monotonic_ns"]
        first, last = request["events_ns"][0] + offset, request["events_ns"][-1] + offset
        phases = {}
        for name, a, b in (("send_to_first_event", request["start_wall_ns"], first), ("decode_events", first, last)):
            selected = [r for r in commands if r["end"] > a and r["start"] < b]
            all_intervals = [(r["start"], r["end"]) for r in selected]
            classes = collections.defaultdict(list)
            counts, attention = set(), set()
            for row in selected:
                attrs = row["attrs"]
                classes[attrs.get("skippy.gpu.op_class", "unknown")].append((row["start"], row["end"]))
                counts.add(attrs["skippy.gpu.command_count"])
                if "skippy.attention.row_list_nodes" in attrs:
                    attention.add((attrs.get("skippy.attention.row_list_nodes"), attrs.get("skippy.attention.kv_cells")))
            phases[name] = {"wall_ns": b-a, "gpu_active_union_ns": overlap_ns(all_intervals, a, b),
                            "per_class_union_ns": {k: overlap_ns(v, a, b) for k, v in classes.items()},
                            "command_counts_per_compute": sorted(counts), "attention_row_nodes_and_kv_cells": sorted(attention)}
        output.append({"bucket": request["bucket"], "usage": request["usage"], "phases": phases})
    grouped = collections.defaultdict(list)
    for row in commands:
        a = row["attrs"]
        grouped[a["skippy.gpu.context_id"], a["skippy.gpu.compute_index"]].append(a)
    incomplete = [key for key, rows in grouped.items()
                  if {r["skippy.gpu.command_index"] for r in rows} != set(range(rows[0]["skippy.gpu.command_count"]))]
    return {"command_rows": len(commands), "all_execution_union_ns": union_ns([(r["start"], r["end"]) for r in commands]),
            "incomplete_graph_command_sets": incomplete, "dropped_counter_observations": dropped, "requests": output}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / ("followup-attribution-" + str(time.time_ns()))
    root.mkdir()
    models = json.loads((args.cache / "models.json").read_text())
    inventory = {}
    env = host.clean_env(root)
    for tool in ("gpudebug", "gpucapture", "metalperftrace"):
        direct = Path("/usr/bin") / tool
        inventory[tool] = {"direct_path_exists": direct.exists()}
        if direct.exists():
            run = subprocess.run([str(direct), "--help"], env=env, capture_output=True, text=True, timeout=15)
            inventory[tool].update(exit_code=run.returncode, help=(run.stdout+run.stderr)[:16000])
    host.save(args.output / "target-tool-inventory.json", inventory)
    original = host.launch_args
    results = {"private_root": str(root), "cases": [], "scope":
               "First16generated tokens at original512/4096input shapes. OP_PROFILE splits/serializes classes and changes fusion; diagnostic attribution, not speedup evidence. Raw native logs remain private."}
    cases = [(m, "classes", False) for m in models]
    hybrid = next(m for m in models if m["key"] == "qwen3.5-35b-a3b-q4-k-m")
    cases += [(hybrid, "unsplit", False), (hybrid, "classes", True)]
    for model, mode, ordinary in cases:
        label = model["key"] + "-" + mode + ("-ordinary" if ordinary else "-base")
        directory = root / label
        directory.mkdir()
        env = host.clean_env(directory)
        env["SKIPPY_TELEMETRY_STDERR"] = "1"
        if mode == "classes":
            env["GGML_METAL_OP_PROFILE"] = "1"
        def launch(cache, selected, engine, lanes=1):
            argv = original(cache, selected, engine, lanes)
            argv += ["--telemetry-level", "debug", "--telemetry-queue-capacity", "65536"]
            if ordinary:
                argv.append("--kv-row-attention=false")
            return argv
        host.launch_args = launch
        print("ATTRIBUTION", label, flush=True)
        time.sleep(30)
        host.require_idle(env)
        prompts = [json.loads(line) for line in
                   (args.reference_root / model["key"] / "prompts.jsonl").read_text().splitlines()]
        requests = []
        try:
            with host.server(args.cache, model, "skippy", directory, env) as child:
                host.post("/v1/completions", host.body(model, prompts[0]["messages"][0]["content"], 16))
                for prompt in (prompts[0], prompts[2]):
                    host.require_idle(env, child.pid)
                    response = stream(model, prompt["messages"][0]["content"], 16)
                    response["bucket"] = prompt["bucket"]
                    requests.append(response)
                if host.thermal_pressure():
                    raise host.HostUnavailable("non-nominal pressure after attribution")
        finally:
            host.launch_args = original
        derived = summarize(directory / "server.log", requests)
        derived.update(label=label, model=model["key"], mode=mode, ordinary=ordinary)
        for name in ("launch.json", "before.json", "after.json"):
            shutil.copy2(directory / name, args.output / (label + "-" + name))
        results["cases"].append(derived)
        host.save(args.output / "operator-attribution.json", results)
        print("DERIVED", label, derived["command_rows"], len(derived["incomplete_graph_command_sets"]), flush=True)


if __name__ == "__main__":
    main()
