"""Evaluate inference measurements against saved, workload-compatible baselines."""

from copy import deepcopy
from decimal import Decimal
import re

from .inference_nightly_metrics import metric_value
from .inference_comparability import contracts_match

DATASET = "meshllm/skippy-inference-qualification"
HISTORY_LIMIT = 64
RUN_PATH = re.compile(r"runs/([1-9][0-9]*)/([1-9][0-9]*)\.parquet")
TOLERANCE = Decimal("0.01")
DIRECTION = {
    "ttft_ms_p50": -1, "itl_ms_p50": -1,
    "prefill_tok_s": 1, "decode_tok_s": 1, "output_tok_s": 1,
    "ttft_p95_ms": -1, "itl_p95_ms": -1,
}
SHA = re.compile(r"[0-9a-f]{40}")
CONTRACT = re.compile(r"[0-9a-f]{64}")


def prior_state(files, fetch):
    if "baselines.json" not in files:
        if any(path.startswith("runs/") for path in files):
            raise ValueError("nightly history exists without a baseline manifest")
        return {"schema_version": 1, "last_run_number": 0, "history": [], "baselines": []}
    state = fetch("baselines.json")
    history = state["history"]
    if (type(state["schema_version"]) is not int or state["schema_version"] != 1
            or type(state["last_run_number"]) is not int or state["last_run_number"] < 1
            or type(history) is not list or not 0 < len(history) <= HISTORY_LIMIT
            or any(not isinstance(path, str) or not RUN_PATH.fullmatch(path) or path not in files
                   for path in history) or len(set(history)) != len(history)
            or type(state["baselines"]) is not list):
        raise ValueError("invalid nightly baseline manifest")
    return state


def metric_key(configuration, item):
    return configuration, item["workload"], item["metric"]


def baseline_index(baselines):
    result = {}
    for item in baselines:
        key = metric_key(item["configuration"], item)
        if (item["metric"] not in DIRECTION or key in result
                or not SHA.fullmatch(item["source_sha"])
                or not CONTRACT.fullmatch(item["contract_sha256"])):
            raise ValueError("invalid or duplicate saved inference baseline")
        metric_value(item["value"], positive=True)
        result[key] = item
    return result


def classify(metric, measured, saved):
    current, baseline = Decimal(str(measured)), Decimal(str(saved))
    signed = DIRECTION[metric] * (current - baseline)
    budget = baseline * TOLERANCE
    if signed < -budget:
        return "regressed"
    if signed > budget:
        return "improved"
    return "passed"


def apply_baselines(run, baselines, *, read_only=False):
    result = deepcopy(run)
    active = baseline_index(deepcopy(baselines))
    regression = False
    for config in result["configurations"]:
        if config["outcome"] != "passed" or config.get("benchmark_compatible") is False:
            continue
        for row in config["metrics"]:
            metric = row["metric"]
            if metric not in DIRECTION:
                continue
            current = metric_value(row["measured"], positive=True)
            contract = row["contract_sha256"]
            if not CONTRACT.fullmatch(contract):
                raise ValueError("invalid inference workload fingerprint")
            key = metric_key(config["id"], row)
            saved = active.get(key)
            latest = {"value": current, "source_sha": result["source_sha"], "run_id": result["id"]}
            if saved is None or not contracts_match(config["id"], row["workload"],
                                                    saved["contract_sha256"], contract):
                if read_only:
                    continue
                row["baseline_update"] = "seeded"
            else:
                row["reference"] = {"value": saved["value"], "source_sha": saved["source_sha"]}
                verdict = classify(metric, current, saved["value"])
                row["verdict"] = verdict
                if verdict == "regressed":
                    regression = True
                    continue
                if read_only:
                    continue
                if verdict == "passed" or saved["source_sha"] == result["source_sha"]:
                    saved["latest_passing"] = latest
                    continue
                row["baseline_update"] = "promoted"
            active[key] = {
                "configuration": config["id"], "workload": row["workload"], "metric": metric,
                "contract_sha256": contract, **latest, "latest_passing": latest,
            }
    if regression:
        result["outcome"] = "failed"
    return result, [active[key] for key in sorted(active)]
