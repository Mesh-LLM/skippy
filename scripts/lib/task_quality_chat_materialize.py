"""Shape held-out task prompts to 512 tokens after the pinned chat template."""

import hashlib
import json


TARGET_TOKENS = 512
SOURCE_ITEMS = 512
PADDING = "Background context unrelated to the question. " * 300


def token_digest(tokens):
    return hashlib.sha256(json.dumps(tokens, separators=(",", ":")).encode()).hexdigest()


def materialize(manifest, content_tokenize, rendered_chat_tokenize, detokenize):
    """Materialize using the exact pinned vanilla model's live tokenizer.

    ``rendered_chat_tokenize`` must apply the model's chat template to one
    user message with ``enable_thinking=false`` and a generation prompt, then
    tokenize that rendered prompt with the server's generation settings.
    Callers must later verify the 512-token count from actual responses on
    both vanilla layouts and Skippy. This pure search makes no model calls.
    """
    items = manifest.get("items", [])
    if manifest.get("schema") != "scheduler-task-quality-v3" or len(items) != SOURCE_ITEMS:
        raise ValueError("unexpected version-3 source task manifest")
    if len({item["id"] for item in items}) != SOURCE_ITEMS:
        raise ValueError("duplicate source task ID")
    padding_ids = content_tokenize(PADDING)
    if len(padding_ids) < 700:
        raise ValueError("padding is too short for 512 effective tokens")
    prefixes = {}
    prompts = []
    for item in items:
        tail = f"\nTask:\n{manifest['instructions']}\n{item['task']}\n"
        estimate = TARGET_TOKENS - len(rendered_chat_tokenize(tail))
        if estimate < 0:
            raise ValueError(f"task alone exceeds token target: {item['id']}")
        offsets = [0]
        for delta in range(1, TARGET_TOKENS + 1):
            offsets.extend((delta, -delta))
        found = None
        nearest = None
        for suffix in ("", " ", "\n", "\n "):
            for offset in offsets:
                count = estimate + offset
                if count < 0 or count > len(padding_ids):
                    continue
                if count not in prefixes:
                    prefixes[count] = detokenize(padding_ids[:count])
                prompt = prefixes[count] + suffix + tail
                tokens = rendered_chat_tokenize(prompt)
                distance = abs(len(tokens) - TARGET_TOKENS)
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, count, len(tokens), offset, repr(suffix))
                if len(tokens) == TARGET_TOKENS:
                    found = {"id": item["id"], "stratum": item["stratum"],
                             "prompt": prompt, "effective_tokens": TARGET_TOKENS,
                             "prefix_tokens": count, "search_offset": offset,
                             "prefix_suffix": suffix,
                             "effective_token_ids_sha256": token_digest(tokens),
                             "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
                    break
            if found is not None:
                break
        if found is None:
            raise ValueError(f"cannot materialize exact chat token shape: {item['id']}; nearest={nearest}")
        prompts.append(found)
    return {"schema": "scheduler-task-quality-chat-prompts-v3", "source_items": SOURCE_ITEMS,
            "target_effective_tokens": TARGET_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False}, "prompts": prompts}


def selftest(manifest):
    """Check all held-out tasks against a synthetic template and tokenizer."""
    result = materialize(manifest, list, lambda text: list("TEMPLATE!!!" + text),
                         lambda tokens: "".join(tokens))
    assert len(result["prompts"]) == SOURCE_ITEMS
    assert len({row["id"] for row in result["prompts"]}) == SOURCE_ITEMS
    for row in result["prompts"]:
        assert len("TEMPLATE!!!" + row["prompt"]) == TARGET_TOKENS
        assert row["effective_tokens"] == TARGET_TOKENS
    return result["schema"], len(result["prompts"])
