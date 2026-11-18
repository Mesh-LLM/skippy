# Phase 1 KV cache parity evidence

Run the verifier from the repository root with an independently prepared plan
and the measurements produced against it.

```sh
just cache-parity-plan-identity
just cache-parity-verify /absolute/plan.json /absolute/evidence.json /absolute/raw-dir > /absolute/parity-summary.json
```

Use the output of `cache-parity-plan-identity` to populate the plan's source
commit, llama.cpp pin, ordered patch queue digest, native ABI, cache format,
model manifest, harness and replay scorer digests. The verifier derives
these values again from the checkout. Strict verification requires a clean
checkout. Put local plans, measurements, and output in an ignored directory
or outside the checkout. For a development worktree, use
`just cache-parity-verify-diagnostic` with the same arguments.

The verifier checks each measurement file against the SHA-256 of the exact
plan bytes. See the [JSON schema](parity.schema.json) and
[model manifest](../../crates/skippy-model-artifact/src/kv_fixtures/models.json).

The plan identifies the candidate source commit, ordered native patch queue,
cache format, harness, replay scorer and AgentX corpus,
hardware, model and template bytes, sidecars, workload, and both engine
configurations. Retain the hashed inputs with the measurements. The host
profile should identify the physical machine, compiler, OS and toolkit
versions, driver, accelerator, power settings, and memory configuration.
The Phase 1 fixture manifest declares an empty required sidecar list. Set
`sidecars_sha256` to the SHA-256 of the bytes `[]`.

## Paired streaming diagnostics

Use the paired capture to validate the benchmark inputs against both engines
on one machine. The runner starts one engine at a time on an ephemeral
loopback port, waits for model readiness, and sends the exact same request
bytes to each engine. On even-numbered pairs it runs llama.cpp first. The
request must enable `stream` and `stream_options.include_usage`.

```sh
just cache-parity-capture-pairs ./engines.json ./request.json ./fresh-output
```

The engine configuration supplies `pairs` from 1 to 20 and separate `skippy`
and `llama_server` objects with an absolute `binary` path and `args` array.
Omit listen-address arguments. The runner sets them for both engines and
retains the configuration, binary hashes, server logs and request and response
bytes. Each observation includes client first and last generated event times,
token usage, and server timings when provided. These diagnostic captures
retain both original and normalized completion counts. Include `"verbose": true`
in the request to capture llama.cpp's native stop type. Skippy excludes a
sampled terminal end-of-generation token from its usage. The parity runner
subtracts one from llama.cpp's count only for a confirmed native `eos` stop.
Explicit stop words and length limits keep their reported counts. An ambiguous
llama.cpp stop leaves the normalized count empty.
An optional `seed_request` path sends a shared request to each fresh server
before the measured request and retains that response in a separate `seed`
directory. For L1 diagnostics, use a single llama.cpp slot with RAM and disk
caching disabled and check the positive cached-token count. Skippy includes
`cache_source` in the timings when it observes a local cache restore.

Set `"timings_per_token": true` to collect llama.cpp's sampling timings.
The `sampled_phases` record measures model-service TTFT and the interval from
the first counted sample to the last, with the terminal token excluded. Skippy
measures those boundaries at its token collector. On llama.cpp, the runner
selects the native timing records corresponding to the normalized token
count. The record is empty when it lacks one of the timing boundaries.
The measurement protocol below specifies the additional data needed for
certification.

Set `restart_from_disk` to `true` with a `seed_request` to capture a
post-restart L3 pair. The runner creates a private cache directory per leg.
It waits for Skippy to publish a manifest, stops the publisher, and checks
the committed checkpoint with the offline operator status before starting the
replay process. For llama.cpp,
it saves slot zero, stops the publisher, restores the slot on a fresh process,
and checks the save and restore receipts. Both replays must report reused
tokens, and Skippy must name L3 as the source. The request must enable
`cache_prompt` for the baseline. Omit cache mode, L2 budget, L3 budget, and
L3 directory flags from Skippy's arguments, and omit slot-save path, parallel
count, and RAM cache flags from llama.cpp's arguments. The runner sets those
options for this case.

