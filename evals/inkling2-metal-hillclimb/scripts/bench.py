#!/usr/bin/env python3
"""Inkling 2 Mini hill-climb bench against a running Skippy OpenAI endpoint.
Standard library only (Python >= 3.9).

Adapted from ~/.agents/skills/inference-optimisation/scripts/bench.py and keeping its
rules: token counts come from the server's usage/timings, never from streamed chunks;
decode is the median of at least 6 samples after a warm-up; LOOP, REQ_FAIL and
ON_BATTERY flags; one ledger line per run. It adds the shape this hill climb needs
(~1k-token prompt, 1k generated tokens, greedy), Skippy's own `timings`, server-timed
prefill, a streaming TTFT, a coherence sample, and the server's peak resident memory.

  python3 bench.py --phases sanity,c1,prefill,ttft --reps 6 --server-pid PID --tag NAME --out FILE
  python3 bench.py --phases c4 --concurrency 4 --reps 3 ...
  python3 bench.py --phases profile --warm 32 --profile-tokens 64 ...

Results go to --out and to one `INKLING2_RESULT TAG <json>` line on stdout.
"""
import argparse
import http.client
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

SYSTEM = "You are a helpful assistant."
# Fixed panel, as in the skill's bench: acceptance and speed depend on content.
PANEL = [
    "Continue these notes with a detailed engineering analysis of the service, with concrete examples.",
    "Summarise the notes above, then list the risks you see and how you would mitigate each one.",
    "Write an implementation plan for the service described in these notes, step by step.",
    "Explain the design in these notes to a new engineer, from first principles.",
    "Review these notes critically: what is missing, what is over-engineered, and why?",
    "Turn the notes above into a checklist an operator can run every morning.",
]
# Natural sentences: a prompt with ordinary token statistics, unique per seed.
SENTENCES = [
    "The scheduler groups requests by their prefix before it reserves memory for the batch.",
    "Each worker keeps a local copy of the weights and reports its queue depth every second.",
    "A prefix cache hit removes most of the prefill work but still needs a KV transfer.",
    "The router falls back to the least loaded worker when the primary is unavailable.",
    "Decode throughput depends on how many bytes of weights a single token must stream.",
    "Expert routing is decided per token, so two tokens rarely activate the same experts.",
    "Attention over a long context reads the whole KV cache unless the layer uses a window.",
    "The page table maps logical positions to physical slots in the KV pool.",
    "Chunked prefill trades a little throughput for shorter stalls between decode steps.",
    "Batching several streams amortises the weight reads across all of them.",
    "The compiler fuses the normalisation with the preceding matrix multiply.",
    "Quantised matrix-vector products rarely reach the memory bandwidth of a plain copy.",
    "The profiler splits the graph so each kind of work is timed on its own.",
    "A speculative draft lowers the bytes per accepted token when its proposals land.",
    "The KV cache grows with context while the weight traffic stays constant.",
    "Sliding-window layers bound the attention read to the window size.",
    "The draft model is small enough that its own cost is a few percent of a step.",
    "Sampling happens on the device to avoid a synchronisation point.",
    "The request log records prompt, decode and queue times separately.",
    "A cold start must read every weight from disk before the first token.",
    "Warm restarts find the weights in the page cache and start in seconds.",
    "The admission controller reserves KV space before it accepts a request.",
    "Memory pressure on a unified-memory machine is reported by the driver, not by RSS alone.",
    "Retrieval over a long document is checked with hidden codes at several depths.",
    "The acceptance rate of a draft is measured over many prompts, not one.",
    "Each layer visit reads its own attention projection and its own routed experts.",
    "The looped block reuses the same weights on every pass with fresh messages.",
    "Tensors are laid out so a single row is contiguous for the gather kernel.",
    "The command buffer is committed once per graph when profiling is disabled.",
    "Idle gaps between command buffers show where the host, not the GPU, is the limit.",
]

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def looping(text, n=8, thresh=0.5):
    """Degenerate-output flag: share of repeated word n-grams (as in the skill)."""
    w = text.split()
    grams = [tuple(w[i:i + n]) for i in range(max(0, len(w) - n))]
    return len(grams) > 50 and 1 - len(set(grams)) / len(grams) > thresh


