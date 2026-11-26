# Qualification-model scheduler and active-KV comparison

Campaign closed at bounded qualification blockers, 2026-11-24 17:33 UTC.
No overall speedup or full model-quality qualification is established here.
All registered jobs are terminal; no eligible dependent timing stages remain
under the registered correctness and completion gates. Completed node occupancy
is 14.936 hours, including failed attempts and diagnostics, with all job budget
caps at $0. Per-run null billing fields remain null in the ledger.

The target is `macstudio-2l0352`, Apple M1 Ultra, 128 GiB unified memory, Metal.
Models use the exact qualification artifact pins. Prefix and disk caching are
off; active KV remains enabled. Four configurations are compared: pinned Skippy
base, patched Skippy, vanilla separate-slot KV, and vanilla unified KV.
Source, binary, patch, job/spec identities and occupancy are recorded in the
[campaign ledger](../../target/scheduler-four-models/campaign.json). The
[registered protocol](SCHEDULER_FOUR_MODEL_PLAN.md) records settings and amendments.

## Current coverage

| Model | Completed evidence | Remaining work or limit |
|---|---|---|
| Qwen3.5-4B | Four screen rounds; c64; c128; serial controls; four timeline captures | c128 has nine pairwise output mismatches against vanilla. Patch/base match. Streaming controls and trace analysis audited; loaded disagreement remains; c256 blocked. |
| Qwen3-Coder-30B-A3B | Four screen rounds; c64/c128; c256 failure audit; four timeline captures | c256 hits Skippy's admission limit. Private trace analysis and completed-run statistics audited. |
| Qwen3.6-35B-A3B | Screen round 0 and 192 serial/repeat/logprob controls | Repeatable output disagreement with vanilla; further timing held. Responsible operator unresolved. |
| Qwen3.8-Flash-Next | Native admission, screen round 0 and 64 repeat/logprob controls audited | Reproducible serial disagreement plus mixed/serial dependence; four screen mismatches remain. Further timing blocked. |

## Completed-run performance and limits

The [four-round tables](SCHEDULER_FOUR_MODEL_STATISTICS.md) cover 96 cells:
single-user, c64 and c128 for Qwen3.5 and Coder. Each reports TTFT, E2E, TPOT,
output throughput, actual client in-flight counts, and client lag. The
[machine-readable statistics](../../target/scheduler-four-models/statistics.json)
retain per-round values, evidence hashes, all four engine pairings and exact
paired bootstrap intervals. Four rounds offer limited uncertainty coverage.

No patched/base metric in those inferencekit workloads meets both the registered
2% effect threshold and a paired 95% interval excluding parity. Every c64/c128
cell exceeds the 30-second p95 TTFT target. Completion and output-agreement
checks therefore must not be described as a latency-SLO pass. Peak client
in-flight counts are exactly 64/128, including waiting HTTP requests; maximum
load-client send lag is 3.42 ms. Single-user closed-loop send lag is unavailable.

The separate unprofiled mixed-c4 diagnostic supports a narrower scheduling
benefit: patched/base late-short TTFT ratios are 0.195 (95% interval
0.148–0.198) for Qwen3.5 and 0.178 (0.139–0.308) for Coder. There are tradeoffs:
long-2 E2E increases 2.53% and 2.37%, respectively; Coder's anchor maximum token
gap increases 6.49% (ratio interval 1.035–1.075). Full-wave throughput changes
are below 2%. These use the custom diagnostic client and observed arrival
timing, not inferencekit or a production workload. Output screens do not prove
full quality, and Qwen3.5's separate loaded disagreements remain unresolved.

## Coder c256: admission capacity failure

Each entry below is repeated identically in all four fresh-process rounds.
The workload offers a seeded Poisson burst capped at 256 requests, each with
512 content input tokens (native accounting verified) and 32 requested output
tokens. It uses the frozen 749.4787085 ms baseline reference, 16 active lanes,
and equal 120-second nominal cooldowns. The c256 label describes offered load,
not measured simultaneous active sequences or production capacity.

| Configuration | Attempts per round | Completed | Queue rejected | Completion rate |
|---|---:|---:|---:|---:|
| Vanilla separate-slot KV | 256 | 256 | 0 | 100% |
| Vanilla unified KV | 256 | 256 | 0 | 100% |
| Skippy unpatched | 256 | 144 | 112 | 56.25% |
| Skippy patched | 256 | 144 | 112 | 56.25% |

**Both Skippy builds fail the zero-error completion gate at this offered load.**
The pinned default waiting queue is `16 × 8 = 128`; the observed 144 admissions
agree with 16 active plus 128 waiting. No queue override was supplied. The patch
does not remove this admission limit. Vanilla completes more work under these
registered settings; successful-only latency cannot establish a Skippy speedup.

The [strict audit failed](../../target/scheduler-four-models/artifacts/coder30b-c256/qualification-failure.json).
The separate [failure diagnostic](../../target/scheduler-four-models/artifacts/coder30b-c256/failure-audit.json)
verifies all 4,096 attempts: 3,200 complete and 896 receive the queue-full HTTP
429 response. Native settings, fixture identities, successful token counts,
zero cache counts and nominal boundaries pass. All 1,728 shared-success output
comparisons match; rejected requests have no output equivalence result.
Successful same-arm fresh-process repeats also match. This does not establish
full quality qualification or clear earlier mismatches in other workloads.

## Unresolved correctness and attribution

