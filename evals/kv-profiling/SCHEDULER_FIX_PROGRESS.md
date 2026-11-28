# Scheduler fix and performance status

PR [#233](https://github.com/Mesh-LLM/skippy/pull/233) contains measured fixes
for scheduler admission, cache-off logprob prefill chunks, and Q4_K Metal
scale arithmetic. Serial Rust checks and the earlier native Metal replay passed.
The [PR description](https://github.com/Mesh-LLM/skippy/pull/233) carries the
four historical model tables with their original failures and limits.

Review follow-through split the separate resident-prefix eviction policy out
of this PR at `478f44ed`. Its four files match `main`; focused Just format,
check and warning-denying Clippy passed, as did 1,353 serial cache and serving
tests (32 ignored). The removed policy patch remains in a local backup. The
related Q4_0/Q4_1 Metal arithmetic observation is tracked in
[issue #257](https://github.com/Mesh-LLM/skippy/issues/257) for a separate
measured test. The Q4_K test's half-subnormal input was replaced with a
small normal half input for portability. The `79523f8b` full Metal replay
passed: 340 executed tests, one registered skip, zero failures. All inline
review threads are resolved.

The [first relative-performance protocol](SCHEDULER_PERFORMANCE_PROTOCOL.json)
is performance-only. Its Qwen3.6 c1 run completed eight balanced rounds:
seven of eight metric/layout gates passed, while the p95 TPOT upper bound
against vanilla separate slots was 1.0420, above the registered 1.02 bound.
That comparison and the four-model no-regression claim remain **UNPROVEN**.
The Qwen3.5 c128 first half failed because the local runner read a capacity
summary as if it contained raw request rows; the measured child evidence had
128 complete requests and was retained. Its corrected sealed replacement
passed a 16-cell, 2,048-request independent audit on the previous product
source. The cache-policy split changed Rust
inputs, so those Qwen3.6 and Qwen3.5 runs are diagnostic for the present PR
head. Coder c256 and Flash c1 merged-source tables are pending. The version 2
protocol registered `79523f8b`, but its build was cancelled before timing:
current `main` added a required split-peer CI receipt and a new native patch.
The [version 3 protocol](SCHEDULER_PERFORMANCE_PROTOCOL_V3.json) pins the
merged `9707dba3` source with the same margins. No version 3 timing or positive
four-model claim has been completed.

The 30-second TTFT SLO failed in every arm of the historical Qwen3.5 c128 and
Coder c256 runs. Output differences and earlier task-quality failures remain
recorded in the [history archive](pr-evidence/scheduler-pr233-history-index.json).
This PR makes no task-quality equivalence claim. Historical Thoughtworks
indices 16/227 and the older hybrid-MoE discrepancy remain unresolved.

The live JY campaign ledger is an ignored local file, not part of the PR.
Its job IDs and hashes are authoritative for continuing the campaign; this
tracked page is a dated status snapshot, not an admission or completion receipt.
