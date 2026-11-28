#!/usr/bin/env python3
"""Does reading graph inputs in place corrupt output on an integrated CUDA GPU?

Upstream reports every CUDA device as discrete because integrated devices corrupted
output on Jetson Orin (llama.cpp #15034). GGML_CUDA_INTEGRATED=1 restores the device's
own flag, so kernels read graph inputs from host buffers instead of a device copy.
Each model is served once per arm:

  off          the default: inputs are copied to device memory
  on           GGML_CUDA_INTEGRATED=1
  on-blocking  on, with CUDA_LAUNCH_BLOCKING=1, so no kernel is still running when
               the host writes the next inputs
  on-trace     on, traced, to count the input uploads left
  off-trace    off, traced, for the same count
  on-device    on, with LLAMA_IGPU_HOST_STATE_DISABLE=1, so inputs are read in place
               but the KV cache and recurrent state stay in device memory
  on-device-trace  on-device, traced

A run sends its requests one at a time (a count, then two long prompts that take
several ubatches each), then all together. Every answer is checked for sense: a count
must run on from 1 and a needle must be found. One-at-a-time answers are also compared
word for word with the first off run's, which only off runs need to match.

Corruption in on that on-blocking does not show points to a race between host writes
and running kernels; corruption in both points elsewhere. on against on-device splits a
speed change between the in-place inputs and the host-resident state. With --profile each
server runs under Nsight Systems, and the kernels whose GPU time differs most between arms
are reported. The script exits 0 whenever
the experiment ran, whatever it found: summary.json holds the verdicts.
"""

import argparse
import collections
import csv
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 9449
ENDPOINT = f"http://127.0.0.1:{PORT}"
MODEL_ID = "integrated-smoke"
COUNT_TO = 200
COUNT_MIN = 60
WORDS = ["amber", "basalt", "cobalt", "delta", "ember", "fjord", "garnet", "harbor",
         "indigo", "juniper", "kestrel", "lantern", "meadow", "nectar", "onyx", "pepper",
         "quartz", "raven", "saffron", "tundra", "umber", "velvet", "willow", "xenon",
         "yarrow", "zephyr", "acorn", "bramble", "cinder", "dune", "estuary", "falcon"]
ARMS = {
    "off": ({}, False),
    "on": ({"GGML_CUDA_INTEGRATED": "1"}, False),
    "on-blocking": ({"GGML_CUDA_INTEGRATED": "1", "CUDA_LAUNCH_BLOCKING": "1"}, False),
    "on-trace": ({"GGML_CUDA_INTEGRATED": "1"}, True),
    "off-trace": ({}, True),
    "on-device": ({"GGML_CUDA_INTEGRATED": "1", "LLAMA_IGPU_HOST_STATE_DISABLE": "1"}, False),
    "on-device-trace": ({"GGML_CUDA_INTEGRATED": "1", "LLAMA_IGPU_HOST_STATE_DISABLE": "1"}, True),
}
ON_ARMS = ("on", "on-trace", "on-device", "on-device-trace")
DEFAULT_ARMS = "off,on,on-device,on-trace,on-device-trace,off-trace,off,on,on-device"
# ggml_cuda_init logs this once per device the flag turned on
IN_PLACE_LINE = "reads host buffers in place"
# llama.cpp logs each cache buffer it allocates, named for its buffer type (CUDA0, CUDA_Host)
STATE_BUFFER = re.compile(r"(\S+ (?:KV|RS)) buffer size")
DETAIL_DEVICE = re.compile(r"^[A-Z]+\d+#")
DETAIL_INDEX = re.compile(r"#\d+$")


