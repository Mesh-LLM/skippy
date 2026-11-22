# Inference qualification (performance)

`inference-performance.yml` compares two Skippy commits, and optionally
llama.cpp's own server, on the same Metal machine. It is separate from
qualification: qualification proves one commit works; this measures whether
one commit is faster than another, so it needs every arm on one host, repeated
rounds, and load that exercises batching.

## Running it

Dispatch it from the Actions tab or with `gh`:

```bash
gh workflow run inference-performance.yml -f candidate=main \
  -f baseline=a192dece58aaef5618ab6101fb09cecc8f83c829 \
  -f models=qwen35-4b,qwen3-coder-30b-a3b -f nodes=METAL_NODE_A,METAL_NODE_B
```

| input | meaning |
|---|---|
| `candidate` | ref or SHA on main to measure, or `pr:N` for the head of open pull request N; default `main` |
| `baseline` | ref, SHA or `pr:N` to compare against; passing the candidate again gives an A/A noise run |
| `reference` | add llama.cpp's `llama-server` at the candidate's `llama_cpp/upstream.txt` pin; default on |
| `models` | `ci/inference-ladder.json` ids; shards are the hash-pinned files in `ci/inference-artifacts.json` |
| `nodes` | Metal nodes, one job each; empty runs one job on any Metal Mac with room for the largest model plus 16 GiB |
| `rounds` | rounds per arm, at most 9; default 3 |
| `trace` | after the rounds, one diagnostic pass of the candidate with debug telemetry; default off |

Both commits must be on protected main, unless an arm is `pr:N`: the head of an
open pull request from this repository, never a fork's, authored by a collaborator with write access, so only code that could
already run on trusted CI reaches the lab. Such an arm is reported, and stored,
as not on main; with `pr:N` as the baseline, a change stacked on another pull
request is measured against that one alone. The controller and the harness
always come from main, so every arm is driven by the same scripts and scenario,
and an older commit is never asked to run code it does not have.

## What one job does

1. Builds each arm from its own checkout with release settings: `just
   llama-build` with Metal static linking, then `cargo build --release -p
   skippy-cli`; the reference builds `llama-server` with CMake. Qualification
   uses debug builds; a comparison measures what ships.
2. Admits each model through the same hash-verified cache as qualification and
   checks the weights plus 16 GiB against the lesser of free and inactive
   memory and Metal's working-set limit, as qualification does.

Any Apple M-series Mac whose Metal device is its own GPU can run a job, not
only the M3 Ultra that qualification requires: arms are compared on one host,
never across hosts. The job refuses to run as root.
3. For each model and round, runs every arm in rotated order (each arm leads one
   round in three), and for each arm starts one server per entry in
   `ci/inference-performance.json`, then drives each of its points with the
   pinned AIPerf.

The scenario covers one request at a time, four lanes with short and long
output, sixteen lanes with and without a 4,096-token shared prefix in a 32K
pool, and four lanes at that depth with 16K and 32K pools: the cells where KV
pressure and batching show.

An arm that lacks a setting, such as `--kv-pool-tokens` on a commit from before
the pool existed, runs without it. Each sample records what was applied and
what the arm did not support. All arms are probed before model measurements begin.
An arm missing required endpoint/model/context/GPU or startup options fails explicitly
as unsupported before any measured round; required options are never silently omitted. `llama-server` gets the same lanes and the same
total context (`-np`, `-c`, `-kvu`). It also starts with `-lv 4`: at the pinned
llama.cpp that is the lowest log verbosity that prints the GPU-offload line the
startup check reads, and the sample records it as `log_verbosity`.

## Traces

With `trace` on, each job runs the candidate once more per model and server after
the measured rounds, with `--telemetry-level debug`. It writes the spans and the
resource gauges as OTLP/JSON lines, one file each per model and server
(`MODEL-SERVER.otlp.jsonl` and `.metrics.jsonl`), packed as `traces.tar.gz` in the
job's artifacts; the controller downloads it beside the evidence as
`LABEL-traces.tar.gz`. Each line is an `ExportTraceServiceRequest` that an
OTLP/HTTP collector accepts as it is, and DuckDB reads the files directly. Tracing slows the arm it runs on, so these
samples sit under `diagnostic` in `performance.json` and never join the medians.
An arm whose `serve` has no `--telemetry-level` records it as unsupported and
writes nothing.

## Evidence

The job writes `performance.json` with the harness commit, scenario digest,
host chip, macOS version and Metal working-set limit, every arm's commit and binary SHA-256, the model
shards, and one sample per model, round, arm and point. Each sample holds output
tokens per second, requests per second, time to first token (p50 and p95),
inter-token latency (p50), request latency (p50) and mean output tokens. Server
logs, build logs and the AIPerf exports (as `aiperf.tar.gz`) sit beside it.

The controller checks that evidence against the admitted plan and writes a table
of medians, round ranges and the candidate's change against the baseline to the
run summary. It uses the same measured-health layout as nightly Actions and the
Hugging Face card: verdict counts, regressions first, comparison charts and current
numbers. All model/server/point configurations appear. The tolerance is ±1%:
higher throughput and lower latency are favorable. Overlapping round ranges are
reported separately as uncertainty and do not override the tolerance verdict.
Controlled latency is the median of round p50 values, distinct from nightly's
mean of two trial p95 values. Baseline, candidate and optional llama.cpp reference
arms are measurements from the same experiment, not artificial history points.
Stored rows retain their actual timestamps for future trend analysis.

For graphs over time it also writes `LABEL-rows.jsonl` beside each job's
`LABEL-performance.json`, LABEL being the job's node or `any`: one row per measured sample,
with the job, when the run started, the host, the arm's commit and when it was
committed, the model revision, the server's lanes and pool, the point's
concurrency, depth and token counts, the round, and each metric as its own column.
An arm taken from a pull request has `on_main` false and its number in
`pull_request`; llama.cpp's server has `on_main` null. Rows from every run can
share one table. With DuckDB, over a folder per run, main's history alone:

```sql
select committed_at, arm, model, point,
       median(output_tok_s) as output_tok_s, median(ttft_ms_p50) as ttft_ms_p50
from read_json_auto('runs/*/*-rows.jsonl', union_by_name = true)
where on_main is not false
group by all order by committed_at, model, point, arm;
```

## Limits

- Metal only for now; Sparks follow the same shape with a CUDA build.
- Results live in artifacts, not yet in `jy result`.
- AIPerf's prefix flags (`--prefix-prompt-length`, `--prefix-prompt-pool-size`)
  and `ignore_eos` handling are taken from its documentation; the first run
  confirms them, and a renamed metric shows as null with the raw export kept.
- Nothing here has run on hardware yet.