Each leg retains the publisher and replay process logs and an overview in
`restart.json`. The baseline overview adds explicit slot restore time to
its streamed TTFT using client and native timings respectively. Skippy's
TTFT already includes its automatic restore.

For an L2 pair, set `l2_eviction_request` and `seed_request` to different
streaming requests. Use a disjoint prefix for the eviction request and enable
`cache_prompt` for eviction and replay. This mode uses the same settings
exclusions as the disk restart mode and also sets Skippy's L1 entry limit.
The runner waits for Skippy's durable prefixes, replaces the L1 entry, then
exercises replay until the production route reports `l2`. It verifies that
the store-wide disk-payload read counter stays constant between the final
eviction response and measured replay. llama.cpp runs one slot and must log
a successful host prompt-cache restore. The runner retains both observations
in `host-restore.json` and keeps the preparation requests for review.
The llama.cpp summary adds its native cache update time to its sampled TTFT.
That update covers saving the displaced slot and restoring the selected RAM
entry. The client timing already includes the update.

Set `memory_sampling` in both engine objects to `host`, `cuda`, or `metal` to record
`memory.json` alongside each measured capture. Both legs use the same
profile, with resident process memory sampled at the edges of the request and
at 250 ms intervals during it. For disk restart, the measurement window
includes llama.cpp's explicit restore call. The Linux `cuda` profile sums
reported device memory for the server PID across visible GPUs. The `host`
profile records resident process bytes and leaves device bytes empty.
[NVIDIA SMI process query](https://docs.nvidia.com/deploy/nvidia-smi/#selective-query-options).

Build the Metal sampler on macOS arm64 and set `metal_memory_probe` in both
engine objects to the absolute path of the built library.

```sh
just cache-parity-build-metal-probe /absolute/metal-memory-probe.dylib
```

The runner loads the same library into each benchmark process. Each sample
records the Metal resource allocations reported inside that process and the
process's resident memory as two independent metrics. The values can overlap
on Apple silicon's unified memory. [Metal resource allocation counter](https://developer.apple.com/documentation/metal/mtldevice/currentallocatedsize)
and [unified memory](https://developer.apple.com/documentation/metal/mtldevice/hasunifiedmemory).

For an already-running local engine, use
`just cache-parity-capture-request 127.0.0.1:9337 ./request.json ./fresh-output`.

### Multi-request workload traces

```sh
just cache-parity-capture-trace ./engines.json ./workload.json ./fresh-output
```

The [workload manifest](workload.schema.json) lists ordered bursts of up to
32 concurrent requests, with a maximum of 200 requests per leg. Each entry
names a request file relative to the manifest, pins its SHA-256, and may
specify the exact expected response content. Request files must enable
streaming and usage.

Generate a frozen fixture with 200 requests using the alias served by both
engines, the cache state, the workload shape, and the burst width.

```sh
just cache-parity-make-workload kv-parity warm_l1 divergent_tail 4 ./fixture
just cache-parity-capture-trace ./engines.json ./fixture/workload.json ./fresh-output
```

The generator supports `cold_prefill`, `warm_l1`, `l2_restore`, and
`post_restart_l3`, with `exact_replay`, `divergent_tail`, and
`growing_conversation`. It writes content-addressed inputs and exact expected
responses for deterministic synthetic code reviews. The divergent variant
changes the final instruction after a common source prefix. The growing
variant accumulates simulated CI results and retains the previous turn in the
seed. Use the generated manifest digest in the selected plan cell.

For cold and resident L1 traces, use the same engine configuration as for
paired capture, optionally with a seed request for L1. Each engine starts
once per leg. The next burst starts
after all requests in the current burst finish. Sampling and wall timing
cover the measured requests. The runner writes per-request bytes, usage,
timings, and output comparisons after the measurement window. Each leg keeps
a `trace.json` with its scheduled order and request start and finish offsets.
The output retains the frozen workload manifest and the original request
bytes under `inputs/<sha256>.json` even if a leg cannot start.

For per-slot resident L1, use a pinned `seed` for each entry and omit the
global preparation settings. The harness seeds each slot before its burst
and measures the overlapping replays on the same process throughout the leg.
Omit the cache flags and baseline parallel count controlled by the harness.

For independent L2 or L3 replays, use one request per burst and the
`l2_eviction_request` or `restart_from_disk` setting described above. Each
request gets fresh cache state and its own engine lifecycle. Skippy primes
L2 before the timed request, then performs one more eviction to expose the
host-RAM restore. The L3 publisher exits before the replay process can open
its root. Each request retains the validated tier, timings, and setup
receipts. The runner removes its temporary per-request cache state after
capturing that evidence. These diagnostics report individual restore windows.

For a post-restart burst trace, set `restart_from_disk` and omit the global
`seed_request`. Each entry needs a `seed` object with its own request file and
SHA-256. Give each seed and replay the same zero-based `id_slot` within its
burst, and enable `cache_prompt` on both. Use distinct histories within the
burst so a request does not pick up another request's resident state. Both
engines receive the same pinned request bytes.

Each burst uses a private cache root and one replacement process. The
publisher commits each Skippy checkpoint or saves each llama.cpp slot before
exiting. The replacement runs the measured requests concurrently and charges
llama.cpp's explicit slot restores to their corresponding requests. Retained
receipts identify both processes, the saved and restored slots, and the
actual cache source.

For an L2 burst trace, give every entry both a `seed` and an `eviction`
object. The seed, eviction, and replay requests use the same slot and set
`cache_prompt`. The eviction has a distinct history. Leave the global
`seed_request`, `l2_eviction_request`, and `restart_from_disk` settings unset.
Each burst uses one server process with a 2 GiB RAM cache budget. On Skippy,
the runner conditions each prefix in RAM and checks that the disk payload
read counter stays constant across all measured replays. On llama.cpp,
the runner holds each slot through preparation and checks the native
RAM-restore log for every measured request. The measured inference latency
includes llama.cpp's cache update time for that request.

### Export raw trials from a trace

```sh
just cache-parity-export-trace ./plan.json ./cell.json ./capture ./raw > ./measured-cell.json
```

Use the selected plan cell's `cell` object as `cell.json`. For exportable
captures, add `host_profile` to the engine configuration. It names the
pinned host-profile JSON file with `os` and `arch` fields. Both servers must
load the same direct local model with its embedded chat template. The runner
hashes the model and executables before and after the capture and retains
the host profile. Both engine configuration digests in the planned cell
must match the SHA-256 of the complete retained `configuration.json`.
For Metal, set `metal_memory_probe_sha256` in the host profile to the SHA-256
of the built library. Capture and export check that the measured source
matches this digest.

Export supports cold, resident L1, L2, and post-restart L3 traces with 200
requests per engine and measured device memory on CUDA or Metal. For
L2 and L3 burst traces the exporter uses the sum of the replay windows and
the peak samples across measured processes. Host-only traces remain diagnostics.
A completed request requires an exact text oracle in the frozen workload.
Set `prompt_tokens` independently in the workload when a failed request
should count without a returned usage record.

The exporter reparses each saved SSE transcript and recomputes output,
cache-source, token, and native timing observations. The client timestamps
must fit the per-request measurement windows. The prefill rate uses
model-service time through the first sampled token and counts prompt
tokens that the cache did not supply. The decode rate uses the time from
the first counted sample through the last. Raw trial files are named by
digest. A measured cell with fewer than 20 pairs is partial evidence.

## Measurement protocol

Collect all 96 cells on macOS arm64 Metal and Linux x86-64 CUDA. For each of the
four model families, run exact replay, divergent tails, and a growing agentic
conversation in each of the four cache states. Give each workload an immutable
trace that defines the request sequence, prefix length, concurrency, output
checks, and how the same cache state is prepared and observed for each engine.
Finish cache setup before timing. Start the clock before the measured request's
restore work, including any explicit restore call. For post-restart restore,
finish cache publication and stop the owner before launching the new process.
Begin timing after the new server is ready. An observation counts only when it
confirms the planned cache path for that engine and request.

The pinned llama.cpp server exposes cold completions with prompt reuse disabled,
resident reuse from a seeded slot, host prompt-cache reuse via `--cache-ram`,
and disk persistence through slot save and restore. Record a cache-path
observation in addition to the reused-token count. For the host-cache case,
evict the seeded resident state before replay and confirm the host-cache load.
For the disk case, record the successful restore on the new process and the
reused tokens on replay. Confirm the corresponding Skippy tier for each
request.

Collect 20 consecutive matched pairs per cell on the same otherwise idle
machine, with 200 measured requests per engine per pair. Start with Skippy in
pair one and alternate order on each subsequent pair. Keep a distinct raw JSON
record for each trial, with the ordered request digests, request results,
cache observations, timings, and host and device memory samples. Record
memory at both ends of the measured interval. Use the same memory counters
and sampling schedule for both engines. See the [raw trial schema](raw-trial.schema.json).

Generate the JSON trial entry from the raw record and retain that exact file
as `RAW_DIR/<raw_artifact_sha256>.json`.

```sh
just cache-parity-summarize-trial /absolute/raw-trial.json > trial-entry.json
```

For the local replay score, divide all prompt and output tokens from completed
requests by the measured wall time. This follows AIPerf's total token
throughput formula. Set the plan's `agentx_scorer_sha256` to the SHA-256 of
`tools/xtask/src/cache_parity/raw/measurements.rs`. Compute prefill rate from
new prompt tokens and measured prefill durations, and decode rate from the
tokens and duration after each request's first token. Compute each TTFT percentile with the
nearest rank over completed requests. Retain output checks and failures
separately. [AIPerf 0.13.0 metrics reference](https://docs.nvidia.com/aiperf/v0.13.0/reference/ai-perf-metrics-reference#total-token-throughput)

## Decision

For each pair, the candidate needs at least 98% of the baseline's replay score
and throughput, and at most 102% of its TTFT and peak memory. The bounds are
inclusive. A metric passes a cell when at least 15 of 20 pairs meet the bound.
Under independent pairs with a boundary success probability of one half, an
exact one-sided binomial tail at 15 successes is about 2.1%. Five or fewer
successes indicate regression under the same rule. Intermediate results are
inconclusive. An increase in failed requests or incorrect outputs in any pair
is a regression. Every metric in every cell must pass.

The command emits the computed per-cell decisions and exits nonzero for a
regression, an inconclusive result, or invalid evidence. The inputs have no
decision field. The verifier reopens each retained raw record, checks its
digest and trial identity, validates matched request sequences within pairs,
and recomputes the trial metrics. The local harness captures state-specific
workloads, phase timings, and memory samples in this raw record format.
Official AgentX exports are still required. Release integration still
needs to bind candidate executable and runtime bytes to the verified plan
and use the admitted evidence during publication.

### Verify cache qualification inputs together

```sh
just cache-parity-verify-inputs ./plan.json ./evidence.json ./raw ./metal-correctness ./cuda-correctness ./verified-inputs.json
```

Run from the exact clean source checkout used by the plan. The command
recomputes the complete parity matrix, then replays the retained correctness
and cache reports for all four fixtures on both backends. Use the same
llama.cpp executable per backend for correctness and parity. The receipt
records the identity of that reference, the parity candidate, and the
correctness test executable. It also pins the matched reports, fixture
manifest, source, and cache format. Release admission still requires official
AgentX evidence and a binding from the release binaries and native runtime
artifacts to the verified inputs.
