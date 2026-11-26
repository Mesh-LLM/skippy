# Prospective four-model non-regression qualification

Registered 2026-11-26 UTC, after the closed scheduler/fix campaigns and before
any new timing cells under this protocol. The existing results are diagnostic
baselines and cannot be reclassified as a successful prospective claim. The
[four-model plan](SCHEDULER_FOUR_MODEL_PLAN.md), [fix progress](SCHEDULER_FIX_PROGRESS.md),
and [PR evidence draft](SCHEDULER_PR_DESCRIPTION_DRAFT.md) retain their failures.

## Claim and decision rule

Claim only if every registered model, workload, comparator, correctness check,
and hardware gate passes: “On the listed cache-off Apple M1 Ultra Metal
workloads, the current Skippy candidate has no greater than 2% regression in
aggregate output throughput, p95 first-token latency, p95 complete-request
latency, or p95 inter-token latency versus **either** pinned vanilla layout.”
This is a bounded non-inferiority claim, not a claim that all hardware, models,
loads, quality tasks, or capacity targets pass.

For each model and load level, pair rounds by the registered Williams order.
Let `T = Skippy output tok/s ÷ vanilla output tok/s` and let `L = Skippy p95
latency ÷ vanilla p95 latency` separately for TTFT, E2E, and TPOT. Use the
median of four paired round ratios and the complete 4⁴ empirical paired
bootstrap with a two-sided percentile 95% interval. Require the **lower**
interval bound of `T` to exceed 0.98 and the **upper** interval bound of each
`L` to be below 1.02 for both vanilla layouts. The strict inequalities avoid
declaring an exact boundary a pass. A confidence interval including parity is
compatible with non-inferiority if its adverse bound remains inside the 2%
margin; a point estimate alone is never enough. If an interval crosses the
margin, verdict is UNPROVEN. A complete interval wholly on the regressed side
is FAIL. Do not add rounds selectively after seeing a result; a new protocol
must register any extra rounds before running them.

Exact request/response accounting, zero cache hits, repeat stability, and
cross-arm text agreement remain gates for the current synthetic fixtures.
Any mismatch is FAIL for this exact-output screen and blocks the claim. A
separate task-quality policy may be developed explicitly, but cannot silently
replace these registered checks or turn the failed records into passes.

The absolute capacity SLO remains separate: TTFT p95 ≤30 seconds and TPOT p95
≤1 second with zero failed requests. Report its PASS/FAIL per arm and level;
relative non-inferiority must never be worded as capacity-SLO success. Report
both speed and latency losses, even when the overall claim fails.

## Pinned scope and comparators

- Four PR qualification models only: `qwen35-4b`, `qwen3-coder-30b-a3b`,
  `qwen36-35b-a3b`, and `qwen38-flash-next` at the exact revisions and shard
  hashes in `ci/inference-artifacts.json` (current manifest SHA256
  `c6aac96ddf3c4b57f864d13e752e32a2da7e3b06b62cb2f5098a9d52f0afd6f4`).
  The manifest hash is a checkpoint; each job seals its selected pin and fails
  on any shard or tokenizer drift. No downloads, substitute weights, precision,
  or architecture changes.
- Candidate is the integrated admission/logprob/scheduler Skippy source with
  the qualified Q4_K Metal patch and an immutable binary/runtime manifest
  sealed before the first new timing run. Compare to **both** pinned vanilla
  separate-slot and unified configurations. The prior original Skippy binary
  plus fixed Metal is a diagnostic control, not a substitute candidate.
- Rebuilding vanilla with the fixed Metal component may be a useful *separate*
  correctness diagnostic. It is not silently substituted for the pinned
  vanilla comparators. If adopted as the reference, register a new protocol
  version and rerun all paired cells under that identity.
