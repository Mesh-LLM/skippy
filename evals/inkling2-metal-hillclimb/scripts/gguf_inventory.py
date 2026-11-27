#!/usr/bin/env python3
"""Summarise a GGUF without loading it: metadata plus every tensor's bytes, grouped by
name class and layer. Standard library only (Python >= 3.9); reads only the header.

  python3 gguf_inventory.py MODEL.gguf --tag NAME [--out inventory.json]

Prints one line, `INKLING2_RESULT NAME <json>`, so a job log carries the whole summary
(in `INKLING2_RESULT_PART` lines when it exceeds 6 KB; the job log drops longer lines).
The byte counts come from the checkpoint (quantization included), which is what a
decode step streams from memory; they are the input to the ceiling arithmetic.
"""
import argparse
import collections
import json
import os
import re
import struct
import sys

# A job log line must stay well under 64 KB: the lab agent retries an oversized append
# forever and every later line of the run waits behind it. A large model's summary is
# printed in parts that collect.py reassembles.
LINE_LIMIT = 6000


def print_result(tag, text):
    if len(text) <= LINE_LIMIT:
        print("INKLING2_RESULT %s %s" % (tag, text), flush=True)
        return
    parts = [text[i:i + LINE_LIMIT] for i in range(0, len(text), LINE_LIMIT)]
    print("INKLING2_RESULT_PARTS %s %d" % (tag, len(parts)), flush=True)
    for i, part in enumerate(parts, 1):
        print("INKLING2_RESULT_PART %s %d %d %s" % (tag, i, len(parts), part), flush=True)


# ggml_type -> (name, elements per block, bytes per block)
GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22), 7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110), 12: ("Q4_K", 256, 144), 13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292), 16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98), 19: ("IQ1_S", 256, 50), 20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136), 24: ("I8", 1, 1), 25: ("I16", 1, 2),
    26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8), 29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54), 35: ("TQ2_0", 256, 66),
    36: ("Q4_0_4_4", 32, 18), 37: ("Q4_0_4_8", 32, 18), 38: ("Q4_0_8_8", 32, 18),
    39: ("MXFP4", 32, 17),
}

SCALAR = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


class Reader:
    def __init__(self, path):
        self.f = open(path, "rb")

    def take(self, n):
        data = self.f.read(n)
        if len(data) != n:
            raise EOFError("unexpected end of GGUF header")
        return data

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self):
        return self.take(self.unpack("Q")).decode("utf-8", "replace")

    def value(self, kind):
        if kind == 8:
            return self.string()
        if kind == 9:
            inner, count = self.unpack("I"), self.unpack("Q")
            return [self.value(inner) for _ in range(count)]
        return self.unpack(SCALAR[kind])