Qwen3.5's [c128 audit](../../target/scheduler-four-models/artifacts/qwen35-c128-retry/audit.json)
records the nine discrepancies. Both sets of 96 serial controls agree across engines and between streaming
and non-streaming transports. All48 saved loaded request bodies match the serial
streaming bodies, yet33 loaded-vs-serial text differences remain. Transport alone
does not explain this; concurrency, batching and request history remain coupled.

Qwen3.6's [control audit](../../target/scheduler-four-models/artifacts/qwen36-correctness/audit.json)
finds exact repeats within each engine, with serial disagreement on five of six
prompts. One prompt differs at the initial prediction with a vanilla preference
gap of 3.398212388 nats. Another changes text between ordinary and logprob paths
in both Skippy builds. These findings do not identify the responsible operator.

[Qwen3.5](../../target/scheduler-four-models/artifacts/qwen35-timeline/audit.json)
and [Coder](../../target/scheduler-four-models/artifacts/coder30b-timeline/audit.json)
timeline metadata checks pass. Private analyses verify all32 request windows
across8 captures, target-PID CPU/GPU rows and no unattributed rows. Profiled
timings are diagnostic.

Historical Thoughtworks indices 16/227 and the earlier hybrid-MoE discrepancy
remain unresolved; this campaign has not directly replayed those cases.
Flash's remaining timing stages are held on output agreement. No L2/L3 retention work is included.

Flash's [round-0 audit](../../target/scheduler-four-models/artifacts/flash-screen-r0/audit.json)
verifies four completed cells: 16 inferencekit requests and 28 diagnostic wave
responses, zero cache counts, native 49/49-layer offload and nominal boundaries.
Model, baseline and candidate identities exactly match its audited native admission.
There are four pairwise text mismatches: `load-0001` in inferencekit and `short-9`
in the mixed wave each differ from both vanilla layouts. Base and patched Skippy
match on all saved responses; all four same-process short-repeat controls match.
This is a failure of exact agreement, not proof of the responsible operator.
Job `job_zgqevpjrcz2z8f2` replayed those two prompts serially in two fresh processes
per arm, with normal streaming repeats and non-streaming top-five logprob repeats.
All [64 responses were audited](../../target/scheduler-four-models/artifacts/flash-correctness/audit.json).
Same-process and fresh-process text/logprob repeats are exact within each arm;
normal streaming and logprob text also match. `load-0001` still differs from both
vanilla layouts, first at reported token index 8: vanilla chooses “ each” while
Skippy chooses two newlines, with a vanilla preference gap of 0.089606285 nats.
This does not identify the responsible operator or justify a relaxed tolerance.

`short-9` agrees across all arms when serial. The
[screen-to-control audit](../../target/scheduler-four-models/artifacts/flash-correctness/screen-comparison.json)
verifies immutable identities, launch settings, native settings, KV allocations,
fixture hashes and saved inferencekit request bodies. All 16 text differences
from same-arm screen results are vanilla `short-9` mixed versus serial responses;
Skippy retains its serial text. Batching, concurrency and history are not isolated.
All four original screen discrepancies remain unresolved. Rounds 1–3 and load
tests remain blocked; one screen round cannot support a performance claim.

Flash's [native admission audit](../../target/scheduler-four-models/artifacts/flash-native-reserve-accounting/audit.json)
verifies 49/49-layer Metal offload and matching 32-token probes in all four arms.
Logged headroom is 4.254 GiB for each Skippy build, 13.854 GiB for vanilla slots,
and 9.767 GiB for vanilla unified. All request 16 active lanes, but native
`n_seq_max` resolves to 32 for Skippy and 16 for vanilla. This internal allocation
difference remains explicit; the probe does not prove concurrent peak memory.

## Diagnostic scheduler mechanism

One private mixed-c4 capture per configuration; these are profiled observations,
not the unprofiled performance comparison. GPU busy means the target process's
command/encoder interval union during the late request's wait, including work
for other requests.

| Model | Configuration | Late short TTFT | GPU busy during wait | First linked work after arrival |
|---|---|---:|---:|---:|
| Qwen3.5-4B | Skippy unpatched | 7.571 s | 97.91% | 7.239 s |
| Qwen3.5-4B | Skippy patched | 1.551 s | 98.17% | 1.218 s |
| Qwen3.5-4B | Vanilla slots | 7.675 s | 98.36% | Not instrumented |
| Qwen3.5-4B | Vanilla unified | 7.822 s | 98.41% | Not instrumented |
| Coder-30B-A3B | Skippy unpatched | 8.179 s | 97.49% | 7.820 s |
| Coder-30B-A3B | Skippy patched | 1.436 s | 97.47% | 1.026 s |
| Coder-30B-A3B | Vanilla slots | 8.206 s | 98.61% | Not instrumented |
| Coder-30B-A3B | Vanilla unified | 8.070 s | 98.73% | Not instrumented |

The patch starts the late request's linked work earlier while the GPU remains
busy. This supports scheduling order as the mechanism for its reduced wait.
Aggregate recorded scheduler overhead is7.6–17.5ms per roughly10-second Skippy
wave. However,496–640ms maximum token-event gaps remain on the anchor request,
with475–616ms of Metal activity inside those gaps. These captures do not identify
shader-level attention/KV cost or establish that all scheduler issues are fixed.

Evidence: [Qwen3.5 trace audit](../../target/scheduler-four-models/artifacts/qwen35-timeline-analysis/audit.json),
[Coder trace audit](../../target/scheduler-four-models/artifacts/coder30b-timeline-analysis/audit.json),
and [transport controls](../../target/scheduler-four-models/artifacts/qwen35-c128-stream-controls/transport-vs-loaded.json).
