<!-- Evidence draft for laughing-vulture. Do not publish a four-model
     non-regression claim until the listed gates are satisfied. The linked
     pr-evidence/ snapshots are reviewable after they are included in the PR. -->

Suggested title: Fix scheduler admission, logprob prefill, and Q4 Metal scaling

## Original problem

Scheduler and active-KV qualification exposed a bounded admission failure, a
cache-off logprob prefill difference, and grouped Q4_K Metal arithmetic that
changed Qwen3.5 output under concurrency. The fixes make those registered
cases reproducible and correct, while the remaining model and latency limits
stay visible to reviewers.

## Diagnostics

The [original four-model campaign](SCHEDULER_FOUR_MODELS.md) preserves all
failed gates. The [fix investigation](SCHEDULER_FIX_PROGRESS.md) records the
measured mechanisms, exact model and binary identities, job/spec hashes, and
independent native audits. The original Qwen3.5 c128 and Coder c256 failures
remain separate from their retests.
The [source attestation](pr-evidence/source-attestation.json) binds the Rust
diff and Q4 Metal patch to the isolated artifacts used in the retests.

## Fix

- Bound Qwen3.6 direct-prefill admission by both native and scheduler limits,
  and align cache-off logprob prefill chunks with the ordinary scheduler bound.
- Correct the Q4_K Metal scale arithmetic for grouped lanes, while preserving
  the existing native patch queue and non-Metal runtime bytes.
- Use the existing explicit 256-waiting-request queue option for the Coder c256
  retest. This is a workload configuration; the default queue is unchanged.

## Compatibility

The registered fixes do not substitute model weights, precision, or runtime.
The Q4 Metal patch changes native arithmetic and therefore some Qwen3.5 output
text. Its ordered patch-queue digest is updated in the packaged native recipe.
ABI and wire formats are unchanged. The measured fixes apply only to the
listed models, settings, and requests; other architectures and cache modes are
not qualified by these tests.

## Validation

The final combined source passed `just lint` and serial offline Rust workspace
tests through `just --justfile target/scheduler-fixes/combined-checks.just
workspace-tests` (4,864 passed, 314 ignored, including integration and
documentation tests). The focused changed-crate check used `just --justfile
target/scheduler-fixes/combined-checks.just check`, which runs Cargo format,
check, warning-denying all-target Clippy, and tests with the dynamic native
runtime feature. The isolated native Metal replay used `just native-full-replay
metal` (336 passed, one skipped). The Qwen3.5 serving integration and c128
retest ran on the pinned M1 Ultra and passed their registered native, request,
cache, and thermal audits; the c128 exact cross-engine and absolute TTFT gates
failed as shown below. `python3 -m compileall -q scripts`, the task-quality
scorer/materializer selftests, the four-table recomputation, source
attestation, and `git diff --check` pass. The C++ facade suite remains unrun:
GoogleTest v1.18.0 was not supplied and no replacement was downloaded. The
Coder current-candidate balanced screen is running; Qwen3.6 and Flash
current-candidate balanced load checks have not run because correctness
diagnostics remain unresolved.

All tables are observed client measurements on macstudio-2l0352 (Apple M1
Ultra, Metal), with exact CI model pins, full offload, F16 KV, 16 serving lanes,
8192 tokens per lane, and both caches off. Output tok/s is aggregate output
tokens divided by client span. Four-round values are medians of per-round
metrics, not pooled request percentiles. The registered latency SLO is TTFT
p95 ≤30 s and TPOT p95 ≤1 s. These are distinct model workloads; do not rank
the models against each other.

### Qwen3.5-4B — fixed-Metal c128, four rounds

| Arm | Complete | TTFT p95 | E2E p95 | TPOT p95 | Output tok/s | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|
| Skippy original + fixed Metal | 512/512 | 62.30 s | 66.42 s | 252.0 ms | 60.84 | 0/4 |
| Skippy combined + fixed Metal | 512/512 | 62.24 s | 66.36 s | 251.7 ms | 60.88 | 0/4 |
| Vanilla separate slots | 512/512 | 73.54 s | 77.39 s | 283.8 ms | 52.29 | 0/4 |
| Vanilla unified | 512/512 | 72.57 s | 76.40 s | 281.3 ms | 52.97 | 0/4 |