def tensor_bytes(dims, kind):
    name, block, size = GGML_TYPES[kind]
    row = dims[0]
    rows = 1
    for d in dims[1:]:
        rows *= d
    if row % block:
        raise ValueError("row length %d is not a multiple of the %s block %d" % (row, name, block))
    return (row // block) * size * rows


def classify(rest):
    """Name class of a tensor (the `blk.N.` prefix stripped): what reads it per token."""
    if rest in ("output", "output.weight", "output_norm"):  # lm_head and final norm
        return "lm_head"
    if rest.startswith("token_embd"):
        return "embedding"
    if "exps" in rest:
        return "routed_experts"
    if "shexp" in rest:
        return "shared_expert"
    if rest.startswith("attn_") or "attn" in rest:
        return "attention"
    if "gate_inp" in rest or "router" in rest:
        return "router"
    if rest.startswith("ffn_"):
        return "dense_mlp"
    return "other"


def inventory(path):
    r = Reader(path)
    if r.take(4) != b"GGUF":
        raise ValueError("not a GGUF file")
    version = r.unpack("I")
    if version != 3:
        raise ValueError("only GGUF v3 is supported, found v%d" % version)
    n_tensors, n_kv = r.unpack("Q"), r.unpack("Q")
    metadata = {}
    for _ in range(n_kv):
        key = r.string()
        metadata[key] = r.value(r.unpack("I"))
    header_bytes = r.f.tell()
    tensors = []
    for _ in range(n_tensors):
        name = r.string()
        n_dims = r.unpack("I")
        dims = [r.unpack("Q") for _ in range(n_dims)]
        kind = r.unpack("I")
        offset = r.unpack("Q")
        if kind not in GGML_TYPES:
            raise ValueError("unknown ggml type %d for %s" % (kind, name))
        tensors.append({
            "name": name, "dtype": GGML_TYPES[kind][0], "shape": dims,
            "bytes": tensor_bytes(dims, kind), "elements": rows_elements(dims),
            "offset": offset,
        })
    return version, metadata, header_bytes, tensors


def rows_elements(dims):
    out = 1
    for d in dims:
        out *= d
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model")
    parser.add_argument("--tag", default="gguf_inventory")
    parser.add_argument("--out")
    args = parser.parse_args()

    version, metadata, header_bytes, tensors = inventory(args.model)
    file_bytes = os.path.getsize(args.model)
    alignment = metadata.get("general.alignment", 32)

    by_dtype = collections.defaultdict(lambda: {"tensors": 0, "bytes": 0, "elements": 0})
    by_category = collections.defaultdict(int)
    by_layer = collections.defaultdict(lambda: collections.defaultdict(int))
    by_layer_pattern = collections.defaultdict(lambda: collections.defaultdict(int))
    patterns = {}
    non_block = []
    max_layer = -1
    total_bytes = 0
    for t in tensors:
        d = by_dtype[t["dtype"]]
        d["tensors"] += 1
        d["bytes"] += t["bytes"]
        d["elements"] += t["elements"]
        total_bytes += t["bytes"]
        m = re.match(r"^blk\.(\d+)\.(.+)$", t["name"])
        if m:
            layer, rest = int(m.group(1)), m.group(2)
            max_layer = max(max_layer, layer)
        else:
            layer, rest = None, t["name"]
            non_block.append({k: t[k] for k in ("name", "dtype", "shape", "bytes")})
        category = classify(rest)
        by_category[category] += t["bytes"]
        if layer is not None:
            by_layer[str(layer)][category] += t["bytes"]
            by_layer_pattern[str(layer)][rest] += t["bytes"]
        p = patterns.setdefault(rest, {"tensors": 0, "bytes": 0, "layers": 0, "category": category,
                                       "dtype": t["dtype"], "shape": t["shape"]})
        p["tensors"] += 1
        p["bytes"] += t["bytes"]
        if layer is not None:
            p["layers"] += 1

    # The tokenizer arrays and chat template are large and irrelevant here.
    kept = {}
    for key, value in metadata.items():
        if key.startswith("tokenizer."):
            continue
        text = json.dumps(value)
        kept[key] = value if len(text) <= 400 else (text[:400] + "...")

    # A DSpark / MTP drafter shipped inside the file names itself: layers past the
    # trunk (inkling.block_count) or tensors named for a draft.
    block_count = metadata.get("inkling.block_count")
    draft = []
    for t in tensors:
        m = re.match(r"^blk\.(\d+)\.", t["name"])
        beyond_trunk = bool(m) and block_count is not None and int(m.group(1)) >= block_count
        if beyond_trunk or re.search(r"dspark|draft|nextn|mtp|dflash", t["name"], re.I):
            draft.append(t["name"])
    vocab_size = metadata.get("inkling.vocab_size")
    tokens = metadata.get("tokenizer.ggml.tokens")
    if vocab_size is None and isinstance(tokens, list):
        vocab_size = len(tokens)
    summary = {
        "tag": args.tag,
        "path": os.path.realpath(args.model),
        "file_bytes": file_bytes,
        "file_gib": round(file_bytes / 2**30, 3),
        "gguf_version": version,
        "header_bytes": header_bytes,
        "alignment": alignment,
        "tensor_count": len(tensors),
        "tensor_bytes": total_bytes,
        "tensor_gib": round(total_bytes / 2**30, 3),
        "tensor_elements": sum(t["elements"] for t in tensors),
        "max_layer_index": max_layer,
        "vocab_size": vocab_size,
        "metadata": kept,
        "by_dtype": dict(sorted(by_dtype.items())),
        "by_category_bytes": dict(sorted(by_category.items())),
        "by_layer_category_bytes": {k: dict(sorted(v.items())) for k, v in sorted(by_layer.items(), key=lambda kv: int(kv[0]))},
        "by_layer_pattern_bytes": {k: dict(sorted(v.items())) for k, v in sorted(by_layer_pattern.items(), key=lambda kv: int(kv[0]))},
        "patterns": dict(sorted(patterns.items())),
        "non_block_tensors": non_block,
        "draft_like_tensor_names": draft[:64],
        "draft_like_tensor_count": len(draft),
    }
    text = json.dumps(summary, separators=(",", ":"))
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
    print_result(args.tag, text)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, EOFError, KeyError, struct.error) as error:
        print("gguf_inventory failed: %r" % (error,), file=sys.stderr)
        sys.exit(1)
