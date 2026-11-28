#!/usr/bin/env python3
"""Compare shared-prefix checkpoint admission, branch against grid, on one build.

Each arm serves the pinned model with --batch-checkpoint-admission set to it and runs two
AIPerf workloads:

  agentx       the nightly's AgentX scenario over the pinned Claude Code traces: multi-turn
               sessions whose turns extend the previous turn's prompt
  long-output  c4-long-output from the load harness: four 512-token prompts at once, each
               generating 2048 tokens, where a lone prompt's grid checkpoint costs a pass

Arms swap order every round so machine drift falls on both. summary.json holds every run,
each arm's means and branch's change against grid.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from lib.ci_artifacts import read_json, write_json
from lib.ci_process import cancel_on_signal, command, start, stop
from lib.inference_checks import ENDPOINT, verify_corpus
from lib.inference_inputs import admit, downloaded_path, fixture, safe_relative

ARMS = ("branch", "grid")
AIPERF = ["uv", "tool", "run", "--python", "3.12", "--constraints", "ci/aiperf-requirements.lock",
          "--from", "aiperf==0.13.0", "aiperf", "profile"]
# lanes and context per lane: the nightly AgentX server's, and the load harness's lanes4 server's
WORKLOADS = {"agentx": (1, 65536), "long-output": (4, 8192)}
METRICS = {
    "output_tok_s": ("output_token_throughput", "avg"),
    "request_per_s": ("request_throughput", "avg"),
    "ttft_ms_p50": ("time_to_first_token", "p50"),
    "ttft_ms_p95": ("time_to_first_token", "p95"),
    "itl_ms_p50": ("inter_token_latency", "p50"),
    "latency_ms_p50": ("request_latency", "p50"),
    "requests": ("request_count", "avg"),
}


def pinned_tokenizer(model):
    revision, files = model["tokenizer_revision"], model["tokenizer_files"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("unpinned tokenizer revision")
    tokenizer = downloaded_path(command(["hf", "download", model["tokenizer_repo"], "--revision", revision,
                                         "--quiet", *files], timeout=600, capture_stdout=True))
    if any((tokenizer / safe_relative(name)).stat().st_size == 0 for name in files):
        raise ValueError("empty pinned tokenizer asset")
    return tokenizer


def server_args(skippy, path, coordinate, workload, arm):
    lanes, context = WORKLOADS[workload]
    return [skippy, "serve", "--startup-timeout-secs", "300", "--ctx-size", str(context),
            "--n-gpu-layers", "999", "--bind-addr", ENDPOINT.removeprefix("http://"), "--model-path",
            str(path), "--model-id", coordinate, "--generation-concurrency", str(lanes),
            "--speculative-strategy", "disabled", "--debug", "--batch-checkpoint-admission", arm]


def workload_args(workload, coordinate, tokenizer, corpus, seconds, directory):
    lanes, context = WORKLOADS[workload]
    common = ["--ui", "simple", "--url", ENDPOINT, "--endpoint-type", "chat", "--streaming", "--model",
              coordinate, "--tokenizer", str(tokenizer), "--use-server-token-count", "--random-seed", "42",
              "--concurrency", str(lanes), "--artifact-dir", str(directory)]
    if workload == "agentx":
        # shorter than the nightly's validated run, so its submission rules are overridden as the
        # PR profile's are
        return common + ["--scenario", "inferencex-agentx-mvp", "--max-context-length", str(context),
                         "--cache-bust", "first-turn-prefix", "--public-dataset", corpus["alias"],
                         "--benchmark-duration", str(seconds), "--unsafe-override"]
    return common + ["--no-server-metrics", "--no-gpu-telemetry", "--request-count", "8",
                     "--warmup-request-count", "2", "--synthetic-input-tokens-mean", "512",
                     "--synthetic-input-tokens-stddev", "0", "--output-tokens-mean", "2048",
                     "--output-tokens-stddev", "0", "--extra-inputs", "ignore_eos:true"]


def measurements(directory):
    exports = sorted(directory.rglob("profile_export_aiperf.json"))
    if len(exports) != 1:
        raise ValueError(f"{directory.name}: expected one AIPerf export, found {len(exports)}")
    value = read_json(exports[0])
    count, errors = value["request_count"]["avg"], value["request_error_rate"]["avg"]
    if (not count or errors != 0 or value.get("error_summary") != []
            or value.get("is_complete") is not True):
        raise ValueError(f"{directory.name}: empty, incomplete or failed request evidence")
    return {name: (value.get(metric) or {}).get(statistic) for name, (metric, statistic) in METRICS.items()}


def wait_ready(server, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"server exited with {server.returncode} during startup")
        try:
            with urllib.request.urlopen(ENDPOINT + "/v1/models", timeout=5):
                return
        except OSError:
            time.sleep(2)
    raise RuntimeError("server did not answer /v1/models in time")


def measure(args, workload, arm, label, model, out):
    with (out / f"{label}-server.log").open("wb") as log:
        server = start(server_args(args.skippy, model["path"], model["coordinate"], workload, arm),
                       stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            wait_ready(server, 900)
            # AIPerf's ZeroMQ sockets live under TMPDIR, which must stay under the unix-socket path limit
            short_tmp = tempfile.mkdtemp(prefix="aiperf-", dir="/tmp")
            try:
                command(AIPERF + workload_args(workload, model["coordinate"], model["tokenizer"],
                                               model["corpus"], args.agentx_seconds, out / label),
                        timeout=args.agentx_seconds + 3600, overrides={"TMPDIR": short_tmp})
            finally:
                shutil.rmtree(short_tmp, ignore_errors=True)
        finally:
            stop(server)
    return measurements(out / label)


def summarize(runs):
    means = {}
    for workload in WORKLOADS:
        for arm in ARMS:
            rows = [run["metrics"] for run in runs if run["workload"] == workload and run["arm"] == arm]
            if rows:
                means.setdefault(workload, {})[arm] = {
                    key: sum(row[key] for row in rows) / len(rows)
                    for key in METRICS if all(row[key] is not None for row in rows)}
    change = {workload: {key: 100 * (arms["branch"][key] / arms["grid"][key] - 1)
                         for key in arms["branch"] if arms["grid"].get(key)}
              for workload, arms in means.items() if set(arms) == set(ARMS)}
    return {"means": means, "branch_vs_grid_pct": change}


def progress(span, done, total, note):
    line = f"{span[0] + (span[1] - span[0]) * done // total}% {note}"
    if os.environ.get("JIANYANG_PROGRESS_PATH"):
        Path(os.environ["JIANYANG_PROGRESS_PATH"]).write_text(line + "\n")
    print(f"PROGRESS {os.environ.get('JIANYANG_NODE', '?')} {time.strftime('%H:%M:%S')} {line}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skippy", default="target/release/skippy")
    parser.add_argument("--model", default="qwen35-4b", help="key in ci/inference-artifacts.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--workloads", default=",".join(WORKLOADS))
    parser.add_argument("--agentx-seconds", type=int, default=600)
    parser.add_argument("--progress", type=lambda v: tuple(map(int, v.split(","))), default=(0, 100))
    args = parser.parse_args()
    workloads = args.workloads.split(",")
    if not set(workloads) <= set(WORKLOADS):
        parser.error(f"--workloads takes {', '.join(WORKLOADS)}")
    args.out.mkdir(parents=True, exist_ok=True)

    pinned = fixture(args.model)
    model = {"path": admit(args.model, pinned), "coordinate": f'{pinned["repo"]}@{pinned["revision"]}',
             "tokenizer": pinned_tokenizer(pinned), "corpus": read_json(Path("ci/inference-corpus.json"))}
    if "agentx" in workloads:
        verify_corpus(model["corpus"], args.out, "before")

    runs, total = [], args.rounds * len(ARMS) * len(workloads)
    for round_index in range(args.rounds):
        for arm in ARMS if round_index % 2 == 0 else ARMS[::-1]:
            for workload in workloads:
                label = f"r{round_index + 1}-{arm}-{workload}"
                metrics = measure(args, workload, arm, label, model, args.out)
                runs.append({"round": round_index + 1, "arm": arm, "workload": workload, "metrics": metrics})
                write_json(args.out / "summary.json", {"model": model["coordinate"], "runs": runs, **summarize(runs)})
                print(label + ": " + ", ".join(f"{k} {v:.2f}" for k, v in metrics.items() if v is not None),
                      flush=True)
                progress(args.progress, len(runs), total, label)

    if "agentx" in workloads:
        verify_corpus(model["corpus"], args.out, "after")
    for workload, change in summarize(runs)["branch_vs_grid_pct"].items():
        print(f"{workload} branch vs grid: " + ", ".join(f"{k} {v:+.1f}%" for k, v in change.items()))
    return 0


if __name__ == "__main__":
    with cancel_on_signal():
        sys.exit(main())
