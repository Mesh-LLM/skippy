# Prospective task-quality and performance qualification, version 2

Registered after the owner chose a separate task-quality gate on 2026-11-26.
This is a new prospective protocol. The [version 1 exact-output results](SCHEDULER_NONREGRESSION_PROTOCOL.md)
remain FAIL where recorded; neither old timing cells nor three CI smoke prompts
are promoted to version 2 passes.

## Scope of a possible claim

If every gate below passes, the PR may say: “On the listed cache-off Apple M1
Ultra Metal workloads and objective task set, the current Skippy candidate
showed no greater than 2% performance regression against either pinned
vanilla llama.cpp layout, with no measured task-quality regression beyond the
registered 2% bound.” This does not assert identical tokens, general model
quality, other hardware, or satisfaction of the absolute latency SLO.

Use only the four exact CI model revisions and shard hashes in
`ci/inference-artifacts.json`: Qwen3.5-4B, Qwen3-Coder-30B-A3B,
Qwen3.6-35B-A3B, and Qwen3.8-Flash-Next. The candidate is the current
combined Skippy server plus qualified fixed-Metal native bundle. Compare with
both original vanilla layouts on the pinned binary. No model, weights,
precision, runtime, backend, or dataset substitution or download is allowed.
Run on `macstudio-2l0352` with 16 lanes ×8192 context, F16 K/V, logical and
microbatch 512, full Metal offload, prefix and disk caches off, and verified
memory headroom. A model or load that cannot meet this configuration is
capacity-blocked, not silently resized.

## Register the quality fixture before generating results

Seal a versioned generator, scorer, 256-item source task manifest, and their
SHA256 digests before any quality or version 2 timing cell. Use seed 42 and
64 items each of: integer arithmetic, keyed record extraction, ordered-value
selection, and simple Python assignment semantics. Each item has an objective
answer and a fixed deterministic parser; malformed or unparseable responses
fail that item. The model-specific prompt text places the task after neutral
padding, is tokenizer-verified across all arms, and must have exactly 512
effective input tokens. Fix output at 32 tokens with identical generation
parameters in every arm. Preserve every prompt, expected answer, raw response,
parser decision, token count, and digest. A fixture that cannot reach these
shapes for a model is NOT RUN; do not replace its task or alter only one arm.

The sealed offline source fixture is
[`scheduler-task-quality-v1.json`](scheduler-task-quality-v1.json), SHA256
`ccf46ac48e9257d29b8bcf0ba188bbc01e1fe380badfdace807fb524b2bca302`.
The generator, parser, scorer and exact binomial-bound implementation are
[`scripts/profile-scheduler-task-quality.py`](../../scripts/profile-scheduler-task-quality.py),
SHA256 `0b8b783c800fa15021327dc406c930728bc96bd455bcfbe0644f808fbc00807c`.
The manifest contains exactly 64 items in each stratum. A response is correct
when its final nonempty line is an integer, optionally prefixed by `ANSWER:`, and equals
the registered integer; an unparseable response is incorrect. The scorer
requires exactly 256 completed items, unique IDs and 512 input tokens per
item. The independent job audit must additionally verify model-specific
materialization, full request bodies, generation parameters, zero cache use,
native settings, and complete output provenance. No quality result has yet
been measured with this fixture.

The [prompt materializer](../../scripts/lib/profile_scheduler_task_materialize.py),
SHA256 `4a5aa78ee90e5f52e28b28c213d9e994a876dc00b42d244622f5b71286450670`,
selects a detokenized prefix of neutral text and places the unchanged task at
the end. It searches for exactly 512 effective tokens using the pinned
model's live tokenizer, then records prompt and effective-token digests.
Its offline contract check covers all 256 tasks; that check uses a synthetic
tokenizer and is **not** model-specific tokenization evidence. The live
materialized prompts and their hashes must be sealed before new model runs.

The scored task set is deliberately bounded. The three prompts in
`ci/inference-quality.json` remain a smoke check. Open-ended historical
notebook outputs are retained as diagnostics rather than assigned new
after-the-fact scores.

For each model and each registered level (single, c64, c128, c256), score
the exact speed configuration. Run a fresh A/A repeat of **each** vanilla
layout and the current candidate at that level, with the same task IDs and
generation settings. Require the candidate's parsed answer to agree with its
own repeat for every item; retain raw-text differences as diagnostics. Score
both vanilla layouts and the current Skippy candidate; retain the original Skippy arm as a
diagnostic. Require 256/256 completed responses per scored arm and at least
200 baseline-correct items per vanilla comparator. For each comparator,
count a candidate adverse item only when vanilla answers correctly and
Skippy does not. Let `n` be the number of baseline-correct items and `k` the
adverse count. The exact one-sided 95% Clopper–Pearson upper bound for `k/n`
must be **strictly below 0.02** for each layout and level. If its one-sided
lower bound is strictly above 0.02, the result is FAIL; a bound crossing the
margin is UNPROVEN. Apply the same
bound to a fresh vanilla A/A comparison; if the reference is too unstable,
the quality verdict is UNPROVEN. Candidate-only wins do not cancel adverse
items. Preserve per-stratum counts and all discordant item IDs. These are
per-comparison bounds, not a simultaneous familywise 95% guarantee.

Any nonfinite logit/logprob, incomplete output, missing task, mismatched
tokenization, cache hit, or candidate self-instability is FAIL. The archived
Qwen3.6 confident token divergence and Flash/Qwen3.5 numerical differences
remain open diagnostics. A task score alone does not adjudicate a confident
distribution disagreement: resolve it with a measured numeric mechanism or
an explicitly pinned independent reference before a same-model engine claim.
No unmeasured tolerance is introduced.

## Matched performance and capacity gates

Use fresh-process Williams orders, four preregistered rounds, one warmup and
equal thermal cooldown per arm. Freeze the vanilla single-user median E2E
reference before load cells. Use the same 256 task IDs as the scored quality
set at offered c64, c128, and c256, with an explicit 256-waiting-request
setting in both Skippy arms at c256. Keep inferencekit 0.1.1 and the original
client timing and request-evidence contract. Allow one equal longer-cooldown
retry only for thermal/contention invalidation; never retry an output,
completion, or performance failure selectively.

For each level and vanilla layout, form four paired ratios for aggregate
output tok/s and p95 TTFT, E2E, and TPOT. Use the full 4⁴ empirical paired
bootstrap and its two-sided percentile 95% interval. Throughput's lower
bound must exceed 0.98; each latency upper bound must be below 1.02. A bound
crossing the margin is UNPROVEN, and a wholly regressed interval is FAIL.
State every ratio, interval, loss, incomplete cell, and exact-text mismatch.

Separately report PASS/FAIL for TTFT p95 ≤30 seconds, TPOT p95 ≤1 second,
and zero failed requests, per arm and level. Relative non-inferiority cannot
turn an absolute SLO failure into a pass. Publish four per-model tables with
quality, exact-text diagnostic, relative performance, and capacity verdicts,
plus reviewer-accessible hashes and artifacts. Historical Thoughtworks
indices 16/227 and old hybrid-MoE discrepancies remain unresolved.
