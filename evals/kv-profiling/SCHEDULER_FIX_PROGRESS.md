# Scheduler fix and performance status

PR [#233](https://github.com/Mesh-LLM/skippy/pull/233) contains measured fixes
for scheduler admission, cache-off logprob prefill chunks, and Q4_K Metal
scale arithmetic. Serial Rust checks and the native Metal replay passed.
The [PR description](https://github.com/Mesh-LLM/skippy/pull/233) carries the
four historical model tables with their original failures and limits.

The [registered relative-performance protocol](SCHEDULER_PERFORMANCE_PROTOCOL.json)
is performance-only. Current-source Qwen3.6 c1 completed eight balanced rounds:
seven of eight metric/layout gates passed, while the p95 TPOT upper bound
against vanilla separate slots was 1.0420, above the registered 1.02 bound.
That comparison and the four-model no-regression claim remain **UNPROVEN**.
The Qwen3.5 c128 first half failed because the local runner read a capacity
summary as if it contained raw request rows; the measured child evidence had
128 complete requests and was retained. A corrected sealed replacement was
submitted. Coder c256 and Flash c1 current-source tables are pending.

The 30-second TTFT SLO failed in every arm of the historical Qwen3.5 c128 and
Coder c256 runs. Output differences and earlier task-quality failures remain
recorded in the [history archive](pr-evidence/scheduler-pr233-history-index.json).
This PR makes no task-quality equivalence claim. Historical Thoughtworks
indices 16/227 and the older hybrid-MoE discrepancy remain unresolved.

The live JY campaign ledger is an ignored local file, not part of the PR.
Its job IDs and hashes are authoritative for continuing the campaign; this
tracked page is a dated status snapshot, not an admission or completion receipt.
