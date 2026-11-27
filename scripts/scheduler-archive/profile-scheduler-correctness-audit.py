#!/usr/bin/env python3
"""Audit saved serial controls and compare supported logprob representations."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from lib import profile_scheduler_suite as suite


def tokens(choice):
    logprobs = choice["logprobs"]
    if "content" in logprobs:
        return [{"token": row["token"], "logprob": row["logprob"],
                 "top": {x["token"]: x["logprob"] for x in row["top_logprobs"]}}
                for row in logprobs["content"]]
    return [{"token": token, "logprob": lp, "top": top} for token, lp, top in
            zip(logprobs["tokens"], logprobs["token_logprobs"], logprobs["top_logprobs"])]


def audit(root):
    record = json.loads((root / "correctness-controls.json").read_text())
    assert record["complete"] and "error" not in record and len(record["cells"]) == 8
    prompts = {p["id"]: p for p in record["prompts"]}
    cases = suite.CORRECTNESS_CASES[record["model"]["key"]]
    assert len(record["prompts"]) == len(cases) and set(prompts) == set(cases)
    assert all(prompts[name]["output_tokens"] == count for name, count in cases.items())
    rows = {}
    arms = ("skippy-base", "skippy-patched", "llama-slots", "llama-unified")
    for cell in record["cells"]:
        assert cell["complete"] and len(cell["responses"]) == 4 * len(cases)
        directory = root / "evidence" / f"r{cell['repeat']}-{cell['arm']}"
        for boundary in ("before", "after"):
            assert json.loads((directory / (boundary + ".json")).read_text())["native_thermal_pressure_level"] == 0
        for item in cell["responses"]:
            path = directory / item["file"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
            response = json.loads(path.read_text())
            usage, choice, prompt = response["usage"], response["choices"][0], prompts[item["prompt"]]
            assert usage["prompt_tokens"] == prompt["input_tokens"]
            assert usage["completion_tokens"] == prompt["output_tokens"] and choice["finish_reason"] == "length"
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if cached is None:
                cached = (response.get("timings") or {}).get("cache_n")
            assert cached == 0
            assert hashlib.sha256(choice["text"].encode()).hexdigest() == item["text_sha256"]
            if item["mode"].startswith("logprobs"):
                token_rows = tokens(choice)
                assert len(token_rows) == prompt["output_tokens"]
                assert all(math.isfinite(x["logprob"]) and all(math.isfinite(v) for v in x["top"].values()) for x in token_rows)
            key = (cell["repeat"], cell["arm"], item["prompt"], item["mode"])
            assert key not in rows
            rows[key] = choice
    assert len(rows) == 32 * len(cases)
    result = {"responses_verified": len(rows), "controls": [], "comparisons": [], "first_divergences": [],
              "scope": "Top-five logprob diagnostics vs baseline; no numerical tolerance or full quality verdict",
              "representation_limit": "Comparison uses reported token strings; a difference involving an empty/special token needs native token-ID verification"}
    for arm in arms:
        for prompt in prompts:
            a, b = rows[0, arm, prompt, "normal"], rows[0, arm, prompt, "logprobs"]
            result["controls"].append({"arm": arm, "prompt": prompt,
                "normal_repeat_exact": all(rows[r, arm, prompt, "normal"]["text"] == rows[r, arm, prompt, "normal-repeat"]["text"] for r in (0, 1)),
                "fresh_process_text_exact": a["text"] == rows[1, arm, prompt, "normal"]["text"],
                "normal_logprob_text_exact": all(rows[r, arm, prompt, "normal"]["text"] == rows[r, arm, prompt, "logprobs"]["text"] for r in (0, 1)),
                "logprob_repeat_exact": all(rows[r, arm, prompt, "logprobs"]["logprobs"] == rows[r, arm, prompt, "logprobs-repeat"]["logprobs"] for r in (0, 1)),
                "fresh_process_logprobs_exact": b["logprobs"] == rows[1, arm, prompt, "logprobs"]["logprobs"]})
    for other in arms[1:]:
        for prompt in prompts:
            result["comparisons"].append({"candidate": other, "prompt": prompt,
                "text_exact": rows[0, "skippy-base", prompt, "normal"]["text"] == rows[0, other, prompt, "normal"]["text"]})
    for arm in ("llama-slots", "llama-unified"):
        for prompt in prompts:
            ref, cand = rows[0, arm, prompt, "logprobs"], rows[0, "skippy-base", prompt, "logprobs"]
            for position, (a, b) in enumerate(zip(tokens(ref), tokens(cand))):
                if a["token"] == b["token"]:
                    continue
                alternative = a["top"].get(b["token"])
                result["first_divergences"].append({"prompt": prompt, "reference": arm,
                    "token_index": position, "reference_token": a["token"], "candidate_token": b["token"],
                    "reference_logprob_gap": a["logprob"] - alternative if alternative is not None else None,
                    "candidate_in_reference_top5": alternative is not None,
                    "normal_path_matches_logprob_path": ref["text"] == rows[0, arm, prompt, "normal"]["text"] and cand["text"] == rows[0, "skippy-base", prompt, "normal"]["text"]})
                break
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["first_divergences"], indent=2))
