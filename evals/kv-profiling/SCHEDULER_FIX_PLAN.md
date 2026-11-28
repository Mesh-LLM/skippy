# Scheduler performance qualification

The [registered performance protocol](SCHEDULER_PERFORMANCE_PROTOCOL.json) compares
the current Skippy product with both pinned vanilla llama.cpp layouts at four
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

The registered protocol names the product source used for its first timing
jobs. Review cleanup at `478f44ed` removed the resident-prefix policy from
the PR and changed Rust source inputs. Earlier timing jobs remain diagnostic;
qualifying the resulting PR requires a new sealed source identity and matched
runs with the same decision thresholds.

Do not submit duplicate jobs, substitute models or native inputs, or edit
runtime code without a measured mechanism. Builds use Just and required Rust
checks run serially.
