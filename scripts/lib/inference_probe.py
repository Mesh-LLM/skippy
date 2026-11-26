"""Bounded, warmed, uncached throughput samples separate from quality checks."""

import json
import math
import time

from .ci_artifacts import write_json
from .ci_smoke import curl
from .inference_checks import ENDPOINT

WORKLOAD = "DGX Spark warm probe v2"
PARAGRAPHS = (8, 32, 64)
WARMUP_TOKENS = 64
MEASURED_TOKENS = 128
RECORD = ("Requests traverse a gateway and inference workers; keep their routing, queue, "
          "and cache state consistent.")


def payload(model, index, warmup):
    # Different early tokens prevent prefix reuse; equal shapes warm the same kernels.
    prefix = "A" if warmup else "B"
    prompt = f"{prefix}{index}\n" + "\n".join(
        f"Record {item}: {RECORD}" for item in range(PARAGRAPHS[index])
    ) + "\nWrite a detailed engineering analysis of this service, with concrete examples."
    return {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "seed": 42, "ignore_eos": True,
            "max_tokens": WARMUP_TOKENS if warmup else MEASURED_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False}}


def validate_response(sample, expected):
    if sample["payload"] != expected:
        raise ValueError("throughput probe request differs from its workload")
    response = sample["response"]
    usage, timing = response["usage"], response["timings"]
    count = expected["max_tokens"]
    text = response["choices"][0]["message"]["content"]
    if (not isinstance(text, str) or not text.strip() or response.get("error") is not None
            or type(usage["completion_tokens"]) is not int or usage["completion_tokens"] != count
            or type(timing["predicted_n"]) is not int or timing["predicted_n"] != count
            or type(usage["prompt_tokens_details"]["cached_tokens"]) is not int
            or usage["prompt_tokens_details"]["cached_tokens"] != 0
            or type(usage["prompt_tokens"]) is not int or usage["prompt_tokens"] <= 0
            or type(timing["prompt_n"]) is not int or timing["prompt_n"] != usage["prompt_tokens"]):
        raise ValueError("throughput probe requires full output and uncached prompt tokens")
    for value in (sample["client_ms"], timing["prompt_ms"], timing["predicted_ms"]):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("throughput probe requires positive finite timings")
    return timing["prompt_n"]


def validate_evidence(evidence, model):
    if (type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1
            or evidence["workload"] != WORKLOAD
            or evidence["status"] != "passed" or len(evidence["samples"]) != len(PARAGRAPHS)):
        raise ValueError("incomplete warm throughput probe evidence")
    responses = []
    for index, sample in enumerate(evidence["samples"]):
        warm = validate_response(sample["warmup"], payload(model, index, True))
        measured = validate_response(sample["measured"], payload(model, index, False))
        if warm != measured:
            raise ValueError("throughput warmup and measurement have different prompt shapes")
        responses.append(sample["measured"]["response"])
    return responses


def send(request, sample):
    sample["payload"] = request
    body = json.dumps(request)
    started = time.perf_counter_ns()
    try:
        sample["raw_body"] = curl(["-H", "Content-Type: application/json", "--data-binary", body,
                                    ENDPOINT + "/v1/chat/completions"], 180)
        sample["response"] = json.loads(sample["raw_body"])
    finally:
        sample["client_ms"] = (time.perf_counter_ns() - started) / 1_000_000


def measure(model, output):
    evidence = {"schema_version": 1, "workload": WORKLOAD, "status": "incomplete", "samples": []}
    try:
        for index in range(len(PARAGRAPHS)):
            sample = {}
            evidence["samples"].append(sample)
            sample["warmup"] = {}
            send(payload(model, index, True), sample["warmup"])
            validate_response(sample["warmup"], payload(model, index, True))
            sample["measured"] = {}
            send(payload(model, index, False), sample["measured"])
            validate_response(sample["measured"], payload(model, index, False))
        evidence["status"] = "passed"
        validate_evidence(evidence, model)
    except Exception as error:
        # Retain partial/invalid responses for diagnosis, then preserve the failure.
        evidence["status"] = "failed"
        evidence["error"] = str(error)
        raise
    finally:
        write_json(output / "throughput-probe.json", evidence)
