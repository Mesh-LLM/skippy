#!/usr/bin/env python3
"""Turn Skippy's stderr telemetry into a decode-step GPU profile. Standard library
only (Python >= 3.9).

The server runs with `--telemetry-level debug`, `SKIPPY_TELEMETRY_STDERR=1` and
`GGML_METAL_OP_PROFILE=1`. Metal then splits every graph into one command buffer per
run of same-class nodes and times each on the device, and Skippy writes one JSON line
per command buffer (`mtl.command_buffer`) with its op class, model layer, node count and
input bytes. This script groups the decode steps inside a time window by op class, by
layer visit and by size signature, so each kind of kernel work can be ranked by its share
of a decode step. Splitting serialises the graph, so absolute times are longer than an
unprofiled run; the shares are what matter.

  python3 profile_from_spans.py server.stderr.log --window START_NS,END_NS --tag NAME --out FILE
"""
import argparse
import collections
import json
import statistics
import sys

CLASSES = ("attention", "matmul", "recurrent", "kv_write", "other")


def events(path):
    with open(path, "r", errors="replace") as f:
        for line in f:
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and "event" in event and isinstance(event.get("attributes"), dict):
                yield event


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def rounded(value, digits=4):
    return round(value, digits) if isinstance(value, (int, float)) else value


def command_row(event):
    a = event["attributes"]
    cls = a.get("skippy.gpu.op_class", "unclassified")
    input_bytes = sum(number(v) for k, v in a.items() if k.startswith("skippy.gpu.input_bytes."))
    nodes_by_class = {k[len("skippy.gpu.nodes."):]: int(number(v)) for k, v in a.items() if k.startswith("skippy.gpu.nodes.")}
    return {
        "compute": int(number(a.get("skippy.gpu.compute_index"))),
        "index": int(number(a.get("skippy.gpu.command_index"))),
        "count": int(number(a.get("skippy.gpu.command_count"))),
        "class": cls,
        "layer": a.get("skippy.gpu.layer"),
        "nodes": int(number(a.get("skippy.gpu.node_count"))),
        "nodes_by_class": nodes_by_class,
        "input_bytes": int(input_bytes),
        "encode_ms": number(a.get("skippy.gpu.encode_ms")),
        "queue_ms": number(a.get("skippy.gpu.queue_ms")),
        "execute_ms": number(a.get("skippy.gpu.execute_ms")),
        "start": int(event.get("start_time_unix_nanos") or 0),
        "end": int(event.get("end_time_unix_nanos") or 0),
        "attention": {k[len("skippy.attention."):]: v for k, v in a.items() if k.startswith("skippy.attention.")},
    }