def distinct_ratio(text, n=4):
    w = text.split()
    grams = [tuple(w[i:i + n]) for i in range(max(0, len(w) - n))]
    return round(len(set(grams)) / len(grams), 4) if grams else None


def on_ac():
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=20).stdout
        return "AC Power" in out
    except Exception:
        return True


def text(seed, n_sentences):
    rng = random.Random(seed)
    picked = [rng.choice(SENTENCES) for _ in range(n_sentences)]
    return " ".join(picked)


class RssWatch(threading.Thread):
    """Peak resident set size of the server, in bytes (ps rss is KiB on macOS)."""

    def __init__(self, pid):
        super().__init__(daemon=True)
        self.pid, self.peak, self.samples, self.stop = pid, 0, 0, threading.Event()

    def run(self):
        while not self.stop.wait(0.25):
            try:
                out = subprocess.run(["ps", "-o", "rss=", "-p", str(self.pid)], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
                if out:
                    self.peak = max(self.peak, int(out) * 1024)
                    self.samples += 1
            except Exception:
                continue


class Server:
    def __init__(self, base_url, model):
        u = urlparse(base_url)
        self.host, self.port = u.hostname, u.port or 80
        self.path = u.path.rstrip("/")
        self.model = model

    def healthy(self):
        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
            conn.request("GET", self.path + "/v1/models")
            ok = conn.getresponse().status == 200
            conn.close()
            return ok
        except Exception:
            return False

    def _body(self, prompt, max_tokens, ignore_eos, temperature):
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "temperature": temperature, "seed": 42, "max_tokens": max_tokens,
            "ignore_eos": ignore_eos, "stream": False,
        }

    def chat(self, prompt, max_tokens, ignore_eos=False, temperature=0.0, timeout=3600):
        """Non-streaming chat completion. Returns the server's usage and timings."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        payload = json.dumps(self._body(prompt, max_tokens, ignore_eos, temperature)).encode()
        t0 = time.perf_counter()
        try:
            conn.request("POST", self.path + "/v1/chat/completions", body=payload,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            status, raw = resp.status, resp.read()
        finally:
            conn.close()
        wall = time.perf_counter() - t0
        out = {"status": status, "wall_s": round(wall, 4)}
        data = json.loads(raw.decode("utf-8", "replace")) if raw else {}
        if status != 200:
            out["error"] = data
            return out
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}
        details = usage.get("prompt_tokens_details") or {}
        out.update({
            "text": message.get("content") or "",
            "reasoning": message.get("reasoning_content") or message.get("reasoning") or "",
            "finish_reason": choice.get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cached_tokens": details.get("cached_tokens"),
            "timings": timings,
            "prompt_n": timings.get("prompt_n"),
            "prompt_ms": timings.get("prompt_ms"),
            "predicted_n": timings.get("predicted_n"),
            "predicted_ms": timings.get("predicted_ms"),
        })
        return out

    def stream_ttft(self, prompt, max_tokens=8, ignore_eos=True, timeout=3600):
        """Streaming chat completion: time to the first chunk and to the first piece of text."""
        body = self._body(prompt, max_tokens, ignore_eos, 0.0)
        body.update({"stream": True, "stream_options": {"include_usage": True}})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        payload = json.dumps(body).encode()
        t0, t0_ns = time.perf_counter(), time.time_ns()
        conn.request("POST", self.path + "/v1/chat/completions", body=payload,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        out = {"status": resp.status, "t_headers_s": round(time.perf_counter() - t0, 4), "chunks": 0}
        first_chunk = first_text = None
        usage, timings, pieces = {}, {}, []
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if first_chunk is None:
                first_chunk = time.perf_counter() - t0
            out["chunks"] += 1
            if event.get("usage"):
                usage = event["usage"]
            if event.get("timings"):
                timings = event["timings"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = (delta.get("content") or "") + (delta.get("reasoning_content") or delta.get("reasoning") or "")
                if piece:
                    pieces.append(piece)
                    if first_text is None:
                        first_text = time.perf_counter() - t0
        conn.close()
        out.update({
            "ttft_ms": round(first_text * 1000, 2) if first_text is not None else None,
            "first_chunk_ms": round(first_chunk * 1000, 2) if first_chunk is not None else None,
            "total_s": round(time.perf_counter() - t0, 4),
            "window_ns": [t0_ns, time.time_ns()],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "timings": timings,
            "text": "".join(pieces)[:200],
        })
        return out


def decode_tps(r):
    """Server-timed decode: completion tokens over the decode phase only."""
    if r.get("predicted_n") and r.get("predicted_ms"):
        return round(r["predicted_n"] / r["predicted_ms"] * 1000.0, 2)
    return None


def wall_decode_tps(r):
    """Client-timed decode: completion tokens over the wall time after the prefill."""
    if r.get("completion_tokens") and r.get("prompt_ms") is not None and r.get("wall_s"):
        decode = r["wall_s"] - r["prompt_ms"] / 1000.0
        return round(r["completion_tokens"] / decode, 2) if decode > 0 else None
    return None


def median_or_none(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def words_for(server, target, tolerance=0.04):
    """Sentences needed for a prompt of about `target` tokens, from the server's count."""
    n = max(4, int(target / 19))
    for _ in range(4):
        r = server.chat(text(1000 + n, n), 1, ignore_eos=True)
        count = r.get("prompt_n") or r.get("prompt_tokens")
        if not count:
            break
        if abs(count - target) <= tolerance * target:
            break
        n = max(4, int(round(n * target / float(count))))
    log("prompt calibration: %d sentences -> %s tokens (target %s)" % (n, count, target))
    return n


