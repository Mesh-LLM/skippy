# Scheduler qualification: current plan

The [campaign ledger](../../target/scheduler-fixes/campaign.json) is the live job and
artifact record. Its queued job IDs and spec hashes are authoritative; do not
submit duplicates. The full historical plan, failures, run reports and audits
are preserved in the [hash-indexed history archive](pr-evidence/scheduler-pr233-history-index.json).

## Claim gate

Follow the sealed [version 3 protocol](SCHEDULER_TASK_QUALITY_V3_PROTOCOL.md).
Use only the four exact CI model pins, the current source-pinned Skippy bundle
and the two pinned vanilla llama.cpp layouts on `macstudio-2l0352`. Require
full Metal, F16 KV, 16×8192 context, batch/microbatch 512, caches off,
nominal thermals and complete outputs. The $0 host time is authorized. Do not
replace models or native inputs and do not interfere with unrelated jobs.

Advance a model independently only after its preceding quality gate passes.
Coder c1 quality passed; its c64/c128/c256 quality and fixed-output timing
remain to run. Qwen3.6 and Flash reverse-order c1 repeats are presealed and
queued; audit them before any promotion. Qwen3.6's first-look quality bound
crosses 2%, and its confident numerical disagreement remains unresolved.
Preserve the original exact-output failures, Qwen3.5 c128 and Coder c256
absolute TTFT failures, and historical Thoughtworks 16/227 and hybrid-MoE
discrepancies. The four-model claim remains unproven.

Only change Rust or native code after a measured mechanism. Use Just and
required serial checks for such changes. Preserve all job/spec/artifact SHA256
identities, $0 occupancy and original failed runs in the ledger. Update the
draft PR with bounded results, verify links and `git diff --check`, and remove
the heartbeat only on completion or a terminal blocker.
