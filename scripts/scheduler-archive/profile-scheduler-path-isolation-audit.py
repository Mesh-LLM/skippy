#!/usr/bin/env python3
"""Verify observation/history isolation and retain every disagreement."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import runpy

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def driver_trace(directory, item):
    selected, drops = [], []
    for line in (directory / "server.log").read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        attrs = row.get("attributes", {})
        drops.extend((k, v) for k, v in attrs.items() if ("drop" in k or k == "skippy.otel_export_errors")
                     and isinstance(v, (int, float)) and v > 0)
        start, end = row.get("start_time_unix_nanos"), row.get("end_time_unix_nanos")
        if start is None or end is None or not (
                end >= item["start_unix_ns"] and start <= item["end_unix_ns"]):
            continue
        if row.get("event", "").startswith("stage."):
            selected.append({"event": row["event"], "start_unix_ns": start, "end_unix_ns": end,
                "attributes": {k: v for k, v in attrs.items() if k.startswith((
                    "skippy.scheduler.", "llama_stage.prefill_", "skippy.kv.prefill_"))}})
    require(not drops, "trace dropped events")
    routes = [r["attributes"].get("skippy.scheduler.generation_driver") for r in selected
              if r["event"] == "stage.scheduler_route"]
    require(len(routes) == 1 and routes[0] in ("built-in", "feature"), "missing/ambiguous driver route")
    return {"driver": routes[0], "events": selected}


def audit(root, prior, fixture_path):
    record, original = read(root / "path-isolation.json"), read(prior / "correctness-controls.json")
    require(record["complete"] and "error" not in record, "incomplete isolation")
    require(record["identity"] == original["identity"] and record["candidate"] == original["candidate"],
            "binary identities changed")
    # Earlier Qwen3.6 controls predate the derived aggregate identity field.
    # Validate its derivation where present, then compare all model fields.
    models = []
    for source in (record["model"], original["model"]):
        model = dict(source)
        derived = suite.source_identity(model["files"])
        require(model.get("source_model_sha256", derived) == derived, "model aggregate identity mismatch")
        model["source_model_sha256"] = derived
        models.append(model)
    require(models[0] == models[1], "model identity changed")
    require(record["identity"]["source_sha"] == suite.SOURCE, "source drift")
    require(record["candidate"]["patch_sha256"] == suite.PATCH, "candidate drift")
    require(hashlib.sha256(fixture_path.read_bytes()).hexdigest() == record["fixture_sha256"]
            == original["fixture_sha256"], "fixture drift")
    fixture = read(fixture_path)
    token_rows = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-correctness-audit.py")))["tokens"]
    trace = record.get("trace_driver", False)
    batch_size = record.get("batch_size", 512)
    microbatch_size = record.get("microbatch_size", 512)
    require(batch_size in (512, 4096) and (batch_size == 512 or trace), "unregistered batch control")
    require(microbatch_size == 512 or (microbatch_size in (455, 511) and batch_size == 512 and trace),
            "unregistered microbatch control")
    arms = ("skippy-base",) if trace else ("skippy-base", "llama-slots")
    prompts = ("long-2",) if trace else ("short-0", "long-2")
    keys = {(first, arm, prompt) for first in ("normal", "logprobs")
            for arm in arms for prompt in prompts}
    require(len(record["cells"]) == len(keys), "wrong cell count")
    seen, responses, prior_matches, traces = set(), {}, [], []
    for cell in record["cells"]:
        first, arm, prompt = cell["first"], cell["arm"], cell["prompt"]
        key = first, arm, prompt
        require(key in keys and key not in seen, "unknown/duplicate process")
        seen.add(key)
        directory = root / "evidence" / f"{first}-first-{arm}-{prompt}"
        previous = prior / "evidence" / f"r0-{arm}"
        launch = read(previous / "launch.json")
        if batch_size != 512:
            launch["argv"][launch["argv"].index("--batch-size") + 1] = str(batch_size)
        if microbatch_size != 512:
            launch["argv"][launch["argv"].index("--microbatch-size") + 1] = str(microbatch_size)
        if trace:
            launch["argv"] += ["--telemetry-level", "debug", "--telemetry-queue-capacity", "65536"]
            launch["environment_names"] = sorted(launch["environment_names"] + ["SKIPPY_TELEMETRY_STDERR"])
        require(read(directory / "launch.json") == launch, "launch settings changed")
        resolved = suite.resolved(directory / "server.log", arm, 16, batch_size, microbatch_size)
        require(resolved == cell["resolved"], "native evidence mismatch")
        original_resolved = suite.resolved(previous / "server.log", arm, 16)
        original_resolved["n_batch"] = [str(batch_size)]
        original_resolved["n_ubatch"] = [str(microbatch_size)]
        require({k: v for k, v in resolved.items() if k != "kv_buffers"}
                == {k: v for k, v in original_resolved.items() if k != "kv_buffers"}, "native settings changed")
        pattern = r"size =\s*([\d.]+) MiB.*?K \(f16\):\s*([\d.]+) MiB, V \(f16\):\s*([\d.]+) MiB"
        buffers = [set(re.findall(pattern, "\n".join(v["kv_buffers"]))) for v in (resolved, original_resolved)]
        require(buffers[0] and buffers[0] == buffers[1], "KV allocation changed")
        for boundary in ("before", "after"):
            require(read(directory / f"{boundary}.json")["native_thermal_pressure_level"] == 0,
                    "non-nominal thermal boundary")
        modes = (first, "logprobs" if first == "normal" else "normal") * 2
        require(cell["complete"] and len(cell["responses"]) == 4, "incomplete process responses")
        count = suite.CORRECTNESS_CASES[record["model"]["key"]][prompt]
        row = fixture["mixed"][prompt]
        for index, (item, mode) in enumerate(zip(cell["responses"], modes)):
            filename = f"{index}-{mode}.json"
            path = directory / filename
            require(item["mode"] == mode and item["file"] == str(Path(directory.name) / filename),
                    "response order/path differs")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], "response hash mismatch")
            request = host.body(record["model"], row["text"], count)
            if mode == "logprobs":
                request["logprobs"] = 5
            require(read(directory / f"{index}-{mode}-request.json") == request, "request body changed")
            response = read(path)
            choice, usage = response["choices"][0], response["usage"]
            require(usage["prompt_tokens"] == row["tokens"] and usage["completion_tokens"] == count
                    and choice["finish_reason"] == "length", "token/completion gate failed")
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if cached is None:
                cached = (response.get("timings") or {}).get("cache_n")
            require(cached == 0, "cache hit or missing counter")
            require(hashlib.sha256(choice["text"].encode()).hexdigest() == item["text_sha256"], "text hash mismatch")
            if mode == "logprobs":
                rows = token_rows(choice)
                require(len(rows) == count and all(math.isfinite(r["logprob"])
                        and all(math.isfinite(v) for v in r["top"].values()) for r in rows), "invalid logprob rows")
            responses[first, arm, prompt, index] = choice
            old = read(previous / f"{prompt}-{mode}.json")["choices"][0]
            prior_matches.append({"first": first, "arm": arm, "prompt": prompt, "index": index,
                                  "mode": mode, "text_matches_archived": old["text"] == choice["text"]})
            if trace:
                traces.append({"first": first, "mode": mode, "index": index,
                               **driver_trace(directory, item)})
    require(seen == keys and len(responses) == 4 * len(keys), "missing controls")
    controls = []
    for arm in arms:
        for prompt in prompts:
            normal = [responses["normal", arm, prompt, i] for i in range(4)]
            logs = [responses["logprobs", arm, prompt, i] for i in range(4)]
            controls.append({"arm": arm, "prompt": prompt,
                "fresh_first_normal_vs_logprobs_text_exact": normal[0]["text"] == logs[0]["text"],
                "normal_mode_text_all_exact": len({r["text"] for r in (normal[0], normal[2], logs[1], logs[3])}) == 1,
                "logprobs_mode_text_all_exact": len({r["text"] for r in (normal[1], normal[3], logs[0], logs[2])}) == 1,
                "logprobs_numeric_all_exact": all(r["logprobs"] == logs[0]["logprobs"] for r in (normal[1], normal[3], logs[2]))})
    boundary_controls = []
    if microbatch_size in (455, 511):
        expected_prefill = [455] * 9 + [1] if microbatch_size == 455 else [511] * 8 + [8]
        for row in traces:
            events = row["events"]
            prefill = [e["attributes"].get("skippy.scheduler.prefill_tokens", 0)
                       for e in events if e["event"] == "stage.scheduler_iteration"]
            prefill = [n for n in prefill if n]
            feature_prefix = [e["attributes"].get("llama_stage.prefill_token_count")
                              for e in events if e["event"] == "stage.openai_prefill"]
            feature_steps = [e["attributes"].get("skippy.scheduler.token_count")
                             for e in events if e["event"] == "stage.scheduler_feature_iteration"]
            if row["mode"] == "normal":
                require(row["driver"] == "built-in" and prefill == expected_prefill,
                        "ordinary prompt boundary control not executed")
            else:
                require(row["driver"] == "feature" and feature_prefix == [4095]
                        and feature_steps == [1] * 24, "feature prompt boundary control not executed")
            boundary_controls.append({"first": row["first"], "mode": row["mode"], "index": row["index"],
                "ordinary_prefill_chunks": prefill, "feature_prefix_tokens": feature_prefix,
                "feature_iteration_tokens": feature_steps})
    return {"responses_verified": len(responses), "controls": controls, "archived_comparisons": prior_matches,
            "prompt_boundary_controls": boundary_controls,
            "driver_traces": traces,
            "scope": "Mode and request-history isolation; preserves failures. No operator attribution or tolerance clearance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.prior, args.fixtures)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["controls"], indent=2))
