#!/usr/bin/env python3
"""Reconcile saved Qwen3.5 c128 and serial outputs without changing verdicts."""

import argparse
import hashlib
import json
from pathlib import Path


ARMS = ("skippy-base", "skippy-patched", "llama-slots", "llama-unified")
PROMPTS = ("load-0100", "load-0190")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def output_file(root, round_number, arm, prompt):
    candidates = tuple((root / "evidence" / f"r{round_number}-{arm}" / "evidence").glob(
        f"*/outputs/{arm}/load-c128/p512-o32/{prompt}.json"
    ))
    if len(candidates) != 1:
        raise ValueError(f"expected one saved output: {round_number} {arm} {prompt}")
    return candidates[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixed", type=Path, required=True)
    parser.add_argument("--serial", type=Path, required=True)
    parser.add_argument("--serving", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    audit_path = args.fixed / "audit.json"
    audit = read(audit_path)
    if audit["source_job"] != "job_txcg43nzentnsd4" or len(audit["cells"]) != 16:
        raise ValueError("unexpected fixed c128 audit")
    expected_mismatches = {(item["round"], item["candidate"], item["prompt"])
                           for item in audit["exact_output_mismatches"]}
    if len(expected_mismatches) != 12:
        raise ValueError("unexpected mismatch count")

    matrix = {}
    sources = {str(audit_path): digest(audit_path)}
    for prompt in PROMPTS:
        matrix[prompt] = {}
        for arm in ARMS:
            loaded = []
            serial = []
            for round_number in range(4):
                path = output_file(args.fixed, round_number, arm, prompt)
                record = read(path)
                if (record["id"] != prompt or record["error"] is not None
                        or record["usage"]["prompt_tokens"] != 512
                        or record["usage"]["completion_tokens"] != 32
                        or record["usage"]["prompt_tokens_details"]["cached_tokens"] != 0):
                    raise ValueError(f"invalid loaded response: {path}")
                loaded.append(record["text"])
                sources[str(path)] = digest(path)
            if len(set(loaded)) != 1:
                raise ValueError(f"loaded output drift: {arm} {prompt}")
            for round_number in range(2):
                path = args.serial / "evidence" / f"r{round_number}-{arm}" / f"{prompt}-normal.json"
                record = read(path)
                serial.append(record["choices"][0]["text"])
                sources[str(path)] = digest(path)
            if len(set(serial)) != 1:
                raise ValueError(f"serial output drift: {arm} {prompt}")
            matrix[prompt][arm] = {
                "loaded_text_sha256": hashlib.sha256(loaded[0].encode()).hexdigest(),
                "old_serial_text_sha256": hashlib.sha256(serial[0].encode()).hexdigest(),
                "loaded_equals_old_serial": loaded[0] == serial[0],
                "loaded_prefix": loaded[0][:80],
                "old_serial_prefix": serial[0][:80],
            }
        for round_number in range(4):
            reference = matrix[prompt]["skippy-base"]["loaded_text_sha256"]
            actual = {(round_number, arm, prompt) for arm in ARMS[1:]
                      if matrix[prompt][arm]["loaded_text_sha256"] != reference}
            if actual != {item for item in expected_mismatches if item[0] == round_number and item[2] == prompt}:
                raise ValueError(f"audit mismatch matrix drift: {round_number} {prompt}")

    serving_file = args.serving / "evidence/c1-first-native-fixed/0-c1.json"
    serving = read(serving_file)["requests"]["subject-00"]
    if serving["usage"]["prompt_tokens"] != 512 or serving["usage"]["completion_tokens"] != 32:
        raise ValueError("invalid fixed serial serving response")
    sources[str(serving_file)] = digest(serving_file)
    fixed_serial_0190 = serving["text_sha256"]
    if fixed_serial_0190 != matrix["load-0190"]["skippy-base"]["loaded_text_sha256"]:
        raise ValueError("fixed serial/load-0190 no longer agrees with fixed loaded output")

    result = {
        "source_job": audit["source_job"],
        "scope": "Saved-output reconciliation only; no new generation or operator attribution.",
        "fixed_serial_load_0190_sha256": fixed_serial_0190,
        "matrix": matrix,
        "source_sha256": sources,
        "next_diagnostic": "Fresh fixed-Metal serial load-0100; old-Metal serial controls cannot establish fixed-Metal serial parity.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({prompt: {arm: row["loaded_equals_old_serial"]
                               for arm, row in arms.items()} for prompt, arms in matrix.items()}, indent=2))


if __name__ == "__main__":
    main()
