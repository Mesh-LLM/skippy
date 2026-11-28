"""Require useful native phase evidence, not merely a nonempty trace file."""

import json
import math
from collections import Counter
from datetime import datetime

from .ci_artifacts import digest, read_json, write_json


def verify_phases(output, role):
    path = output / "phases.jsonl"
    counts, prefills = Counter(), []
    with path.open() as stream:
        for line in stream:
            for resource in json.loads(line).get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        counts[span["name"]] += 1
                        if span["name"] == "stage.openai_prefill":
                            prefills.append(span)
    proof = {"status": "passed", "role": role, "trace_sha256": digest(path), "span_counts": dict(counts)}
    if role == "worker":
        if not all(counts[name] for name in ("stage.binary_llama_decode", "stage.binary_message_timing")):
            raise ValueError("noise profile lacks detailed worker phase spans")
    elif role == "leader":
        samples = read_json(output / "noise-profile.json")["samples"]
        matched = 0
        for sample in samples:
            for name in ("warmup", "measured"):
                request = sample[name]
                begin, end = (datetime.fromisoformat(request[key]).timestamp()
                              for key in ("started_at", "completed_at"))
                spans = [span for span in prefills if begin <= int(span["startTimeUnixNano"]) / 1e9 <= end]
                if len(spans) != 1:
                    raise ValueError("noise profile lacks a unique prefill span for each request")
                attributes = {row["key"]: row["value"] for row in spans[0].get("attributes", [])}
                required = ("stage0_compute_ms", "runtime_lock_wait_ms", "forward_write_ms", "downstream_wait_ms")
                if not all("llama_stage." + key in attributes for key in required):
                    raise ValueError("noise profile lacks prefill phase breakdown")
                elapsed = attributes.get("llama_stage.elapsed_ms", {})
                millis = float(elapsed.get("doubleValue", elapsed.get("intValue", "nan")))
                if not math.isfinite(millis) or abs(millis - request["response"]["timings"]["prompt_ms"]) >= 1:
                    raise ValueError("noise profile span differs from response prefill timing")
                matched += 1
        if not matched:
            raise ValueError("noise profile lacks measured requests")
        proof["requests_with_prefill_breakdown"] = matched
    else:
        raise ValueError("unknown noise profile role")
    write_json(output / "phase-proof.json", proof)
    return proof
