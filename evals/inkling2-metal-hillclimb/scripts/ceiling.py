#!/usr/bin/env python3
"""Decode, prefill and KV ceilings for Inkling 2 Mini from a GGUF inventory and measured
hardware. Standard library only (Python >= 3.9). Follows the skill's ceilings.md:

  bytes per token = dense weights + experts x (top_k / n_experts) + lm head + KV read
  decode ceiling  = measured bandwidth / bytes per token
  prefill ceiling = 0.5 x measured peak FLOPs / FLOPs per token

Inkling 2 Mini loops layers 6-17 three times per token, so every tensor of a looped
layer is read once per pass; the inventory's per-layer bytes are multiplied by the
pass count. Expert bytes use the inventory's byte counts, which include quantization.

  python3 ceiling.py --inventory gguf_inventory_Q8_0.json --membench membench.json \
      --bench bench_c1_Q8_0.json [--bench bench_c4_Q8_0.json] --tag ceiling_Q8_0 --out FILE
"""
import argparse
import collections
import json
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def find(obj, key, found):
    if isinstance(obj, dict):
        if key in obj:
            found.append(obj)
        for v in obj.values():
            find(v, key, found)
    elif isinstance(obj, list):
        for v in obj:
            find(v, key, found)
    return found


def as_list(value, length):
    if isinstance(value, list):
        return value[:length] + [value[-1]] * (length - len(value)) if value else [None] * length
    return [value] * length