def server_args(skippy, model, trace):
    # the load-chunks variant of ci/inference-performance.json, as the rate runs serve it
    args = [skippy, "serve", "--startup-timeout-secs", "600", "--ctx-size", "8192",
            "--n-gpu-layers", "999", "--bind-addr", f"127.0.0.1:{PORT}",
            "--model-path", model, "--model-id", MODEL_ID,
            "--generation-concurrency", "6", "--speculative-strategy", "disabled",
            "--kv-pool-tokens", "32768", "--debug",
            "--batch-policy", "prompt_order", "--batch-prefill-wait", "active",
            "--batch-wait-sessions", "direct", "--batch-prefill-chunk", "adaptive",
            "--batch-checkpoint-align", "microbatch"]
    return args + ["--telemetry-level", "debug"] if trace else args


def needle(seed, records=160, target=117):
    words = [f"{WORDS[(seed * 7 + i * 13) % len(WORDS)]}{(seed * 31 + i * 17) % 97}" for i in range(records)]
    lines = [f"Record {i:04d}: the code word is {words[i]}." for i in range(records)]
    question = f"What is the code word for record {target:04d}? Answer with the code word only."
    return "\n".join(lines) + "\n\n" + question, words[target]


def chat(prompt, max_tokens, timeout=300):
    payload = {"model": MODEL_ID, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0, "seed": 42, "max_tokens": max_tokens,
               "chat_template_kwargs": {"enable_thinking": False}}
    request = urllib.request.Request(ENDPOINT + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())["choices"][0]["message"]["content"]


def check_count(text):
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    consecutive = 0
    for expected, number in enumerate(numbers, start=1):
        if number != expected:
            break
        consecutive = expected
    return consecutive >= COUNT_MIN and consecutive == len(numbers), f"consecutive to {consecutive} of {len(numbers)}"


def check_needle(text, expected):
    return expected.lower() in text.lower(), f"expected {expected}"


def requests():
    """(name, start delay when sent together, prompt, max tokens, checker)"""
    count = f"Count from 1 to {COUNT_TO}, separated by commas. Output only the numbers."
    jobs = [("count", 0.0, count, 900, check_count)]
    for name, delay, seed in (("needleA", 1.0, 1), ("needleB", 2.5, 2)):
        prompt, expected = needle(seed)
        jobs.append((name, delay, prompt, 24, lambda text, expected=expected: check_needle(text, expected)))
    return jobs


def together():
    count = requests()[0]
    return [(f"count{i}", 0.2 * i, *count[2:]) for i in range(3)] + requests()[1:]


def ask(job):
    name, _, prompt, max_tokens, checker = job
    started = time.monotonic()
    try:
        text = chat(prompt, max_tokens)
        passed, detail = checker(text)
    except (OSError, KeyError, ValueError) as error:
        text, passed, detail = "", False, f"request failed: {error}"
    return name, {"passed": passed, "detail": detail, "seconds": round(time.monotonic() - started, 2), "text": text}


def ask_together(jobs):
    results = {}

    def one(job):
        time.sleep(job[1])
        name, result = ask(job)
        results[name] = result

    threads = [threading.Thread(target=one, args=(job,)) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def wait_ready(server, deadline):
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"server exited with {server.returncode} during startup")
        try:
            with urllib.request.urlopen(ENDPOINT + "/v1/models", timeout=5):
                return
        except OSError:
            time.sleep(2)
    raise RuntimeError("server did not answer /v1/models in time")


def stop(server, timeout=60):
    if server.poll() is None:
        os.killpg(server.pid, signal.SIGINT)
        try:
            server.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)
            server.wait()


def uploads(path):
    """Host-to-device copies by tensor, from the run's OTLP span lines."""
    counts = collections.Counter()
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            for resource in row.get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        if not span.get("name", "").startswith("cuda.upload"):
                            continue
                        detail = next((next(iter(item["value"].values()), "") for item in span.get("attributes", [])
                                       if item["key"] == "skippy.native.detail" and item["value"]), "")
                        counts[DETAIL_INDEX.sub("", DETAIL_DEVICE.sub("", str(detail))) or "-"] += 1
    return dict(counts)


