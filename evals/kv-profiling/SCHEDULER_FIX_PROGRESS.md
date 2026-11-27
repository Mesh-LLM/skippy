# Scheduler fix and qualification status

PR [#233](https://github.com/Mesh-LLM/skippy/pull/233) contains three
qualified fixes: scheduler admission bounds, cache-off logprob prefill chunk
alignment, and Q4_K Metal grouped scale arithmetic. Offline serial Rust
checks and native Metal replay passed. The [PR description](https://github.com/Mesh-LLM/skippy/pull/233)
contains the four historical model tables, with exact failures and absolute
SLO results. Those tables do not establish current-source four-model
non-regression.

| Model | Current prospective task-quality status | Remaining gate |
| --- | --- | --- |
| Qwen3.5-4B | c1 pooled confirmatory PASS: 2 adverse / 554 baseline-correct, 97.5% upper 1.29796%; original 512-task look remains UNPROVEN | loaded quality, fixed-output timing, c128 exact mismatch and TTFT SLO |
| Qwen3-Coder-30B-A3B | c1 PASS against both vanilla layouts: 2 adverse / 314, 95% upper 1.99136%; all three fresh repeats exact | c64/c128/c256 quality and timing; prior c256 TTFT SLO |
| Qwen3.6-35B-A3B | first c1 look UNPROVEN: 3 adverse / 353, upper 2.18175% | reverse repeats, confident numerical divergence, loaded quality/timing |
| Qwen3.8-Flash-Next | first c1 look PASS: 1 adverse / 387, upper 1.21989% | reverse repeats, serial/mixed numerical differences, loaded quality/timing |

All four older exact-output failures remain recorded. The prospective
task-quality result permits bounded answer equivalence; it does not erase
those failures or make a throughput or latency claim. The 30-second TTFT SLO
failed for every arm at the displayed historical Qwen3.5 c128 and Coder c256
loads. The four-model no-regression claim is **UNPROVEN**.

The current [version 3 protocol](SCHEDULER_TASK_QUALITY_V3_PROTOCOL.md),
[source task manifest](scheduler-task-quality-v3.json),
[extension manifest](scheduler-task-quality-v3-extension.json), and
[reviewer evidence index](pr-evidence/README.md) are retained in the PR.
The full 2,087-line historical progress diary and all earlier documents/data
are preserved byte-for-byte in the
[history archive](pr-evidence/scheduler-pr233-history.tar.xz), with individual
SHA256 values in its [index](pr-evidence/scheduler-pr233-history-index.json).
The live [ledger](../../target/scheduler-fixes/campaign.json) contains the
current job order, occupancy and artifact hashes. Do not infer completion
from this snapshot; check the ledger and JY status.