def phase_sanity(server, res):
    """One greedy 1k/1k sample with EOS honoured: must be coherent, not looping."""
    n = words_for(server, 950)
    prompt = text(777, n) + "\n\nBased on the notes above, explain the main ideas in plain language and give three concrete recommendations."
    r = server.chat(prompt, 1000, ignore_eos=False)
    entry = {
        "prompt_n": r.get("prompt_n"), "completion_tokens": r.get("completion_tokens"),
        "finish_reason": r.get("finish_reason"), "prompt_ms": r.get("prompt_ms"),
        "predicted_ms": r.get("predicted_ms"), "wall_s": r.get("wall_s"), "status": r.get("status"),
    }
    if r.get("status") == 200:
        body = r.get("text") or r.get("reasoning") or ""
        entry.update({
            "first_200_chars": body[:200], "last_200_chars": body[-200:], "chars": len(body),
            "distinct_4gram_ratio": distinct_ratio(body), "looping": looping(body),
            "text": body,
        })
        if looping(body):
            res["flags"].append("LOOP")
    else:
        entry["error"] = r.get("error")
        res["flags"].append("REQ_FAIL")
    res["sanity"] = entry
    log("sanity: finish=%s completion_tokens=%s looping=%s" % (entry.get("finish_reason"), entry.get("completion_tokens"), entry.get("looping")))


def phase_c1(server, res, reps):
    server.chat("Say hello.", 32, ignore_eos=True)  # warm-up (not measured)
    n = words_for(server, 1000)
    samples = []
    for i in range(reps):
        prompt = text(2000 + i, n) + "\n\n" + PANEL[i % len(PANEL)]
        r = server.chat(prompt, 1000, ignore_eos=True)
        row = {"seed": 2000 + i, "status": r.get("status"), "prompt_n": r.get("prompt_n"),
               "completion_tokens": r.get("completion_tokens"), "cached_tokens": r.get("cached_tokens"),
               "prompt_ms": r.get("prompt_ms"), "predicted_ms": r.get("predicted_ms"), "wall_s": r.get("wall_s"),
               "decode_tps": decode_tps(r), "wall_decode_tps": wall_decode_tps(r)}
        if r.get("status") != 200:
            row["error"] = r.get("error")
            res["flags"].append("REQ_FAIL")
        samples.append(row)
        log("c1 sample %d: %s tok/s (prompt %s, decode %s ms)" % (i + 1, row["decode_tps"], row["prompt_n"], row["predicted_ms"]))
    res["c1_samples"] = samples
    res["c1_decode"] = median_or_none([s["decode_tps"] for s in samples])
    res["c1_decode_all"] = [s["decode_tps"] for s in samples]
    res["c1_wall_decode"] = median_or_none([s["wall_decode_tps"] for s in samples])
    res["c1_prompt_tokens"] = median_or_none([s["prompt_n"] for s in samples])
    res["c1_completion_tokens"] = median_or_none([s["completion_tokens"] for s in samples])
    res["c1_ttft_prompt_ms"] = median_or_none([s["prompt_ms"] for s in samples])
    res["c1_step_ms"] = median_or_none([1000.0 / s["decode_tps"] for s in samples if s["decode_tps"]])
    log("c1 median %s tok/s" % res["c1_decode"])