def signature(row):
    return (row["class"], row["nodes"], round(row["input_bytes"] / 1e6, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log")
    parser.add_argument("--window", help="START_NS,END_NS: only steps fully inside are profiled")
    parser.add_argument("--tag", default="profile")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    window = [int(x) for x in args.window.split(",")] if args.window else None

    commands, others, names = [], [], collections.Counter()
    for event in events(args.log):
        names[event["event"]] += 1
        start, end = int(event.get("start_time_unix_nanos") or 0), int(event.get("end_time_unix_nanos") or 0)
        if window and not (start >= window[0] and end <= window[1]):
            continue
        if event["event"] == "mtl.command_buffer":
            commands.append(command_row(event))
        else:
            a = event["attributes"]
            others.append({"event": event["event"], "ms": (end - start) / 1e6,
                           "detail": (a.get("skippy.native.detail") or "")[:4000], "start": start, "end": end})

    computes = collections.OrderedDict()
    for row in sorted(commands, key=lambda r: (r["compute"], r["index"])):
        computes.setdefault(row["compute"], []).append(row)
    sizes = collections.Counter(len(rows) for rows in computes.values())
    result = {"tag": args.tag, "window_ns": window, "event_name_counts": dict(names),
              "command_records": len(commands), "computes_in_window": len(computes),
              "compute_sizes": dict(sizes)}
    if not computes:
        result["error"] = "no mtl.command_buffer spans inside the window"
        return emit(result, args)

    # Decode steps are the most common graph shape in the window; the prefill graph differs.
    decode_size = sizes.most_common(1)[0][0]
    steps = [rows for rows in computes.values() if len(rows) == decode_size]
    prefill = [rows for rows in computes.values() if len(rows) != decode_size]
    result["decode_step_count"] = len(steps)
    result["prefill_like_computes"] = len(prefill)
    result["commands_missing"] = sum(max(r["count"] for r in rows) - len(rows) for rows in computes.values())

    def per_step(values):
        return round(sum(values) / len(values), 4) if values else 0.0

    walls = [(rows[-1]["end"] - rows[0]["start"]) / 1e6 for rows in steps]
    busy = [sum(r["execute_ms"] for r in rows) for rows in steps]
    encode = [sum(r["encode_ms"] for r in rows) for rows in steps]
    queue = [sum(r["queue_ms"] for r in rows) for rows in steps]
    result["per_step"] = {
        "command_buffers": decode_size,
        "wall_ms_mean": per_step(walls), "wall_ms_median": rounded(statistics.median(walls), 4) if walls else None,
        "gpu_execute_ms_mean": per_step(busy), "encode_ms_mean": per_step(encode), "queue_ms_mean": per_step(queue),
        "gpu_idle_gap_ms_mean": rounded(per_step(walls) - per_step(busy) - per_step(queue), 4),
    }
    wall, gpu = per_step(walls), per_step(busy)

    by_class = collections.defaultdict(lambda: {"ms": 0.0, "nodes": 0, "bytes": 0, "buffers": 0})
    by_signature = collections.defaultdict(lambda: {"ms": 0.0, "count": 0, "nodes": 0, "bytes": 0, "attention": {}})
    for rows in steps:
        for r in rows:
            c = by_class[r["class"]]
            c["ms"] += r["execute_ms"] / len(steps)
            c["nodes"] += r["nodes"] / len(steps)
            c["bytes"] += r["input_bytes"] / len(steps)
            c["buffers"] += 1 / len(steps)
            s = by_signature[signature(r)]
            s["ms"] += r["execute_ms"] / len(steps)
            s["count"] += 1 / len(steps)
            s["bytes"] += r["input_bytes"] / len(steps)
            if r["attention"] and not s["attention"]:
                s["attention"] = r["attention"]
    result["by_class"] = {
        cls: {"ms_per_step": rounded(v["ms"]), "share_gpu_pct": rounded(100 * v["ms"] / gpu, 2) if gpu else None,
              "share_wall_pct": rounded(100 * v["ms"] / wall, 2) if wall else None,
              "buffers_per_step": v["buffers"], "nodes_per_step": v["nodes"],
              "input_bytes_per_step": int(v["bytes"]), "input_mb_per_step": rounded(v["bytes"] / 1e6, 2)}
        for cls, v in sorted(by_class.items(), key=lambda kv: -kv[1]["ms"])
    }
    ranked = sorted(by_signature.items(), key=lambda kv: -kv[1]["ms"])
    result["by_signature"] = [{
        "rank": i + 1, "class": k[0], "nodes_per_buffer": k[1], "input_mb_per_buffer": k[2],
        "buffers_per_step": v["count"], "ms_per_step": rounded(v["ms"]),
        "ms_per_buffer": rounded(v["ms"] / v["count"]) if v["count"] else None,
        "share_gpu_pct": rounded(100 * v["ms"] / gpu, 2) if gpu else None,
        "share_wall_pct": rounded(100 * v["ms"] / wall, 2) if wall else None,
        "input_mb_per_step": rounded(v["bytes"] / 1e6, 2), "attention": v["attention"],
    } for i, (k, v) in enumerate(ranked)]
    result["signature_count"] = len(ranked)

    # Layer visits, in execution order, from a reference step: a visit is a run of
    # command buffers that share the layer of their first named node.
    if steps:
        reference = steps[-1]
        visits, current = [], None
        for r in reference:
            if current is None or r["layer"] != current["layer"]:
                current = {"visit": len(visits) + 1, "layer": r["layer"], "classes": [], "buffers": 0}
                visits.append(current)
            if r["class"] not in current["classes"]:
                current["classes"].append(r["class"])
            current["buffers"] += 1
        same_length = [rows for rows in steps if len(rows) == len(reference)]
        for v in visits:
            v["buffers_mean_ms"] = 0.0
            v["buffers_mean_bytes"] = 0.0
        for rows in same_length:
            i = 0
            for v in visits:
                for _ in range(v["buffers"]):
                    r = rows[i]
                    v["buffers_mean_ms"] += r["execute_ms"] / len(same_length)
                    v["buffers_mean_bytes"] += r["input_bytes"] / len(same_length)
                    i += 1
        for v in visits:
            v["ms"] = rounded(v.pop("buffers_mean_ms"))
            v["input_mb"] = rounded(v.pop("buffers_mean_bytes") / 1e6, 2)
            v["share_gpu_pct"] = rounded(100 * v["ms"] / gpu, 2) if gpu else None
        result["by_visit"] = visits
        result["steps_matching_reference_length"] = len(same_length)
        # The last two steps, raw, for closer reading.
        result["raw_last_steps"] = [
            [[r["class"], r["layer"], r["nodes"], r["input_bytes"], rounded(r["execute_ms"], 4)] for r in rows]
            for rows in steps[-2:]
        ]

    aggregated = collections.defaultdict(lambda: {"count": 0, "ms": 0.0, "detail": ""})
    for o in others:
        g = aggregated[o["event"]]
        g["count"] += 1
        g["ms"] += o["ms"]
        if not g["detail"] and o["detail"]:
            g["detail"] = o["detail"]
    result["other_events"] = [{
        "event": name, "count_in_window": v["count"], "total_ms": rounded(v["ms"]),
        "mean_ms": rounded(v["ms"] / v["count"]) if v["count"] else None, "detail_example": v["detail"][:600],
    } for name, v in sorted(aggregated.items(), key=lambda kv: -kv[1]["ms"])[:40]]
    graph_ops = [o for o in others if o["event"] == "mtl.graph_ops"]
    result["graph_ops"] = {"count": len(graph_ops),
                           "first": graph_ops[0]["detail"] if graph_ops else None,
                           "last": graph_ops[-1]["detail"] if graph_ops else None}
    return emit(result, args)


def emit(result, args):
    text = json.dumps(result, separators=(",", ":"))
    with open(args.out, "w") as f:
        f.write(text + "\n")
    print("INKLING2_RESULT %s %s" % (args.tag, text), flush=True)
    brief = {k: result.get(k) for k in ("decode_step_count", "prefill_like_computes", "commands_missing", "per_step", "by_class")}
    print("profile summary: " + json.dumps(brief), file=sys.stderr, flush=True)
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
