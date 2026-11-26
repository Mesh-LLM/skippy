"""Materialize the sealed task fixture to exactly 512 effective model tokens."""

import hashlib
import json


TARGET_TOKENS = 512
PADDING = "Background context unrelated to the question. " * 300


def token_digest(tokens):
    return hashlib.sha256(json.dumps(tokens, separators=(",", ":")).encode()).hexdigest()


def materialize(manifest, content_tokenize, effective_tokenize, detokenize):
    """Call only while the exact pinned vanilla model tokenizer is live.

    The caller must independently verify all returned prompt/token digests
    against each engine and preserve the sealed model and server identities.
    """
    if manifest.get("schema") != "scheduler-task-quality-v1" or len(manifest.get("items", [])) != 256:
        raise ValueError("unexpected source task manifest")
    padding_ids = content_tokenize(PADDING)
    if len(padding_ids) < 700:
        raise ValueError("padding is too short for 512 effective tokens")
    prefix_cache = {}
    prompts = []
    for item in manifest["items"]:
        tail = f"\nTask:\n{manifest['instructions']}\n{item['task']}\n"
        tail_count = len(effective_tokenize(tail))
        estimate = TARGET_TOKENS - tail_count
        if estimate < 0:
            raise ValueError(f"task alone exceeds token target: {item['id']}")
        offsets = [0]
        for delta in range(1, 49):
            offsets.extend((delta, -delta))
        found = None
        for offset in offsets:
            count = estimate + offset
            if count < 0 or count > len(padding_ids):
                continue
            if count not in prefix_cache:
                prefix_cache[count] = detokenize(padding_ids[:count])
            prompt = prefix_cache[count] + tail
            effective_ids = effective_tokenize(prompt)
            if len(effective_ids) == TARGET_TOKENS:
                found = {"id": item["id"], "stratum": item["stratum"],
                         "prompt": prompt, "effective_tokens": TARGET_TOKENS,
                         "effective_token_ids_sha256": token_digest(effective_ids),
                         "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
                break
        if found is None:
            raise ValueError(f"cannot materialize exact token shape: {item['id']}")
        prompts.append(found)
    return {"schema": "scheduler-task-quality-prompts-v1", "source_items": 256,
            "prompts": prompts}