def phase_prefill(server, res, lengths):
    res["prefill"] = {}
    for target in lengths:
        n = words_for(server, target)
        rows = []
        for i in range(3):
            r = server.chat(text(3000 + target + i, n), 1, ignore_eos=True)
            if r.get("status") != 200:
                rows.append({"status": r.get("status"), "error": r.get("error")})
                res["flags"].append("REQ_FAIL")
                continue
            tps = round(r["prompt_n"] / r["prompt_ms"] * 1000.0, 1) if r.get("prompt_ms") else None
            rows.append({"prompt_n": r.get("prompt_n"), "cached_tokens": r.get("cached_tokens"),
                         "prompt_ms": r.get("prompt_ms"), "wall_s": r.get("wall_s"), "prefill_tps": tps,
                         "ttft_s": r.get("wall_s")})
        res["prefill"][str(target)] = {
            "samples": rows,
            "prefill_tps": median_or_none([x.get("prefill_tps") for x in rows]),
            "prompt_tokens": median_or_none([x.get("prompt_n") for x in rows]),
            "prompt_ms": median_or_none([x.get("prompt_ms") for x in rows]),
            "ttft_ms": median_or_none([x.get("prompt_ms") for x in rows]),
        }
        res["prefill_%d" % target] = res["prefill"][str(target)]["prefill_tps"]
        res["prefill_%d_tokens" % target] = res["prefill"][str(target)]["prompt_tokens"]
        log("prefill ~%d: %s tok/s over %s tokens" % (target, res["prefill_%d" % target], res["prefill_%d_tokens" % target]))


def phase_ttft(server, res):
    n = words_for(server, 1000)
    rows = []
    for i in range(3):
        r = server.stream_ttft(text(4000 + i, n) + "\n\n" + PANEL[i % len(PANEL)], 8, ignore_eos=True)
        if r.get("status") != 200:
            rows.append({"status": r.get("status")})
            res["flags"].append("REQ_FAIL")
            continue
        rows.append({k: r[k] for k in ("ttft_ms", "first_chunk_ms", "prompt_tokens", "completion_tokens", "total_s", "timings")})
        log("ttft sample %d: %s ms (first chunk %s ms)" % (i + 1, r["ttft_ms"], r["first_chunk_ms"]))
    res["ttft_samples"] = rows
    res["ttft_ms"] = median_or_none([x.get("ttft_ms") for x in rows])
    res["ttft_prompt_tokens"] = median_or_none([x.get("prompt_tokens") for x in rows])


def phase_c4(server, res, concurrency, reps):
    server.chat("Say hello.", 32, ignore_eos=True)  # warm-up
    n = words_for(server, 1000)
    samples = []
    for i in range(reps):
        with ThreadPoolExecutor(concurrency) as pool:
            runs = list(pool.map(lambda k: server.chat(text(5000 + i * 10 + k, n) + "\n\n" + PANEL[k % len(PANEL)], 1000, ignore_eos=True), range(concurrency)))
        ok = all(r.get("status") == 200 for r in runs)
        if not ok:
            samples.append({"status": [r.get("status") for r in runs], "error": [r.get("error") for r in runs]})
            res["flags"].append("REQ_FAIL")
            continue
        tokens = sum(r["completion_tokens"] or 0 for r in runs)
        # Server-timed: the decode window shared by the batch is the longest (prompt + decode).
        window = max(r["prompt_ms"] + r["predicted_ms"] for r in runs) - min(r["prompt_ms"] for r in runs)
        aggregate = round(tokens / window * 1000.0, 2) if window > 0 else None
        samples.append({
            "tokens": tokens, "window_ms": round(window, 1), "aggregate_tps": aggregate,
            "per_stream_tps": [decode_tps(r) for r in runs],
            "prompt_tokens": [r["prompt_n"] for r in runs], "wall_s": [r["wall_s"] for r in runs],
        })
        log("c%d sample %d: %s tok/s aggregate, per stream %s" % (concurrency, i + 1, aggregate, samples[-1]["per_stream_tps"]))
    res["c%d_samples" % concurrency] = samples
    res["c%d_aggregate" % concurrency] = median_or_none([s.get("aggregate_tps") for s in samples])
    per = [v for s in samples for v in s.get("per_stream_tps", []) if v is not None]
    res["c%d_per_stream" % concurrency] = round(statistics.median(per), 2) if per else None


