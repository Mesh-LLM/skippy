#!/usr/bin/env python3
"""Fixed-arrival scheduler and KV screens against the sealed Metal builds."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
import hashlib
import json
import re
import statistics
import subprocess
from pathlib import Path
import time
from urllib.request import Request, urlopen

import lib.profile_kv_host as host


ARMS = ("llama-slots", "llama-unified", "skippy")
ORDERS = (ARMS, (ARMS[1], ARMS[2], ARMS[0]), (ARMS[2], ARMS[0], ARMS[1]),
          ARMS[::-1], (ARMS[0], ARMS[2], ARMS[1]), (ARMS[1], ARMS[0], ARMS[2]))
MODELS = ("qwen3-4b-q4-k-m", "qwen3.5-4b-q4-k-m",
          "qwen3-coder-30b-a3b-q4-k-m", "qwen3.5-35b-a3b-q4-k-m")
LANES = 4
WAVES = {
    "serial-short": (("short-0", 0, 128),),
    "short-c2": (("short-1", 0, 128), ("short-2", 0, 128)),
    "short-c4": tuple((f"short-{index}", 0, 128) for index in range(3, 7)),
    "serial-long": (("long-0", 0, 24),),
    "mixed-c2": (("short-7", 0, 128), ("long-1", 0.65, 24)),
    "mixed-c4": (("short-8", 0, 128), ("long-2", 0.65, 24),
                 ("long-3", 0.75, 24), ("short-9", 0.85, 48)),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tokenize(model, engine, prompt):
    if engine == "llama":
        return host.post("/tokenize", {"content": prompt, "add_special": False})["tokens"]
    sha = model.get("source_model_sha256", model["files"][0]["sha256"])
    identity = {"model_id": model["key"], "source_model_sha256": sha,
                "tokenizer_id": "gguf-source-sha256:" + sha, "stage_index": 0}
    return host.post("/v1/tokenize", {"text": prompt, "add_special": False,
                                    "expected_identity": identity})["token_ids"]


def fixtures(model):
    subjects = ("Orchard", "Mountain", "Maritime", "Astronomy", "Sculpture", "Volcano",
                "Library", "Botany", "Bicycle", "Ceramics", "Origami", "Weather",
                "Harbor", "Geology", "Architecture", "Railway", "Textiles", "Cooking",
                "Basketry", "Lantern", "Carving", "Meadow")
    lengths = {name: 384 if name.startswith("short") else 4096
               for wave in WAVES.values() for name, _, _ in wave}
    lengths.update({f"cache-{index}": 4096 for index in range(8)})
    rows = {}
    for (name, length), subject in zip(lengths.items(), subjects, strict=True):
        seed = f"{subject} notebook. Continue this notebook with concrete observations.\n"
        words = f"The {subject.lower()} keeper checks the equipment and writes notes each morning.\n"
        tokens = tokenize(model, "llama", seed + words * 560)[:length]
        prompt = host.post("/detokenize", {"tokens": tokens})["content"]
        if len(tokens) != length or tokenize(model, "llama", prompt) != tokens:
            raise ValueError(f"fixture failed exact tokenizer round-trip: {name}")
        rows[name] = {"text": prompt, "tokens": length, "sha256": digest(json.dumps(tokens).encode())}
    return rows


def request(model, row, offset, count, start, cache, capture_text=False):
    delay = start + offset - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    payload = host.body(model, row["text"], count, stream=True)
    payload["cache_prompt"] = cache
    req = Request(host.URL + "/v1/completions", data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    sent = time.monotonic()
    usage, finish, native_timings = None, None, None
    chunks, arrivals = [], []
    with urlopen(req, timeout=80) as response:
        request_id = response.headers.get("x-request-id")
        for line in response:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            event = json.loads(data)
            if "error" in event:
                raise ValueError(event["error"])
            usage = event.get("usage") or usage
            native_timings = event.get("timings") or native_timings
            for choice in event["choices"]:
                if choice.get("text"):
                    chunks.append(choice["text"])
                    arrivals.append(round((time.monotonic() - start) * 1000, 4))
                finish = choice.get("finish_reason") or finish
    ended = time.monotonic()
    if not chunks or finish != "length" or not usage:
        raise ValueError(f"incomplete stream: {finish=} {usage=}, {len(chunks)} text events")
    if usage["prompt_tokens"] != row["tokens"] or usage["completion_tokens"] != count:
        raise ValueError(f"stream token count mismatch: {usage}")
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is None and native_timings:
        cached = native_timings.get("cache_n")
    if not cache and cached not in (None, 0):
        raise ValueError(f"cold request unexpectedly reused {cached} tokens")
    result = {"request_id": request_id, "offset_ms": offset * 1000, "send_ms": (sent - start) * 1000,
            "end_ms": (ended - start) * 1000, "events_ms": arrivals,
            "usage": usage, "cached_tokens": cached, "finish_reason": finish, "native_timings": native_timings,
            "text_sha256": digest("".join(chunks).encode())}
    if capture_text:
        result["text"] = "".join(chunks)
    return result


def wave(model, rows, schedule, cache_enabled, env, pid, *, max_pressure=0, capture_text=False):
    host.require_idle(env, pid, max_pressure=max_pressure)
    start = time.monotonic() + 0.03
    wall_start_ns = time.time_ns() + int((start - time.monotonic()) * 1e9)
    with ThreadPoolExecutor(max_workers=len(schedule)) as pool:
        pending = {name: pool.submit(request, model, rows[name], offset, count, start, cache_enabled, capture_text)
                   for name, offset, count in schedule}
        while not all(task.done() for task in pending.values()):
            wait(pending.values(), timeout=0.5)
            pressure = host.thermal_pressure()
            competitors = host.competing_work(env, pid)
            if pressure > max_pressure or competitors:
                raise host.HostUnavailable(f"replay host guard: thermal pressure={pressure}, competitors={competitors}")
        return {"start_unix_ns": wall_start_ns, "requests": {name: task.result() for name, task in pending.items()}}


def settings(cache, model, arm, cache_enabled):
    engine = "skippy" if arm.startswith("skippy") else "llama"
    args = host.launch_args(cache, model, engine, LANES)
    if engine == "skippy":
        if cache_enabled:
            args[args.index("--prefix-cache") + 1] = "on"
            args += ["--kv-cache-disk", "off", "--prefix-cache-max-entries", "4",
                     "--prefix-cache-min-tokens", "256"]
        if arm == "skippy-ordinary":
            args += ["--kv-row-attention=false"]
    else:
        if arm == "llama-slots":
            args += ["--no-kv-unified"]
        else:
            args += ["--kv-unified", "--kv-unified-per-slot", "8192"]
        if cache_enabled:
            args[args.index("--cache-ram") + 1] = "2048"
        else:
            args += ["--no-cache-prompt"]
    return engine, args


def read_native_settings(log_path, arm, diagnostic):
    log = log_path.read_text(errors="replace")
    result = {}
    groups = {"n_ctx": "32768", "n_batch": "512", "n_ubatch": "512",
              "kv_unified": "false" if arm == "llama-slots" else "true"}
    for key, expected in groups.items():
        found = sorted(set(re.findall(rf"\b{key}\s*=\s*['\"]?(\w+)", log)))
        result[key] = found
        if (found or diagnostic) and expected not in found:
            raise ValueError(f"unverified {arm} setting {key}, expected {expected}, got {found}")
    gpu = re.search(r"offloaded (\d+)/(\d+) layers to GPU", log)
    if (diagnostic and not gpu) or (gpu and gpu[1] != gpu[2]):
        raise ValueError("could not verify full GPU offload")
    result["gpu_layers"] = int(gpu[1]) if gpu else None
    for key in ("n_ctx_seq", "n_ctx_slot", "n_seq_max", "n_slots", "n_rs_seq", "flash_attn"):
        result[key] = sorted(set(re.findall(rf"\b{key}\s*=\s*(\w+)", log)))
    result["kv_buffer_lines"] = sorted({line.split("llama_kv_cache:", 1)[-1].strip()[:260]
                                         for line in log.splitlines()
                                         if "llama_kv_cache:" in line and ("f16" in line or "cells" in line)})
    return result


def trace_wave(model, rows, schedule, env, pid, private, *, seconds=20, capture_text=False):
    trace = private / "mixed-c4.trace"
    argv = ["xcrun", "xctrace", "record", "--template", "Metal System Trace",
            "--instrument", "Time Profiler", "--attach", str(pid), "--time-limit", f"{seconds}s",
            "--output", str(trace), "--no-prompt"]
    with (private / "capture.log").open("wb") as output:
        profiler = subprocess.Popen(argv, env=env, stdout=output, stderr=output, start_new_session=True)
        try:
            time.sleep(5)
            if profiler.poll() is not None:
                raise RuntimeError("Instruments exited before replay")
            reading = wave(model, rows, schedule, False, env, pid, capture_text=capture_text)
            deadline = time.monotonic() + seconds + 60
            while profiler.poll() is None:
                if host.thermal_pressure() or host.competing_work(env, pid):
                    raise host.HostUnavailable("capture thermal/contention guard")
                if time.monotonic() > deadline:
                    raise TimeoutError("Instruments did not finish bounded capture")
                time.sleep(0.5)
        finally:
            host.stop(profiler)
    if profiler.returncode or not trace.exists():
        raise RuntimeError("Instruments did not produce a trace")
    origin = reading["start_unix_ns"]
    absolute = lambda ms: origin + round(ms * 1e6)
    requests = [{"id": name, "start_wall_ns": absolute(row["send_ms"]),
                 "start_monotonic_ns": absolute(row["send_ms"]), "end_wall_ns": absolute(row["end_ms"]),
                 "events_ns": [absolute(t) for t in row["events_ms"]], "usage": row["usage"]}
                for name, row in reading["requests"].items()]
    host.save(private / "capture.json", {"trace": str(trace), "argv": argv, "requests": requests})
    return reading


def skippy_trace(log_path, waves):
    from lib.profile_kv_xml import overlap_ns

    steps, gpu, drops, feature = [], [], [], []
    with log_path.open() as log:
        for line in log:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name, attrs = row.get("event"), row.get("attributes", {})
            drops.extend((key, value) for key, value in attrs.items()
                         if "drop" in key and isinstance(value, (int, float)) and value > 0)
            if name == "stage.scheduler_iteration":
                steps.append(row)
            elif name in ("stage.scheduler_feature_iteration", "stage.scheduler_feature_runtime"):
                feature.append(row)
            elif name == "mtl.command_buffer" and "skippy.gpu.execute_ms" in attrs:
                end = row["end_time_unix_nanos"]
                gpu.append((end - round(attrs["skippy.gpu.execute_ms"] * 1e6), end, attrs))
    result = {}
    for name, reading in waves.items():
        start = reading["start_unix_ns"]
        end = start + round(max(r["end_ms"] for r in reading["requests"].values()) * 1e6)
        origin = start + round(min(r["send_ms"] for r in reading["requests"].values()) * 1e6)
        selected = [row["attributes"] for row in steps if row["end_time_unix_nanos"] > origin
                    and row["start_time_unix_nanos"] < end]
        phases = Counter()
        for attrs in selected:
            prefill = attrs["skippy.scheduler.prefill_tokens"] + attrs["skippy.scheduler.recompute_tokens"]
            decode = attrs["skippy.scheduler.decode_tokens"]
            phases["mixed" if prefill and decode else "prefill" if prefill else "decode"] += 1
        commands = [(a, b, attrs) for a, b, attrs in gpu if a < end and b > origin]
        shapes = Counter((attrs.get("skippy.attention.kv_cells"), attrs.get("skippy.attention.row_list_nodes"))
                         for _, _, attrs in commands if "skippy.attention.kv_cells" in attrs)
        result[name] = {"iterations": dict(phases), "metal_commands": len(commands),
                        "gpu_active_ms": overlap_ns([(a, b) for a, b, _ in commands], origin, end) / 1e6,
                        "client_window_ms": (end - origin) / 1e6, "attention_geometry_counts": shapes.most_common(),
                        "scheduler_overhead_ms_total": sum(a["skippy.scheduler.overhead_ms"] for a in selected),
                        "scheduler_step_ms_median": statistics.median([a["skippy.scheduler.step_ms"] for a in selected]) if selected else None,
                        "kv_used_cells_max": max((a.get("skippy.kv.used_cells", 0) for a in selected), default=0)}
        if name == "mixed-c4" and feature:
            arrivals = reading["requests"]["short-8"]["events_ms"]
            a, b = max(zip(arrivals, arrivals[1:]), key=lambda endpoints: endpoints[1] - endpoints[0])
            left, right = (start + round(ms * 1e6) for ms in (a, b))
            work = []
            for row in feature:
                first, last = row["start_time_unix_nanos"], row["end_time_unix_nanos"]
                if first < right and last > left:
                    work.append({"span": row["event"], "start_ms": (first - start) / 1e6,
                                 "end_ms": (last - start) / 1e6,
                                 "work": {key.split(".")[-1]: value for key, value in row["attributes"].items()
                                          if key.endswith((".batch_size", ".token_count", ".operation"))}})
            result[name]["max_anchor_gap"] = {"start_ms": a, "end_ms": b, "work": work,
                "gpu_active_ms": overlap_ns([(x, y) for x, y, _ in commands], left, right) / 1e6}
    return {"waves": result, "dropped_events": drops}


def linked_schedule(path, wave):
    def attrs(span):
        return {item["key"]: next(iter(item["value"].values())) for item in span.get("attributes", [])
                if item.get("value")}

    spans = []
    with path.open() as stream:
        for line in stream:
            data = json.loads(line)
            spans.extend(span for resource in data["resourceSpans"] for scope in resource["scopeSpans"]
                         for span in scope["spans"] if span["name"] in ("stage.scheduler_iteration",
                         "stage.openai_scheduler_generation", "stage.openai_generation_admit"))
    requests = {row["request_id"]: name for name, row in wave["requests"].items() if row["request_id"]}
    linked = {(s["traceId"], s["spanId"]): requests[attrs(s)["openai.frontend_request_id"]]
              for s in spans if s["name"] == "stage.openai_scheduler_generation"
              and attrs(s).get("openai.frontend_request_id") in requests}
    origin = wave["start_unix_ns"]
    end = max(row["end_ms"] for row in wave["requests"].values())
    result = {"linked_requests": sorted(set(linked.values())), "steps": [], "admission_ms": {}}
    for span in spans:
        offset = (int(span["startTimeUnixNano"]) - origin) / 1e6
        if not 0 <= offset <= end:
            continue
        fields = attrs(span)
        if span["name"] == "stage.openai_generation_admit":
            marker = fields.get("openai.frontend_request_id")
            if marker in requests:
                result["admission_ms"][requests[marker]] = (int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])) / 1e6
        if span["name"] == "stage.scheduler_iteration":
            result["steps"].append({"start_ms": round(offset, 3),
                                    "prefill_tokens": fields.get("skippy.scheduler.prefill_tokens"),
                                    "decode_tokens": fields.get("skippy.scheduler.decode_tokens"),
                                    "requests": [linked.get((link["traceId"], link["spanId"]))
                                                 for link in span.get("links", [])]})
    return result


def replay(args, model, arm, rows, fixture_path, directory, private):
    cache_enabled = args.mode.startswith("cache")
    diagnostic = args.mode.endswith("trace")
    engine, argv = settings(args.cache, model, arm, cache_enabled)
    env = host.clean_env(directory)
    if diagnostic and engine == "skippy":
        env["SKIPPY_TELEMETRY_STDERR"] = "1"
        argv += ["--telemetry-level", "debug", "--telemetry-queue-capacity", "65536"]
        if args.mode == "metal-trace":
            env["SKIPPY_TELEMETRY_OTLP_FILE"] = str(private / "scheduler-otlp.jsonl")
    with host.server(args.cache, model, engine, directory, env, lanes=LANES,
                     diagnostic=diagnostic, argv=argv) as child:
        if rows is None:
            rows = fixtures(model)
            host.save(fixture_path, rows)
        for name, row in rows.items():
            tokens = tokenize(model, engine, row["text"])
            if digest(json.dumps(tokens).encode()) != row["sha256"]:
                raise ValueError(f"tokenizer mismatch for {arm} {name}")
        scheduled_rows = dict(rows, **{"short-7": rows["short-0"], "short-8": rows["short-0"]})
        if cache_enabled:
            cases = {"warmup": (("short-0", 0, 8),),
                     "cold-before": (("long-0", 0, 24),),
                     "seed": tuple((f"cache-{index}", 0, 16) for index in range(4)),
                     "reuse": tuple((f"cache-{index}", 0, 16) for index in range(4)),
                     "cold-with-retained": (("long-1", 0, 24),),
                     "churn": tuple((f"cache-{index}", 0, 16) for index in range(4, 8)),
                     "reuse-after-churn": tuple((f"cache-{index}", 0, 16) for index in range(4)),
                     "cold-after": (("long-2", 0, 24),)}
            if args.mode.startswith("cache-mixed"):
                cases = {"warmup": (("short-2", 0, 8),), "mixed-c4": WAVES["mixed-c4"]}
            elif args.mode == "cache-anchor":
                cases = {"warmup": (("short-2", 0, 8),), "serial-short": WAVES["serial-short"]}
            elif getattr(args, "compact_cache_check", False):
                cases = {name: cases[name] for name in ("warmup", "seed", "reuse", "churn", "reuse-after-churn")}
        else:
            cases = {"warmup": (("cache-7", 0, 8),), **WAVES}
            if args.focus_mixed:
                cases = {"warmup": (("short-2", 0, 8),),
                         **{name: WAVES[name] for name in ("serial-short", "serial-long", "mixed-c4")}}
        result = {}
        for label, schedule in cases.items():
            print("WAVE", arm, label, flush=True)
            if result and args.wave_cooldown:
                time.sleep(args.wave_cooldown)
            if args.mode == "metal-trace" and label == "mixed-c4":
                result[label] = trace_wave(model, scheduled_rows, schedule, env, child.pid, private)
            else:
                result[label] = wave(model, scheduled_rows, schedule, cache_enabled and label not in
                                     ("cold-before", "cold-with-retained", "cold-after"), env, child.pid,
                                     max_pressure=getattr(args, "max_pressure", 0),
                                     capture_text=getattr(args, "capture_text", False))
            host.save(directory / "waves.json", result)
        resolved = read_native_settings(directory / "server.log", arm, diagnostic)
    trace_summary = skippy_trace(directory / "server.log", result) if diagnostic and engine == "skippy" else None
    if trace_summary is not None and args.mode == "metal-trace":
        trace_summary["mixed_c4_links"] = linked_schedule(private / "scheduler-otlp.jsonl", result["mixed-c4"])
    return rows, resolved, result, trace_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", choices=("cold", "cache", "cache-mixed", "cache-mixed-trace", "trace", "cache-trace", "metal-trace"),
                        required=True)
    parser.add_argument("--row-control", action="store_true")
    parser.add_argument("--arm", choices=(*ARMS, "skippy-ordinary"), action="append")
    parser.add_argument("--wave-cooldown", type=int, default=0)
    parser.add_argument("--cell-cooldown", type=int, default=15)
    parser.add_argument("--focus-mixed", action="store_true")
    parser.add_argument("--model", choices=MODELS, action="append", required=True)
    parser.add_argument("--round", type=int, choices=range(6), action="append", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    private_root = args.cache / f"scheduler-kv-{args.mode}-{time.time_ns()}"
    private_root.mkdir()
    models = json.loads((args.cache / "models.json").read_text())
    identity = json.loads((args.cache / "build-identity.json").read_text())
    paths = {"skippy": args.cache / "skippy",
             "llama-server": args.cache / "llama-build/bin/llama-server"}
    for name, expected in identity["files"].items():
        with paths.get(name, args.cache / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"sealed build identity mismatch: {name}")
    report = {"mode": args.mode, "lanes": LANES, "identity": identity, "cells": [], "fixtures": {},
              "private_root": str(private_root), "wave_cooldown_seconds": args.wave_cooldown,
              "cell_cooldown_seconds": args.cell_cooldown, "focus_mixed": args.focus_mixed,
              "mixed_anchor": {"serial-short": "short-0", "mixed-c2": "short-7", "mixed-c4": "short-8",
                               "all_use_fixture": "short-0"}}
    for model_index, key in enumerate(args.model):
        model = next(model for model in models if model["key"] == key)
        fixture_path = args.cache / f"scheduler-kv-fixtures-v1-{key}.json"
        rows = json.loads(fixture_path.read_text()) if fixture_path.exists() else None
        for round_index in args.round:
            order = ORDERS[round_index // 3 * 3 + (round_index + model_index) % 3]
            if rows is None and order[0] == "skippy":
                order = order[1:] + order[:1]
            if args.row_control:
                order += ("skippy-ordinary",)
            if args.arm:
                order = tuple(arm for arm in (*ARMS, "skippy-ordinary") if arm in args.arm)
            if rows is None and order[0].startswith("skippy"):
                raise ValueError("the exact-token fixture must already exist for a Skippy-only replay")
            for arm in order:
                tag = f"{key}-r{round_index}-{arm}"
                print("REPLAY", args.mode, tag, flush=True)
                directory = private_root / tag
                directory.mkdir()
                env = host.clean_env(directory)
                if report["cells"] or args.cell_cooldown > 15:
                    time.sleep(args.cell_cooldown)
                host.require_idle(env)
                rows, resolved, waves, trace = replay(args, model, arm, rows, fixture_path, directory, directory)
                report["cells"].append({"model": key, "round": round_index, "arm": arm,
                                        "directory": tag, "resolved": resolved, "waves": waves, "trace": trace,
                                        "launch": json.loads((directory / "launch.json").read_text()),
                                        "thermal_before": json.loads((directory / "before.json").read_text())["native_thermal_pressure_level"],
                                        "thermal_after": json.loads((directory / "after.json").read_text())["native_thermal_pressure_level"]})
                report["fixtures"][key] = {name: {k: v for k, v in row.items() if k != "text"}
                                           for name, row in rows.items()}
                host.save(args.output / "replay.json", report)


if __name__ == "__main__":
    main()