- Apple M1 Ultra `macstudio-2l0352`, full Metal offload, 16 serving lanes ×
  8192 context tokens, F16 K/V, flash attention, logical/microbatch 512, 16
  CPU threads, no prefix or disk caching, and no unregistered CPU offload.
  Verify native settings and memory headroom after real model load/generation
  before a timed full-model cell. If a level cannot fit, record its exact
  capacity blocker; do not silently reduce lanes, context, precision or model.

## Workload and sequence

1. Complete focused correctness diagnostics for Qwen3.5 `load-0100` and
   `load-0190`, Qwen3.6's cross-engine serial/mode cases, and Flash's serial
   `load-0001` and mixed `short-9`. Preserve the old output hashes and failures.
   A measured native/serving mechanism, focused regression, Just build and
   serial required checks precede any further runtime change.
2. Run the exact four-arm, four-fresh-process-round single-user screen on the
   **current** candidate for all four models. Retain the original four
   inferencekit requests per arm, serial/repeat/logprob controls and mixed
   fixed-arrival wave, with no cache reuse. The original Qwen3.6/Flash round-0
   timings remain historical and cannot fill current-candidate rows.
3. For every model whose screen passes, run the original 256-prompt seeded
   Poisson 512-content-input/32-output load at offered c64, c128 and c256,
   four Williams rounds per level, with equal settings in all arms. Freeze
   each model's vanilla single-user median reference latency before its load
   cells. Register that value and the arm/binary/manifest identities in the
   job spec. At c256 use the explicit 256-waiting-request setting in both
   Skippy arms; retain the unchanged default as a separate historical control.
4. Thermal and contention boundaries require 30 continuous nominal seconds
   before each measured block. Preserve a failed cell and permit one equal
   longer-cooldown retry **only** for thermal/contention invalidation. No
   automatic retry or tolerance change hides output, completion or SLO
   failures. Run each timed arm in a fresh process and use inferencekit 0.1.1
   client timing. Record actual client in-flight, send lag, all request bodies,
   source/model/binary/runtime hashes, native settings, memory, and $0 JY
   occupancy.
5. Publish four per-model tables including every registered arm/level, TTFT,
   E2E, TPOT, aggregate output tok/s, complete/failed counts, exact-output
   verdict, capacity-SLO verdict, and the paired interval and non-inferiority
   verdict. Missing, failed or capacity-blocked cells stay visible as NOT RUN,
   FAIL or UNPROVEN; never render them as zero or passing rows. Reviewer-accessible
   evidence must replace local `target/` paths in the live PR description.

The historical Thoughtworks indices 16/227 and earlier hybrid-MoE discrepancies
remain unresolved until directly replayed; this synthetic protocol does not
clear them. No L2/L3 retention work is included.

## Separate prospective task-quality policy

The exact-output rule above remains active. Qwen3.5 vanilla itself produced
multiple texts for the same prompt under c16 while repeating its serial text;
matching one vanilla text would not establish a stable cross-engine reference.
The owner chose a separate task-quality rule on 2026-11-26. The
[version 2 protocol](SCHEDULER_TASK_QUALITY_PROTOCOL.md) was registered for
new quality cells. Preserve every failed exact comparison in the old report;
no historical FAIL becomes PASS. The version 1 exact-output performance claim
remains blocked, and version 2 cannot reuse old cells as passing quality
evidence.

A reviewable replacement needs a pinned local prompt/answer set large enough
to bound the declared effect, exact same model shards and speed configuration,
fresh A/A vanilla controls, complete per-item results, a prespecified scoring
adapter and non-inferiority margin, and paired uncertainty against both vanilla
layouts. The three prompts in `ci/inference-quality.json` are only a smoke
screen. Record nonfinite logits, incomplete requests, unstable own-output and
confident token disagreements as separate hard failures. Without a
higher-precision reference, any result would be labelled "versus the pinned
vanilla baseline" rather than broad model-quality equivalence. No dataset,
model, precision or runtime may be downloaded or substituted to fill a gap.
The timing and 30-second TTFT capacity gates stay separate and unchanged.