Evidence: [fixed c128 statistics](pr-evidence/qwen35-c128-statistics.md),
job `job_txcg43nzentnsd4`, spec SHA256
`bbad4abe6a30e1385378458b4ff8bcbec374de4959d9976cdbb00bd105ce9d25`.
The two Skippy arms agree exactly across requests and rounds. Twelve
cross-engine output comparisons still differ, and every arm fails the TTFT
SLO. The apparent cross-engine throughput difference is descriptive, not an
equivalent-output speedup claim. Combined/original Skippy throughput ratio is
1.0008 (paired 95% interval 0.9985–1.0023), below the registered 2% effect.
An [eight-response fixed-Metal serial control](pr-evidence/qwen35-fixed-serial-audit.json)
reproduces both Skippy c128 text groups in fresh processes; the vanilla loaded
outputs still differ from their archived serial controls. The control narrows
the discrepancy but does not clear cross-engine exact output or identify its
native cause.
An [unchanged-vanilla c1/c16 replay](pr-evidence/qwen35-vanilla-concurrency-audit.json)
then completed 136 identical-prompt responses: both vanilla layouts repeated
their serial text 4/4 times, but each produced four distinct c16 texts, none
matching serial. The original c128 text appears among the c16 variants.
Cross-engine exact output remains FAIL; this is a diagnostic of batch
sensitivity, not a quality-policy change or speed claim.

### Qwen3-Coder-30B-A3B — explicit-queue c256, four rounds

| Arm | Complete | TTFT p95 | E2E p95 | TPOT p95 | Output tok/s | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|
| Skippy base, queue 256 | 1,024/1,024 | 129.85 s | 137.13 s | 261.0 ms | 59.22 | 0/4 |
| Skippy patched, queue 256 | 1,024/1,024 | 129.91 s | 137.18 s | 261.1 ms | 59.20 | 0/4 |
| Vanilla separate slots | 1,024/1,024 | 152.15 s | 160.16 s | 304.5 ms | 50.76 | 0/4 |
| Vanilla unified | 1,024/1,024 | 131.28 s | 138.10 s | 259.2 ms | 58.77 | 0/4 |

Evidence: [queue retest report](pr-evidence/coder-c256-report.md),
job `job_t6qt4fsegpdkwte`. All 4,096 requests complete with exact cross-arm
text, but all 16 timed cells fail the TTFT SLO. The queue change fixes this
burst's admission configuration; patched/base throughput is within noise.
Timed Skippy native settings are supported by matched diagnostic preflights,
not by each timed process log.
The [current-candidate selected serial screen](pr-evidence/coder-current-candidate-audit.json)
then passed 64/64 responses and exact text across two prompts, both vanilla
layouts, repeats and logprob modes. Two earlier attempts failed before
generation on harness schema assumptions; their failures remain in the fix
ledger. This selected pass does not replace current four-round timing.
The [historical four-round single-user screen](pr-evidence/coder-single-historical.json)
used the original source, so it cannot qualify the current candidate. Its
patched-Skippy/vanilla-separate-slot TTFT p95 ratio was 1.0157 with paired
95% interval 1.0133–1.0217: the adverse bound crosses the registered 1.02
limit. That comparison is **UNPROVEN** under the prospective non-regression
rule, despite the small point-estimate loss and the successful c256 retest.

### Qwen3.6-35B-A3B — original screen round 0 only

| Arm | Complete | TTFT p95 | E2E p95 | TPOT p95 | Output tok/s |
|---|---:|---:|---:|---:|---:|
| Skippy base | 4/4 | 403.7 ms | 815.4 ms | 13.39 ms | 39.56 |
| Skippy patched | 4/4 | 402.0 ms | 820.1 ms | 13.49 ms | 39.27 |
| Vanilla separate slots | 4/4 | 428.2 ms | 845.9 ms | 13.47 ms | 38.31 |
| Vanilla unified | 4/4 | 429.5 ms | 857.1 ms | 13.89 ms | 37.81 |

