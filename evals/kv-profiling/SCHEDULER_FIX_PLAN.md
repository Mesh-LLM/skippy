# Scheduler performance qualification

The [merged-source performance protocol](SCHEDULER_PERFORMANCE_PROTOCOL_V3.json) compares
the Skippy product built from `9707dba3` with both pinned vanilla llama.cpp layouts at four
model/load points. It requires complete, matched 512-input/32-output requests,
full Metal, F16 KV, 16 lanes with 8192 context each, batch/microbatch 512,
caches off, nominal thermals, and eight balanced fresh-process rounds per model.
The exact four CI model pins and the authorized $0 `macstudio-2l0352` host apply.

The relative claim passes only if the paired 95% interval exceeds 0.98 for
output tokens/s and stays below 1.02 for p95 TTFT, E2E, and TPOT against
**each** vanilla layout. Absolute latency SLO and output differences are
reported separately. The historical failures remain in the
[hash-indexed archive](pr-evidence/scheduler-pr233-history-index.json).

The active JY job IDs, immutable spec hashes, artifact hashes and occupancy
are kept in the ignored local campaign ledger at `target/scheduler-fixes/campaign.json`.
This local path is not a link to a file in the reviewed PR. The PR description
and [status](SCHEDULER_FIX_PROGRESS.md) summarize completed evidence.

The [original protocol](SCHEDULER_PERFORMANCE_PROTOCOL.json) names the source
used for its first timing jobs. Review cleanup at `478f44ed` removed the
resident-prefix policy and changed Rust inputs; the Q4_K test repair at
`79523f8b` changed a native patch. The [version 2 registration](https://github.com/Mesh-LLM/skippy/blob/dcdbe8d4e9598ba53dd07ec10375dafd0706a6b8/evals/kv-profiling/SCHEDULER_PERFORMANCE_PROTOCOL_V2.json)
was superseded before timing when current `main` added a required split-peer
CI contract and a native patch. Its product build was cancelled. Version 3
pins the merged source and retains the same decision thresholds. It has no
timing result yet.

Do not submit duplicate jobs, substitute models or native inputs, or edit
runtime code without a measured mechanism. Builds use Just and required Rust
checks run serially.