def nsys_command(nsys, stem):
    # node tracing, because ggml-cuda replays decode steps as CUDA graphs
    return [nsys, "profile", "--trace=cuda", "--cuda-graph-trace=node", "--sample=none", "--cpuctxsw=none",
            "--force-overwrite=true", "--output", str(stem)]


def nsys_table(path):
    """One nsys stats CSV as {name: (total_ms, instances)}."""
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        return {}
    total = next(key for key in rows[0] if key.startswith("Total Time"))
    count = next(key for key in rows[0] if key.startswith("Instances") or key.startswith("Count"))
    name = next(key for key in rows[0] if key in ("Name", "Operation"))
    return {row[name]: (float(row[total]) / 1e6, int(row[count])) for row in rows}


def nsys_stats(nsys, report):
    """Kernel and memory-operation GPU time from a finished report; the SQLite export it needs is removed."""
    base = report.with_suffix("")
    subprocess.run([nsys, "stats", "--force-export=true", "--format", "csv", "--output", str(base),
                    "--report", "cuda_gpu_kern_sum", "--report", "cuda_gpu_mem_time_sum", str(report)],
                   check=True, stdout=subprocess.DEVNULL)
    base.with_suffix(".sqlite").unlink(missing_ok=True)
    tables = {}
    for key, report_name in (("kernels", "cuda_gpu_kern_sum"), ("memops", "cuda_gpu_mem_time_sum")):
        found = sorted(report.parent.glob(f"{base.name}*{report_name}*.csv"))
        tables[key] = nsys_table(found[0]) if found else {}
    return tables


def run_arm(label, arm, skippy, model, stem, nsys=None):
    extra_env, trace = ARMS[arm]
    log = stem.with_name(f"{stem.name}-server.log")
    trace_file = stem.with_name(f"{stem.name}.otlp.jsonl")
    env = dict(os.environ, **extra_env)
    if trace:
        env["SKIPPY_TELEMETRY_OTLP_FILE"] = str(trace_file)
    run = {"label": label, "arm": arm, "env": extra_env, "trace": trace, "log": str(log)}
    report = stem.with_name(f"{stem.name}-nsys.nsys-rep")
    command = (nsys_command(nsys, report.with_suffix("")) if nsys else []) + server_args(skippy, model, trace)
    with log.open("wb") as stream:
        server = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                  stderr=stream, env=env, start_new_session=True)
        try:
            wait_ready(server, time.monotonic() + 900)
            # a long prompt pages the weights in before anything is compared
            chat("Say OK.", 4)
            chat(needle(99)[0], 8)
            run["alone"] = dict(ask(job) for job in requests())
            run["together"] = ask_together(together())
            run["alive"] = server.poll() is None
        except (RuntimeError, OSError, KeyError, ValueError) as error:
            run["error"] = str(error)
            run["alive"] = False
        finally:
            # nsys writes its report after the server exits
            stop(server, timeout=900 if nsys else 60)
    text = log.read_text(errors="replace")
    run["in_place_logged"] = text.count(IN_PLACE_LINE)
    run["state_buffers"] = dict(collections.Counter(STATE_BUFFER.findall(text)))
    if trace:
        run["uploads"] = uploads(trace_file)
    if nsys:
        try:
            run["nsys"] = nsys_stats(nsys, report) if report.exists() else {"error": "no report written"}
        except (subprocess.CalledProcessError, OSError, StopIteration, ValueError) as error:
            run["nsys"] = {"error": str(error)}
    return run


def passed(run):
    return "error" not in run and run["alive"] and all(
        result["passed"] for phase in ("alone", "together") for result in run[phase].values())


def first_difference(a, b):
    if a == b:
        return None
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def upload_total(runs, arm):
    totals = [sum(run["uploads"].values()) for run in runs if run["arm"] == arm and run.get("uploads") is not None]
    return totals[0] if totals else None