Evidence: [original screen audit](pr-evidence/qwen36-original-r0-audit.json),
[combined fix replay](pr-evidence/qwen36-combined-fix-audit.json), and
[current-candidate serial audit](pr-evidence/qwen36-current-candidate-audit.json).
The table predates the admission and logprob fixes and covers only one screen
round with four serial inferencekit requests per arm. The later serial fix
replay passed its registered cases. The current candidate then failed all
eight selected serial cross-engine comparisons (two prompts, two process
orders, two vanilla layouts), despite 64/64 complete requests and repeat
stability. A [four-process component split](pr-evidence/qwen36-composition-audit.json)
then isolated the new text to the fixed Metal bundle for these two prompts:
original server/fixed Metal matches combined server/fixed Metal, while combined
server/original Metal matches original server/original Metal. Both Skippy texts
still differ from vanilla. Concurrent parity remains unresolved; no post-fix
four-round load result exists. TPOT p95 is derived
from the saved per-request records with the report's linear percentile method.

### Qwen3.8-Flash-Next — original screen round 0 only

| Arm | Complete | TTFT p95 | E2E p95 | TPOT p95 | Output tok/s |
|---|---:|---:|---:|---:|---:|
| Skippy base | 4/4 | 1,085.1 ms | 2,000.3 ms | 30.04 ms | 16.11 |
| Skippy patched | 4/4 | 1,083.9 ms | 1,989.9 ms | 29.23 ms | 16.17 |
| Vanilla separate slots | 4/4 | 1,169.7 ms | 2,084.2 ms | 29.64 ms | 15.49 |
| Vanilla unified | 4/4 | 1,171.1 ms | 2,104.2 ms | 30.29 ms | 15.35 |

Evidence: [original screen audit](pr-evidence/flash-original-r0-audit.json),
[80-response correctness replay](pr-evidence/flash-history-audit.json), and
[current-candidate serial audit](pr-evidence/flash-current-candidate-audit.json).
The one-round timing screen is not a post-fix four-round load test. Serial
cross-engine `load-0001` still fails in the current candidate's two fresh
process orders; `short-9` matches serially, while the saved vanilla mixed-
request difference remains unresolved. All 64 selected replay requests
completed under full Metal, but this is a failed exact-output gate rather than
timing clearance.
TPOT p95 is derived from the saved per-request records.

The registered Qwen3.6 serial alignment and Qwen3.5 Q4 arithmetic/one-prompt
integration checks passed. Both Rust workspaces passed 4,864 tests with 314
ignored; the native suite passed 336 tests with one skipped. C++ facade tests
were blocked by missing supplied GoogleTest v1.18.0, with no replacement
downloaded. Historical Thoughtworks indices 16/227 and the older hybrid-MoE
discrepancies were not directly replayed.

**Four-model no-regression claim: UNPROVEN.** A PR must not say that Skippy is
non-regressing against vanilla for all four models from these tables. The owner
has selected a separate [prospective task-quality protocol](SCHEDULER_TASK_QUALITY_PROTOCOL.md)
for a bounded claim; its new task fixture and scorer are sealed, but no model
has been scored under that protocol. The exact-output failures above remain
failures, and Qwen3.6's confident distribution disagreement still needs a
measured explanation or pinned independent reference. Required before a
bounded claim: pass the new task-quality and A/A gates; run matched
current-candidate balanced rounds for Qwen3.6 and Flash; retain Qwen3.5 and Coder
single-user as well as load levels in the scope; pre-register the metrics and
non-inferiority margin, then show its paired confidence bounds for every
comparison. The 30-second TTFT capacity SLO also fails at the displayed c128
and c256 levels and must be reported separately. For example, at Qwen3.5 c128,
Skippy combined has a measured 62.24 s TTFT p95 against 72.57 s for vanilla
unified: a favorable relative latency comparison, yet both exceed 30 s. At
Coder c256, Skippy patched is 129.91 s and vanilla unified is 131.28 s, so
neither meets the same absolute target. Relative non-regression, if eventually
qualified, would say only that Skippy does not materially worsen the matched
vanilla workload; it would not establish that either engine can serve these
loads within the capacity SLO. The [review snapshot](pr-evidence/README.md)
and its source hashes must be included in the PR before publishing this text.

## Acceptance criteria

- [ ] I ran the relevant local checks, or explained why they do not apply.
- [ ] I agree to license my contributions under the Apache License, Version 2.0.