def phase_profile(server, res, warm, tokens):
    server.chat("Say hello.", warm, ignore_eos=True)  # compiles pipelines and warms allocations
    n = words_for(server, 1000)
    r = server.stream_ttft(text(6000, n) + "\n\n" + PANEL[0], tokens, ignore_eos=True)
    res["profile"] = {k: r.get(k) for k in ("status", "window_ns", "prompt_tokens", "completion_tokens", "ttft_ms", "timings")}
    if r.get("status") != 200:
        res["flags"].append("REQ_FAIL")
    log("profile window %s: %s prompt tokens, %s completion tokens" % (r.get("window_ns"), r.get("prompt_tokens"), r.get("completion_tokens")))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:19337")
    parser.add_argument("--model-id", default="inkling2-mini")
    parser.add_argument("--quant", default="")
    parser.add_argument("--node", default=os.uname().nodename)
    parser.add_argument("--phases", default="sanity,c1,prefill,ttft")
    parser.add_argument("--reps", type=int, default=6)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--prefill-lengths", default="2048,8192")
    parser.add_argument("--warm", type=int, default=32)
    parser.add_argument("--profile-tokens", type=int, default=64)
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--tag", default="bench")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    server = Server(args.base_url, args.model_id)
    res = {"tag": args.tag, "name": args.tag, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "time_ns": time.time_ns(), "node": args.node, "quant": args.quant, "flags": [],
           "base_url": args.base_url, "model_id": args.model_id, "phases": args.phases}
    if not server.healthy():
        res.update(ok=False, error="server not healthy at %s" % args.base_url)
        return finish(res, None, args)
    watch = RssWatch(args.server_pid) if args.server_pid else None
    if watch:
        watch.start()
    try:
        for name in args.phases.split(","):
            if name == "sanity":
                phase_sanity(server, res)
            elif name == "c1":
                phase_c1(server, res, args.reps)
            elif name == "prefill":
                phase_prefill(server, res, [int(x) for x in args.prefill_lengths.split(",")])
            elif name == "ttft":
                phase_ttft(server, res)
            elif name == "c4":
                phase_c4(server, res, args.concurrency, max(1, args.reps // 2))
            elif name == "profile":
                phase_profile(server, res, args.warm, args.profile_tokens)
            else:
                raise ValueError("unknown phase %s" % name)
        res["ok"] = "REQ_FAIL" not in res["flags"]
    except Exception as error:  # keep the partial result and its ledger line
        res.update(ok=False, error=repr(error))
        res["flags"].append("REQ_FAIL")
    return finish(res, watch, args)


def finish(res, watch, args):
    if watch:
        watch.stop.set()
        res["mem_peak_gib"] = round(watch.peak / 2**30, 2)
        res["mem_samples"] = watch.samples
    if not on_ac():
        res["flags"].append("ON_BATTERY")
        res["ok"] = False
    res["flags"] = sorted(set(res["flags"]))
    text = json.dumps(res, separators=(",", ":"))
    with open(args.out, "w") as f:
        f.write(text + "\n")
    print("INKLING2_RESULT %s %s" % (args.tag, text), flush=True)
    log("done %s ok=%s flags=%s" % (res["tag"], res.get("ok"), res["flags"]))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