def took_effect(runs, ons):
    """The flag's own log line, or else a traced on run that copied far less than a traced off run."""
    if all(run["in_place_logged"] for run in ons):
        return True
    on, off = upload_total(runs, "on-trace"), upload_total(runs, "off-trace")
    return on is not None and off is not None and on < off / 2


def verdict(runs):
    offs = [run for run in runs if run["arm"] in ("off", "off-trace")]
    ons = [run for run in runs if run["arm"] in ON_ARMS]
    blocking = [run for run in runs if run["arm"] == "on-blocking"]
    if not offs or not all(passed(run) for run in offs):
        return "the off arm failed its checks, so nothing can be concluded"
    if not took_effect(runs, ons + blocking):
        return "neither the log nor the traced uploads show the flag taking effect"
    if all(passed(run) for run in ons + blocking):
        return "no corruption: every on run passed its checks"
    if any(not passed(run) for run in ons) and blocking and all(passed(run) for run in blocking):
        return "corrupts, and blocking launches prevent it: a race between host writes and running kernels"
    if blocking and any(not passed(run) for run in blocking):
        return "corrupts even with blocking launches: not a race with running kernels"
    return "mixed: read the runs"


def seconds_by_arm(runs):
    """Mean seconds per arm for the count alone and the counts run together, the checks long enough to time."""
    timed = collections.defaultdict(lambda: {"alone": [], "together": []})
    for run in runs:
        if "error" in run:
            continue
        timed[run["arm"]]["alone"] += [r["seconds"] for job, r in run["alone"].items() if job.startswith("count")]
        timed[run["arm"]]["together"] += [r["seconds"] for job, r in run["together"].items() if job.startswith("count")]
    return {arm: {phase: round(sum(values) / len(values), 2) if values else None for phase, values in phases.items()}
            for arm, phases in timed.items()}


def gpu_time_by_arm(runs, key):
    """Mean GPU ms per name for each arm, over its profiled runs."""
    sums = collections.defaultdict(lambda: collections.defaultdict(float))
    counts = collections.Counter()
    for run in runs:
        table = run.get("nsys", {}).get(key)
        if not table:
            continue
        counts[run["arm"]] += 1
        for name, (total_ms, _) in table.items():
            sums[run["arm"]][name] += total_ms
    return {arm: {name: total / counts[arm] for name, total in names.items()} for arm, names in sums.items()}


def gpu_time_deltas(runs, key, slow="on", fast="on-device", top=15):
    """The names whose GPU ms grew most from the fast arm to the slow one."""
    by_arm = gpu_time_by_arm(runs, key)
    if slow not in by_arm or fast not in by_arm:
        return []
    names = set(by_arm[slow]) | set(by_arm[fast])
    rows = [{"name": name, slow: round(by_arm[slow].get(name, 0.0), 2), fast: round(by_arm[fast].get(name, 0.0), 2),
             "off": round(by_arm.get("off", {}).get(name, 0.0), 2)} for name in names]
    rows.sort(key=lambda row: row[fast] - row[slow])
    return rows[:top]