def parse_layers(text, trunk):
    out = set()
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(x for x in out if 0 <= x < trunk)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--membench")
    parser.add_argument("--bench", action="append", default=[])
    parser.add_argument("--profile", help="profile JSON; adds the runtime's measured input bytes per step")
    parser.add_argument("--tag", default="ceiling")
    parser.add_argument("--out", required=True)
    parser.add_argument("--trunk-layers", type=int, default=24)
    parser.add_argument("--loop-layers", default="6-17")
    parser.add_argument("--loop-passes", type=int, default=3)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--experts", type=int)
    parser.add_argument("--kv-bytes-per-elem", type=float, default=2.0, help="F16 KV unless the server says otherwise")
    parser.add_argument("--spec-bandwidth-gbs", type=float, default=0.0, help="vendor number, reported only")
    parser.add_argument("--remap", action="append", default=[], help="PATTERN=CATEGORY, repeatable")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    inv = load(args.inventory)
    meta = inv["metadata"]

    # Architecture keys are prefixed with the model's architecture (`inkling2_loop.*`
    # here); older inventories used `inkling.*`. Resolve under the real prefix first, or
    # the head counts read as zero and the KV bytes and attention FLOPs vanish silently.
    arch = str(meta.get("general.architecture") or "")

    def mkey(name, default=None):
        for prefix in (arch, "inkling"):
            if prefix and (prefix + "." + name) in meta:
                return meta[prefix + "." + name]
        return default

    remap = dict(item.split("=", 1) for item in args.remap)
    trunk = args.trunk_layers
    loop = parse_layers(args.loop_layers, trunk)
    top_k = args.top_k or int(mkey("expert_used_count") or 8)
    experts = args.experts or int(mkey("expert_count") or 128)
    visits = {layer: (args.loop_passes if layer in loop else 1) for layer in range(trunk)}
    total_visits = sum(visits.values())

    def category(pattern):
        return remap.get(pattern) or inv["patterns"].get(pattern, {}).get("category") or "other"

    weights = collections.defaultdict(float)   # category -> bytes read per token, weights only
    stored = collections.defaultdict(float)    # category -> bytes on disk
    draft_bytes = 0.0
    for layer_s, patterns in inv["by_layer_pattern_bytes"].items():
        layer = int(layer_s)
        for pattern, size in patterns.items():
            cat = category(pattern)
            if layer >= trunk:
                draft_bytes += size
                continue
            stored[cat] += size
            if cat == "routed_experts":
                weights[cat] += size * visits[layer] * float(top_k) / experts
            elif cat != "embedding":
                weights[cat] += size * visits[layer]
    for tensor in inv["non_block_tensors"]:
        name = tensor["name"]
        cat = "lm_head" if name in ("output", "output.weight", "output_norm") else "embedding" if name.startswith("token_embd") else "non_block_other"
        stored[cat] += tensor["bytes"]
        if cat != "embedding":
            weights[cat] += tensor["bytes"]

    # Active parameters per token (prefill FLOPs use them), from element counts by dtype.
    head_dim = int(mkey("attention.key_length") or 0)
    value_dim = int(mkey("attention.value_length") or head_dim)
    head_counts = as_list(mkey("attention.head_count"), trunk)
    heads = int(max((h for h in head_counts if h), default=0))
    kv_heads = as_list(mkey("attention.head_count_kv"), trunk)
    swa = as_list(mkey("attention.sliding_window_pattern"), trunk)
    window = int(mkey("attention.sliding_window") or 1024)
    bytes_per_elem = {"F32": 4.0, "F16": 2.0, "BF16": 2.0, "Q8_0": 34.0 / 32, "IQ4_NL": 18.0 / 32,
                      "Q4_0": 18.0 / 32, "Q4_K": 144.0 / 256, "Q6_K": 210.0 / 256, "Q5_K": 176.0 / 256,
                      "Q4_1": 20.0 / 32, "Q5_0": 22.0 / 32, "Q5_1": 24.0 / 32, "Q2_K": 84.0 / 256,
                      "Q3_K": 110.0 / 256, "IQ4_XS": 136.0 / 256, "IQ3_XXS": 98.0 / 256, "IQ2_XXS": 66.0 / 256,
                      "IQ4_NL": 18.0 / 32}
    active_params = 0.0
    for layer_s, patterns in inv["by_layer_pattern_bytes"].items():
        layer = int(layer_s)
        if layer >= trunk:
            continue
        for pattern, size in patterns.items():
            cat = category(pattern)
            # The inventory records one tensor's dtype per name pattern; elements follow
            # from the layer's bytes for that pattern and the dtype's bytes per element.
            info = inv["patterns"][pattern]
            dtype = info.get("dtype")
            bpe = bytes_per_elem.get(dtype)
            if bpe:
                per_layer_elements = size / bpe
                share = float(top_k) / experts if cat == "routed_experts" else (0.0 if cat == "embedding" else 1.0)
                active_params += per_layer_elements * share * visits[layer]
    for tensor in inv["non_block_tensors"]:
        if tensor["name"].startswith("token_embd"):
            continue
        bpe = bytes_per_elem.get(tensor["dtype"])
        if bpe:
            active_params += tensor["bytes"] / bpe

    def kv_bytes_at(position):
        total = 0.0
        for layer in range(trunk):
            local = bool(swa[layer]) if swa[layer] is not None else True
            cells = min(position, window) if local else position
            heads_l = int(kv_heads[layer] or 0)
            total += cells * heads_l * (head_dim + value_dim) * args.kv_bytes_per_elem * visits[layer]
        return total

    weights_bytes = sum(weights.values())
    bench = [load(p) for p in args.bench]
    c1 = next((b for b in bench if b.get("c1_decode")), None)

    bandwidth = None
    if args.membench:
        readings = find(load(args.membench), "p50_gbps", [])
        if readings:
            r = max(readings, key=lambda x: x.get("p50_gbps") or 0)
            bandwidth = {"device": r.get("device"), "p50_gbps": r.get("p50_gbps"), "p90_gbps": r.get("p90_gbps"),
                         "buffer_mb": r.get("buffer_mb"), "runs": r.get("runs"),
                         "tflops_fp16": r.get("compute_tflops_fp16"), "tflops_fp32": r.get("compute_tflops_fp32"),
                         "rated_gbps": r.get("rated_gbps")}
    bw = bandwidth["p50_gbps"] if bandwidth else None

    positions = {}
    if c1:
        mean = (c1.get("c1_prompt_tokens") or 1000) + (c1.get("c1_completion_tokens") or 1000) / 2.0
        positions["c1_mean"] = int(mean)
    positions.setdefault("c1_mean", 1500)
    positions["2k"] = 2048
    positions["8k"] = 8192

    def decode_ceiling(position, concurrency=1):
        kv = kv_bytes_at(position)
        if concurrency <= 1:
            total = weights_bytes + kv
        else:
            union = 1.0 - (1.0 - float(top_k) / experts) ** concurrency
            dense = weights_bytes - weights["routed_experts"]
            total = dense + weights["routed_experts"] * (union / (float(top_k) / experts)) + kv * concurrency
        return total, (bw / (total / 1e9) if bw else None) * (concurrency if concurrency > 1 else 1)

    def prefill_ceiling(length):
        if not bandwidth or not bandwidth.get("tflops_fp16"):
            return None
        flops = 2.0 * active_params
        for layer in range(trunk):
            local = bool(swa[layer]) if swa[layer] is not None else True
            span = min(window, length) if local else length
            flops += 4.0 * heads * head_dim * (span / 2.0) * visits[layer]
        flops_per_token[length] = flops
        return 0.5 * bandwidth["tflops_fp16"] * 1e12 / flops

    rows = {}
    for name, position in positions.items():
        total, ceiling = decode_ceiling(position)
        rows[name] = {"position": position, "kv_bytes": int(kv_bytes_at(position)),
                      "kv_gb": round(kv_bytes_at(position) / 1e9, 4), "bytes_per_token_gb": round(total / 1e9, 4),
                      "decode_ceiling_tps": round(ceiling, 1) if ceiling else None}
    c4_total, c4_ceiling = decode_ceiling(positions["c1_mean"], args.concurrency)
    result = {
        "tag": args.tag, "quant": inv.get("path", "").split("/")[-1], "bandwidth": bandwidth,
        "spec_bandwidth_gbs": args.spec_bandwidth_gbs or None,
        "model": {"trunk_layers": trunk, "loop_layers": loop, "loop_passes": args.loop_passes,
                  "layer_visits_per_token": total_visits, "top_k": top_k, "experts": experts,
                  "kv_heads": kv_heads, "swa_pattern": swa, "sliding_window": window,
                  "head_dim": head_dim, "value_dim": value_dim, "heads": heads,
                  "active_params_b": round(active_params / 1e9, 4)},
        "weights_read_per_token_bytes": {k: int(v) for k, v in sorted(weights.items(), key=lambda kv: -kv[1])},
        "weights_read_per_token_gb": round(weights_bytes / 1e9, 4),
        "stored_bytes_by_category": {k: int(v) for k, v in sorted(stored.items(), key=lambda kv: -kv[1])},
        "draft_or_mtp_bytes": int(draft_bytes),
        "kv_assumption": "KV cache read at %.1f bytes per element; confirm against the server's KV type"
                         % args.kv_bytes_per_elem,
    }
    result["c4_ceiling"] = {"bytes_per_token_step_gb": round(c4_total / 1e9, 4),
                            "aggregate_ceiling_tps": round(c4_ceiling, 1) if c4_ceiling else None,
                            "per_stream_ceiling_tps": round(c4_ceiling / args.concurrency, 1) if c4_ceiling else None}
    result["prefill"] = {}
    flops_per_token = {}
    for name, length in (("2k", 2048), ("8k", 8192)):
        ceiling = prefill_ceiling(length)
        result["prefill"][name] = {"length": length, "flops_per_token": flops_per_token.get(length),
                                   "ceiling_tps": round(ceiling, 1) if ceiling else None,
                                   "basis": "0.5 x measured FMA FLOPs (membench fp16 kernel); rough, not a matrix-unit peak"}
        result["prefill_%s_ceiling_tps" % name] = round(ceiling, 1) if ceiling else None
    if c1:
        measured = float(c1["c1_decode"])
        row = rows["c1_mean"]
        row.update({"measured_tps": measured, "pct_of_ceiling": round(100 * measured / row["decode_ceiling_tps"], 1) if row.get("decode_ceiling_tps") else None})
    for b in bench:
        for key in ("prefill_2048", "prefill_8192"):
            if b.get(key):
                name = "2k" if key.endswith("2048") else "8k"
                ceiling = result["prefill"][name]["ceiling_tps"]
                result["prefill"][name]["measured_tps"] = b[key]
                result["prefill"][name]["pct_of_ceiling"] = round(100 * b[key] / ceiling, 1) if ceiling else None
    result["decode"] = rows
    if args.profile:
        prof = load(args.profile)
        if prof.get("by_class"):
            result["runtime_measured_input_bytes_per_step"] = {cls: v["input_bytes_per_step"] for cls, v in prof["by_class"].items()}
    ledger = {"name": args.tag.replace("ceiling_", "baseline-"), "time": "", "node": None, "quant": (c1 or {}).get("quant") if c1 else None,
              "source_sha": None, "config": None}
    for b in bench:
        for k in ("c1_decode", "c1_decode_all", "c1_prompt_tokens", "c1_completion_tokens", "ttft_ms",
                  "prefill_2048", "prefill_2048_tokens", "prefill_8192", "prefill_8192_tokens",
                  "mem_peak_gib", "time", "node", "flags"):
            if k in b:
                ledger[k] = b[k]
        for k in b:
            if k.endswith("_aggregate"):
                ledger[k] = b[k]
    ledger["ceiling_decode_tps"] = rows["c1_mean"].get("decode_ceiling_tps")
    ledger["pct_of_decode_ceiling"] = rows["c1_mean"].get("pct_of_ceiling")
    ledger["c4_ceiling_aggregate_tps"] = result["c4_ceiling"]["aggregate_ceiling_tps"]
    ledger["ceiling_prefill_2k_tps"] = result["prefill"]["2k"].get("ceiling_tps")
    ledger["ceiling_prefill_8k_tps"] = result["prefill"]["8k"].get("ceiling_tps")
    result["ledger"] = ledger
    text = json.dumps(result, separators=(",", ":"))
    with open(args.out, "w") as f:
        f.write(text + "\n")
    print("INKLING2_RESULT %s %s" % (args.tag, text), flush=True)
    print("INKLING2_LEDGER " + json.dumps(ledger, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("ceiling failed: %r" % (error,), file=sys.stderr)
        sys.exit(1)
