# Scheduler PR evidence snapshot

This directory contains compact audited results for
the four exact CI qualification models. [The manifest](manifest.json) gives
the SHA256 of every snapshot and its source, the SHA256 of each full local suite, and the JY
job/spec identities. [Per-cell metrics](cell-metrics.json) preserve the source
measurement evidence IDs, independent audit reductions used by the displayed
tables, and the original inferencekit metrics. The capacity records have their
own derived evidence IDs; their p95 percentile convention can differ slightly
from the independent audit's linear percentile. The
[request timing samples](request-timings.csv) contain only timing, token counts
and monotonic timestamps for all 6,176 timed requests; their source JSONL
hashes are in the manifest. The Qwen3.6 combined-fix audit is a compact
projection whose full source hash is recorded. Full response traces
and logs remain in the immutable JY run artifacts identified by the manifest;
they are intentionally not duplicated here.
The Qwen3.5 statistics Markdown snapshot differs from its source by one
trailing space removed for repository whitespace checks; the manifest records
both digests and the one-byte projection.

The [table audit](table-audit.json) independently recomputes every displayed
cell of the [four-table PR draft](../SCHEDULER_PR_DESCRIPTION_DRAFT.md) from
the timing samples. It checks arithmetic and request counts; correctness and
relative non-regression have separate gates.

The [source attestation](source-attestation.json) confirms that every changed
Rust crate file in this worktree exactly matches the isolated patch used for
the tested combined server binary. It also binds the native Q4 Metal patch in
the PR source tree to the patch used in the component rebuild. It also verifies
the packaged native recipe against the ordered patch queue. The PR's Rust
files and Q4 patch bytes match the earlier tested patch, while current main
now uses a newer llama.cpp pin; the attestation explicitly marks that port as
awaiting hardware qualification. It does not establish a four-model
correctness or performance pass.

| Model | Review files | Scope |
|---|---|---|
| Qwen3.5-4B | [c128 statistics](qwen35-c128-statistics.md), [audit](qwen35-c128-audit.json), [fixed serial](qwen35-fixed-serial-audit.json), [vanilla concurrency](qwen35-vanilla-concurrency-audit.json) | Four-round fixed-Metal c128 retest; 12 cross-engine exact-output mismatches and all TTFT SLO cells fail. |
| Qwen3-Coder-30B-A3B | [c256 report](coder-c256-report.md), [audit](coder-c256-audit.json), [current serial screen](coder-current-candidate-audit.json), [four-round balanced screen](coder-current-balanced-audit.json), [historical single-user statistics](coder-single-historical.json) | Sealed pre-port candidate passes 192 exact-output requests; its paired E2E upper bound against vanilla separate slots exceeds the 2% margin. The explicit queue256 c256 retest used original source and misses the TTFT SLO. |
| Qwen3.6-35B-A3B | [original round-0 audit](qwen36-original-r0-audit.json), [combined serial-fix audit](qwen36-combined-fix-audit.json), [current candidate audit](qwen36-current-candidate-audit.json), [component split](qwen36-composition-audit.json) | Original one-round timing only; fixed Metal changes the selected Skippy text, and neither Skippy variant matches vanilla. Balanced timing is blocked. |
| Qwen3.8-Flash-Next | [original round-0 audit](flash-original-r0-audit.json), [history audit](flash-history-audit.json), [current candidate audit](flash-current-candidate-audit.json) | Original one-round timing only; current candidate fails selected serial exact-output checks, so balanced timing is blocked. |

These records support the tables as **observations**, not a four-model
non-regression verdict. Qwen3.5, current Qwen3.6, and current Flash exact
cross-engine output fail. The Qwen3.5 c128 and Coder c256 TTFT p95
values exceed the 30-second capacity target in every timed arm. The
[original rule](../SCHEDULER_NONREGRESSION_PROTOCOL.md) requires paired 95%
non-inferiority bounds versus both vanilla layouts, exact-output and hardware
gates, and balanced current-candidate rounds before a positive claim.
A [separate task-quality rule](../SCHEDULER_TASK_QUALITY_PROTOCOL.md) is now
registered for new runs; it does not change these historical verdicts.