def report(name, runs):
    print(f"== {name}")
    reference = next((run for run in runs if run["arm"] == "off" and "alone" in run), None)
    for run in runs:
        if "error" in run or reference is None:
            print(f"  {run['label']:16} ERROR {run.get('error', 'no off run answered to compare with')}")
            continue
        marks = []
        for phase in ("alone", "together"):
            failed = [job for job, result in run[phase].items() if not result["passed"]]
            marks.append(f"{phase} {'ok' if not failed else 'FAIL ' + ','.join(failed)}")
        same = [job for job, result in run["alone"].items()
                if first_difference(result["text"], reference["alone"][job]["text"]) is None]
        seconds = " ".join(f"{job} {result['seconds']:.1f}s" for job, result in run["alone"].items())
        print(f"  {run['label']:16} {'; '.join(marks)}; same as {reference['label']}: {len(same)}/"
              f"{len(run['alone'])}; logged in place: {run['in_place_logged']}; {seconds}")
        for job, result in run["alone"].items():
            at = first_difference(result["text"], reference["alone"][job]["text"])
            if at is not None:
                print(f"    {job} differs from character {at}: {result['text'][max(0, at - 20):at + 60]!r}")
    for run in runs:
        if run.get("uploads") is not None:
            top = sorted(run["uploads"].items(), key=lambda item: -item[1])[:6]
            print(f"  {run['label']} uploads {sum(run['uploads'].values())}: "
                  + ", ".join(f"{tensor} {count}" for tensor, count in top))
    for run in runs:
        print(f"  {run['label']} state buffers: {run.get('state_buffers') or 'not in the log'}")
    for arm, phases in seconds_by_arm(runs).items():
        print(f"  {arm:16} count alone {phases['alone']}s, counts together {phases['together']}s")
    for key in ("kernels", "memops"):
        for row in gpu_time_deltas(runs, key):
            print(f"  {key} on {row['on']:9.1f} ms, on-device {row['on-device']:9.1f} ms, off {row['off']:9.1f} ms: "
                  f"{row['name'][:110]}")
    print(f"  verdict: {verdict(runs)}", flush=True)


def progress_span(value):
    start, end = (int(part) for part in value.split(","))
    if not 0 <= start <= end <= 100:
        raise argparse.ArgumentTypeError("--progress takes START,END percents")
    return start, end


def report_progress(span, done, total, note):
    """The job's percent and the run just finished, for JY's heartbeat and the job log."""
    percent = span[0] + (span[1] - span[0]) * done // total
    line = f"{percent}% {note[:59]}"
    path = os.environ.get("JIANYANG_PROGRESS_PATH")
    if path:
        Path(path).write_text(line + "\n")
    print(f"PROGRESS {os.environ.get('JIANYANG_NODE', '?')} {time.strftime('%H:%M:%S')} {line}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skippy", default="target/release/skippy")
    parser.add_argument("--model", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--out", required=True)
    parser.add_argument("--arms", default=DEFAULT_ARMS, help="comma-separated runs, in order, repeats allowed")
    parser.add_argument("--profile", metavar="NSYS", help="run each server under this nsys and compare kernel GPU time")
    parser.add_argument("--progress", type=progress_span, default=(0, 100), metavar="START,END",
                        help="the share of the job's progress these runs cover")
    args = parser.parse_args()
    order = args.arms.split(",")
    unknown = [arm for arm in order if arm not in ARMS]
    if unknown or "off" not in order:
        parser.error(f"--arms takes names from {sorted(ARMS)} and needs off")
    models = [spec.split("=", 1) for spec in args.model]
    if any(len(spec) != 2 or not Path(spec[1]).is_file() for spec in models):
        parser.error("--model takes NAME=PATH to a GGUF file")

    # one flat directory, so each file is an artifact of its own
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    total = len(models) * len(order)
    for position, (name, path) in enumerate(models):
        runs = []
        for index, arm in enumerate(order, start=1):
            label = f"{arm}#{index}"
            print(f"-- {name} {label}", flush=True)
            runs.append(run_arm(label, arm, args.skippy, path, out / f"{name}-{arm}-{index}", args.profile))
            report_progress(args.progress, position * len(order) + index, total, f"{name} {arm} {index}")
        report(name, runs)
        summary[name] = {"model": path, "verdict": verdict(runs), "seconds": seconds_by_arm(runs),
                         "kernel_deltas": gpu_time_deltas(runs, "kernels", top=40),
                         "memop_deltas": gpu_time_deltas(runs, "memops", top=40), "runs": runs}
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"summary: {out / 'summary.json'}")
    ran = all(any("alone" in run for run in entry["runs"]) for entry in summary.values())
    return 0 if ran else 1


if __name__ == "__main__":
    sys.exit(main())
