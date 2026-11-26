#!/usr/bin/env python3
"""Audit final saved timing identities, controls, swap and output snapshots."""

import argparse
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ledger = json.loads(args.campaign.read_text())
    identity = json.loads((args.root.parent / "campaign.json").read_text())["identity"]
    if identity["source_sha"] != ledger["source_sha"] or identity["upstream_sha"] != ledger["upstream_sha"]:
        raise ValueError("final runtime/source identity differs from the approved campaign")
    common = {"llama": {"-c": "8192", "-np": "1", "-ngl": "999", "-fa": "on",
                        "-ctk": "f16", "-ctv": "f16", "-b": "512", "-ub": "512",
                        "-t": "16", "-tb": "16", "--poll": "0", "--poll-batch": "0", "--cache-ram": "0"},
              "skippy": {"--ctx-size": "8192", "--generation-concurrency": "1", "--n-gpu-layers": "999",
                         "--flash-attention": "enabled", "--kv-cache-type-k": "f16", "--kv-cache-type-v": "f16",
                         "--batch-size": "512", "--microbatch-size": "512", "--threads": "16", "--threads-batch": "16",
                         "--speculative-strategy": "disabled", "--prefix-cache": "off", "--cache-idle-slots": "0"}}
    allowed = {"PATH", "HOME", "TMPDIR", "LANG", "HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "INFERENCEKIT_RUNS_DIR"}
    cells, swaps, outputs = [], [], []
    for model in sorted(args.root.glob("qwen*")):
        for cell in sorted(model.glob("b*-p*-*")):
            engine = cell.name.split("-", 2)[2]
            launch = json.loads((cell / "launch.json").read_text())
            argv = launch["argv"]
            if set(launch["environment_names"]) != allowed:
                raise ValueError(f"unexpected environment names: {cell}")
            if any(flag not in argv or argv[argv.index(flag) + 1] != value for flag, value in common[engine].items()):
                raise ValueError(f"serving controls differ: {cell}")
            if engine == "llama" and "--no-context-shift" not in argv:
                raise ValueError(f"context shifting not disabled: {cell}")
            record = json.loads((cell / "bench.json").read_text())
            version = ledger["source_sha" if engine == "skippy" else "upstream_sha"]
            if record["identity"]["engine"]["version"] != version or record["provenance"]["tool_version"] != "0.1.1":
                raise ValueError(f"client/engine version differs: {cell}")
            request = record["data"]["request"]["args"]
            extra = json.loads(request["extra_body"])
            if request["model"] != model.name or request["warmup"] != 2 or not request["exact_length"]:
                raise ValueError(f"request setup differs: {cell}")
            expected = {"seed": 42, "top_p": 1, "top_k": 0, "min_p": 0, "repeat_penalty": 1, "cache_prompt": False}
            if extra != expected or not record["coverage"]["complete"]:
                raise ValueError(f"request controls/coverage differ: {cell}")
            for boundary in ("before", "after"):
                data = json.loads((cell / (boundary + ".json")).read_text())
                swaps.append(float(re.search(r"used = ([0-9.]+)M", data["sysctl vm.swapusage"])[1]))
            cells.append({"model": model.name, "cell": cell.name, "evidence_id": record["evidence_id"],
                          "launch_sha256": hashlib.sha256((cell / "launch.json").read_bytes()).hexdigest()})
        for path in sorted((model / "outputs/llama").glob("*/*.json")):
            other = model / "outputs/skippy" / path.relative_to(model / "outputs/llama")
            a, b = json.loads(path.read_text()), json.loads(other.read_text())
            for row in (a, b):
                if row["usage"]["prompt_tokens_details"]["cached_tokens"] != 0:
                    raise ValueError(f"cached untagged prompt observed: {path}")
            index = next((i for i, (x, y) in enumerate(zip(a["text"], b["text"])) if x != y),
                         min(len(a["text"]), len(b["text"])))
            outputs.append({"model": model.name, "prompt": a["id"], "text_equal": a["text"] == b["text"],
                            "first_differing_character": None if a["text"] == b["text"] else index,
                            "llama_text_sha256": hashlib.sha256(a["text"].encode()).hexdigest(),
                            "skippy_text_sha256": hashlib.sha256(b["text"].encode()).hexdigest()})
    if len(cells) != 48 or len(outputs) != 16:
        raise ValueError("final four-model coverage incomplete")
    args.output.write_text(json.dumps({"cells": cells, "outputs": outputs, "swap_used_mib_values": sorted(set(swaps)),
        "source_identity": identity, "scope": "Saved startup controls, client identity and untagged output cache/text audit. Actual native settings are separately audited. Tagged cache usage and full logits/task quality are not retained/proven."}, indent=2) + "\n")
    print(f"Audited {len(cells)} cell identities/settings, {len(outputs)} output pairs; swap used MiB {sorted(set(swaps))}.")


if __name__ == "__main__":
    main()
