#!/usr/bin/env python3
"""Audit direct API evidence without treating it as server or model qualification."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from lib import profile_scheduler_suite as suite

MODES = ("whole", "whole-logprobs", "split-iteration", "split-prefill")


def read(path):
    return json.loads(path.read_text())


def require(value, message):
    if not value:
        raise ValueError(message)


def audit(root, prior):
    report = read(root / "native-probe.json")
    require(report["complete"] and "error" not in report, "incomplete probe")
    require(report["native_source"] == "6704435209b0fa8eabc3aa91e3c0a22f92b89863", "native source drift")
    require(report["native_manifest"]["build"]["llama_patched_sha"] == report["native_source"], "manifest source drift")
    require(report["native_manifest_sha256"] == "c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481", "native bundle drift")
    headers_path = Path("target/scheduler-fixes/native-headers/manifest.json")
    require(report["header_manifest_sha256"] == hashlib.sha256(headers_path.read_bytes()).hexdigest(), "header manifest drift")
    require(read(root / "evidence/header-provenance.json") == read(headers_path), "header provenance drift")
    require(report["abi"] == {"abi": [0, 1, 87], "runtime_config_bytes": 240}, "unexpected ABI")
    pin = read(Path("ci/inference-artifacts.json"))["qwen36-35b-a3b"]
    require(report["model"] == pin, "CI model drift")
    require(report["fixture_sha256"] == "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e", "fixture drift")
    expected = [(r, m) for r in range(2) for m in (MODES if r == 0 else tuple(reversed(MODES)))]
    require([(c["repeat"], c["mode"]) for c in report["cells"]] == expected, "wrong process order/count")
    records, cells = {}, {}
    for cell in report["cells"]:
        key = cell["repeat"], cell["mode"]
        name = f"r{key[0]}-{key[1]}"
        require(cell["directory"] == name, "directory drift")
        directory = root / "evidence" / name
        file = directory / "probe.json"
        require(hashlib.sha256(file.read_bytes()).hexdigest() == cell["report_sha256"], "report hash drift")
        record = read(file)
        require(record["complete"] and record["mode"] == key[1] and len(record["steps"]) == 24, "incomplete generation")
        require(record["abi"] == report["abi"]["abi"], "ABI differs")
        require(hashlib.sha256(json.dumps(record["prompt_ids"]).encode()).hexdigest() ==
                "81f8383f806958a17b223be4ada284a1d95932822e483ca02b65b2dc5bf7a9c1", "prompt ID drift")
        chunks = [512] * 7 + [511] if key[1].startswith("split-") else [512] * 8
        require(record["chunks"] == chunks, "API prefill chunk drift")
        native = suite.resolved(root / "evidence" / (name + ".log"), "skippy-base", 16)
        require(native == cell["resolved"] and native["gpu_layers"] == 41
                and native["n_seq_max"] == ["32"] and native["n_rs_seq"] == ["0"], "native setting drift")
        before, after = [read(directory / (b + ".json")) for b in ("before", "after")]
        require(before["native_thermal_pressure_level"] == after["native_thermal_pressure_level"] == 0, "thermal gate")
        require(before["wall_ns"] <= cell["start_unix_ns"] < cell["end_unix_ns"] <= after["wall_ns"], "request window invalid")
        raw = read(directory / "raw-hashes.json")
        require(raw == cell["raw_sha256"] and len(raw) == 24, "private raw manifest drift")
        require(record["generated_ids"] == [s["token"] for s in record["steps"]], "generated IDs differ")
        for i, step in enumerate(record["steps"]):
            require(step["step"] == i and step["position"] == 4096 + i, "step position drift")
            require(step["raw_file"] == f"logits-{i}.f32" and len(raw[step["raw_file"]]) == 64, "raw path/hash invalid")
            require(len(step["top"]) == 20 and all(math.isfinite(x["logit"]) and math.isfinite(x["logprob"]) for x in step["top"]), "invalid top logits")
            require(math.isfinite(step["sampled_logprob"]), "invalid sampled logprob")
            if key[1] != "whole":
                require(math.isfinite(step["recorded_logprob"]), "invalid recorded logprob")
        records[key], cells[key] = record, cell
    comparisons = []
    for other in MODES[1:]:
        a, b = records[0, "whole"], records[0, other]
        common_steps = []
        for i, (x, y) in enumerate(zip(a["steps"], b["steps"])):
            common_steps.append({"step": i, "sampled_token_exact": x["token"] == y["token"],
                "post_call_logits_hash_exact": cells[0, "whole"]["raw_sha256"][x["raw_file"]] == cells[0, other]["raw_sha256"][y["raw_file"]],
                "reference_top": x["top"][:5], "other_top": y["top"][:5]})
            if x["token"] != y["token"]:
                break
        comparisons.append({"other": other, "text_exact": a["text"] == b["text"], "shared_prefix_steps": common_steps})
    split_equivalence = {
        "text_exact": all(records[r, "split-iteration"]["text"] == records[r, "split-prefill"]["text"] for r in range(2)),
        "post_call_logits_exact": all(cells[r, "split-iteration"]["raw_sha256"] == cells[r, "split-prefill"]["raw_sha256"] for r in range(2)),
        "recorded_logprobs_exact": all([s["recorded_logprob"] for s in records[r, "split-iteration"]["steps"]] ==
            [s["recorded_logprob"] for s in records[r, "split-prefill"]["steps"]] for r in range(2)),
    }
    archived = prior / "evidence/normal-first-skippy-base-long-2"
    calibration = {"whole_matches_server_normal": records[0, "whole"]["text"] == read(archived / "0-normal.json")["choices"][0]["text"],
                   "split_prefill_matches_server_logprobs": records[0, "split-prefill"]["text"] == read(archived / "1-logprobs.json")["choices"][0]["text"]}
    return {"processes_verified": 8, "generated_tokens_verified": 192,
            "server_calibration": calibration, "comparisons": comparisons,
            "split_api_equivalence": split_equivalence,
            "repeat_text_exact": {m: records[0, m]["text"] == records[1, m]["text"] for m in MODES},
            "repeat_post_call_logits_exact": {m: cells[0, m]["raw_sha256"] == cells[1, m]["raw_sha256"] for m in MODES},
            "scope": "Direct API diagnostic only. Hashes attest private raw rows, not locally rehashed raw downloads. No operator attribution or qualification; failed server calibration limits applicability."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--prior", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.prior)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["server_calibration"]))
