#!/usr/bin/env python3
"""Offline contract check for exact-token task prompt materialization."""

import json
from pathlib import Path

from lib.profile_scheduler_task_materialize import materialize


ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / "evals/kv-profiling/scheduler-task-quality-v1.json").read_text())
content_tokenize = lambda text: list(text.encode())
effective_tokenize = lambda text: [0] + content_tokenize(text)
detokenize = lambda tokens: bytes(tokens).decode()
result = materialize(manifest, content_tokenize, effective_tokenize, detokenize)
assert len(result["prompts"]) == 256
for row, item in zip(result["prompts"], manifest["items"]):
    assert row["id"] == item["id"]
    assert row["prompt"].endswith(item["task"] + "\n")
    assert len(effective_tokenize(row["prompt"])) == 512
    assert row["prefix_suffix"] == ""

# A tokenizer can skip 512 for every unsuffixed prefix at one task boundary.
# A neutral space before the unchanged task must still yield an exact prompt.
special = manifest["items"][0]


def boundary_tokenize(text):
    if text.startswith("Background") and special["task"] in text:
        return list(range(512 if " \nTask:\n" in text else 511))
    return effective_tokenize(text)


boundary = materialize(manifest, content_tokenize, boundary_tokenize,
                       lambda tokens: bytes(tokens).decode().rstrip())
first = boundary["prompts"][0]
assert first["id"] == special["id"]
assert first["prefix_suffix"] == " "
assert first["prompt"].endswith("\nTask:\n" + manifest["instructions"] + "\n" + special["task"] + "\n")
assert len(boundary_tokenize(first["prompt"])) == 512
print("256 exact-token source prompts verified")
