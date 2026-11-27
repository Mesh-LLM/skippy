"""Fixed paired design and per-shape inference intervals; no device access."""

import math
import re
from statistics import fmean, stdev

from .inference_probe import PARAGRAPHS, MEASURED_TOKENS

PROTOCOL = "DGX Spark paired probe v1"
ROUNDS = 6
REPETITIONS = 3
# Two-sided Student t, df=5, alpha=.05. The independent unit is a restarted
# server pair, not the requests nested inside that pair.
T95 = 2.570581835636305
MAX_NOISE = math.log(1.05)


def schedule(baseline, candidate):
    result = []
    for experiment in ("aa", "ab"):
        for round_index in range(ROUNDS):
            for arm in (("a", "b") if round_index % 2 == 0 else ("b", "a")):
                result.append({"experiment": experiment, "round": round_index, "arm": arm,
                               "source_sha": candidate if experiment == "ab" and arm == "b" else baseline})
    return result


def interval(values):
    center = fmean(values)
    margin = T95 * stdev(values) / math.sqrt(ROUNDS)
    return center, center - margin, center + margin


def summarize(evidence, candidate, baseline, configuration):
    if (evidence.get("schema_version") != 1 or evidence.get("protocol") != PROTOCOL
            or evidence.get("source_sha") != candidate or evidence.get("baseline_sha") != baseline
            or evidence.get("configuration") != configuration
            or type(evidence.get("rounds")) is not int or evidence["rounds"] != ROUNDS
            or type(evidence.get("repetitions")) is not int or evidence["repetitions"] != REPETITIONS
            or any(not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (candidate, baseline))):
        raise ValueError("paired inference identity or design mismatch")
    placements = evidence["placements"]
    roles = {"leader", "worker"} if configuration.endswith("-split") else {"leader"}
    if (len(placements) != len(roles) or {p["role"] for p in placements} != roles
            or len({p["node"] for p in placements}) != len(roles)
            or any(not isinstance(p["node"], str) or not p["node"] for p in placements)):
        raise ValueError("paired inference requires one fixed allocation")
    expected = schedule(baseline, candidate)
    runs = evidence["runs"]
    if len(runs) != len(expected):
        raise ValueError("incomplete paired inference rounds")
    for run, design in zip(runs, expected):
        if (any(run.get(k) != v for k, v in design.items())
                or type(run.get("round")) is not int
                or run.get("placements") != placements or run.get("quality") != "passed"
                or len(run["samples"]) != len(PARAGRAPHS) * REPETITIONS):
            raise ValueError("paired inference order, placement or quality mismatch")
        for index, sample in enumerate(run["samples"]):
            if type(sample["shape"]) is not int or sample["shape"] != index % len(PARAGRAPHS):
                raise ValueError("paired inference workload shape mismatch")
            for phase in ("prefill", "decode"):
                tokens, millis = sample[f"{phase}_tokens"], sample[f"{phase}_ms"]
                if (type(tokens) is not int or tokens <= 0 or type(millis) not in (int, float)
                        or not math.isfinite(millis) or millis <= 0
                        or (phase == "decode" and tokens != MEASURED_TOKENS)):
                    raise ValueError("invalid paired inference timings")
    result = []
    for shape, paragraphs in enumerate(PARAGRAPHS):
        row = {"shape": shape, "paragraphs": paragraphs}
        for phase in ("prefill", "decode"):
            rates, counts = {}, set()
            for run in runs:
                samples = [s for s in run["samples"] if s["shape"] == shape]
                counts.update(s[f"{phase}_tokens"] for s in samples)
                tokens = sum(s[f"{phase}_tokens"] for s in samples)
                millis = sum(s[f"{phase}_ms"] for s in samples)
                rate = tokens * 1000 / millis
                if not math.isfinite(rate) or rate <= 0:
                    raise ValueError("invalid paired inference rate")
                rates[run["experiment"], run["round"], run["arm"]] = rate
            if len(counts) != 1:
                raise ValueError("paired inference token counts changed")
            logs = {kind: [math.log(rates[kind, r, "b"] / rates[kind, r, "a"])
                           for r in range(ROUNDS)] for kind in ("aa", "ab")}
            aa, aa_low, aa_high = interval(logs["aa"])
            center, low, high = interval(logs["ab"])
            noise = max(abs(aa_low), abs(aa_high))
            calibrated = aa_low <= 0 <= aa_high and noise <= MAX_NOISE
            verdict = ("improved" if calibrated and low > noise else
                       "regressed" if calibrated and high < -noise else "inconclusive")
            geometric = lambda arm: math.exp(fmean(math.log(rates["ab", r, arm]) for r in range(ROUNDS)))
            row[phase] = {
                "tokens_per_request": next(iter(counts)), "main_tok_s": geometric("a"),
                "candidate_tok_s": geometric("b"), "ratio": math.exp(center),
                "ci95": [math.exp(low), math.exp(high)],
                "aa_ratio": math.exp(aa), "aa_ci95": [math.exp(aa_low), math.exp(aa_high)],
                "noise_pct": math.expm1(noise) * 100, "calibrated": calibrated, "verdict": verdict,
            }
        result.append(row)
    return result
