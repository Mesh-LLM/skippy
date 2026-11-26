# Four-model scheduler and active-KV campaign

**Scope correction at 2026-11-24 01:17 UTC.** The user's qualification-model
question supersedes the original diagnostic roster below. The primary roster is
the four `pr: true` models from [the qualification ladder](../../ci/inference-ladder.json),
using exact [qualification artifact pins](../../ci/inference-artifacts.json):
Qwen3.5-4B Q4_K_M, Qwen3-Coder-30B-A3B Q4_K_M, Qwen3.6-35B-A3B UD-Q4_K_M,
and Qwen3.8-Flash-Next UD-IQ4_XS (all four shards).
Compare engines within each identical model/precision only. The Qwen3.5-4B
screen remains applicable. Existing Llama and Qwen3-4B results are diagnostics;
the queued Llama retry and active Qwen3.5-35B screen were canceled as superseded.
Preserve their evidence and do not advance them to load tests.

An admission job verifies the exact local model bytes and reads Metal's
recommended working-set limit. It does not download replacements or establish
runtime support. Qwen3-Coder's already pinned local fixture can proceed to its
screen. Qwen3.6 and Flash-Next require availability, actual native load/generation
and memory-headroom checks before timed full-model work. A missing input or
capacity failure must remain explicit; do not silently change model, precision
or offload. Eligible qualification models advance independently, so unavailable
models do not hold up the entire campaign. The balanced-round, correctness,
cache-off and thermal contracts below continue to apply.

The revised Paseo heartbeat is `fba0a4dd`; the old roster heartbeat `3454358b`
has been removed.

Admission verified the exact Qwen3.5-4B, Qwen3-Coder and Qwen3.6 files before
the initial 630-second deadline interrupted Flash-Next hashing. The partial
[admission record](../../target/scheduler-four-models/artifacts/admission-partial/model-admission.json)
is retained. Metal reports a recommended working-set limit of 115,448,725,504
bytes. Qwen3.6's 22,134,528,992-byte weight file leaves substantial nominal room,
but its screen still requires native settings and actual generation before
timing. Its screen is queued. Flash verification has a separate 1,800-second
lease and writes evidence after each shard; a verification timeout is not a
runtime-support or memory-capacity failure. Coder's first screen is running,
and Qwen3.5-4B rounds 1–3 are queued.

Started 2026-11-24 on `macstudio-2l0352` (Apple M1 Ultra, 128 GiB).
The user authorized all four models and continued $0 lab time without further
time approvals. Jobs retain individual bounded leases and nominal thermal gates.

## Scope and immutable inputs

Compare `skippy-base`, `skippy-patched`, `llama-slots`, and `llama-unified`.
Prefix caching and disk KV caching are off. No L2/L3 retention work is included.
The active Skippy KV pool stays unified. Vanilla's two KV layouts remain separate
controls with identical total capacity and per-sequence context targets.

- Source `671c6ebbf7661b236ce44d352024c51741b08f9c`.
- Rust candidate `b3cbe9f85ad166ffff4dd86043870ba780e447240d63b6c48373a6d67741a8d6`.
- Vanilla `8954f194837d2ec4d5f67f20c81b84abe63883ba`.
- Llama 3.2 1B Q4_K_M, digest `6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83`.
- Qwen3-4B-Instruct-2507 Q4_K_M, Qwen3.5-4B Q4_K_M and
  Qwen3.5-35B-A3B Q4_K_M use [the pinned manifests](../kv-validation/models.json).
- Existing binaries and native runtime files are digest-verified before use.
  No Rust changes or model downloads are part of this measurement campaign.

## Stages

1. Each model gets a first-round screen across all four arms. Native settings
   and exact input token IDs are checked before any timed measurement. Four
   single-user inferencekit requests, repeated serial continuations, a long
   prefill, and a fixed-arrival mixed four-request wave are saved per arm.
2. Successful screens advance to remaining balanced fresh-process rounds and
   c64/c128/c256 load cells. Four Williams rounds are registered, with arm orders
   `[0,1,3,2]`, `[1,2,0,3]`, `[2,3,1,0]`, `[3,0,2,1]` using the arm list above.
   A partial round cannot establish a performance claim. A failing operational
   screen is repaired before scheduling dependent load jobs.
3. Reproduce output differences using saved prompt/output records, same-engine
   repeat controls, serial replay, and supported logprob probes. The previous
   Thoughtworks request indices 16 and 227 and the known hybrid-MoE fixture need
   separate replay before those historical discrepancies can be called resolved.
   This new synthetic workload alone cannot resolve them.
4. Capture separate CPU/Metal timelines at workloads showing excess Skippy
   latency or GPU gaps. Keep raw captures private on the host. Attribute scheduler
   waits and active attention/KV costs. Existing kernel-class sums do not prove
   critical-path improvements.
5. Audit all cells and provide cache-off tables per model/load level, including
   exact output mismatches, requested/resolved settings, TTFT p50/p95, TPOT,
   total output tokens per full-wave second, end-to-end latency and client lag.
   Use paired bootstrap over rounds, not individual loaded requests.

## Registered configuration and limits

Sixteen active native lanes, 8,192 tokens per lane, F16 K/V, all layers on Metal,
flash attention enabled, batch and microbatch 512, sixteen CPU threads, and
vanilla busy polling disabled. Native recurrent slot counts are recorded,
because they can differ between engines. Preflight logs use verbosity only
outside timing. Timed engine processes are uninstrumented.

Load uses 256 distinct, tokenizer-verified 512-content-token prompts with 32 generated
tokens. The model's automatic special tokens are additional and must agree in
both count and exact token IDs across all arms. For Llama this includes its BOS
token (513 effective input tokens). Inferencekit 0.1.1 owns the client timing and seeded Poisson arrivals.
For each model, freeze baseline single-user median end-to-end latency before
load to set the common arrival rate `offered concurrency / reference latency`.
Each 30-second arrival window is capped at the selected offered concurrency.
These bounded bursts are not a production capacity or N* claim. Actual in-flight
requests, completed counts, cache reuse and arrival lag must be reported.
No sampled-decoding quality claim or full task-quality qualification is implied.

The mixed screen uses fixed 0/650/750/850 ms arrival offsets and 384/4,096-token
prompts. Its custom client timing is diagnostic. Published single-user/load
metrics come from inferencekit records. Requests must complete at their declared
length; cached input tokens must be zero. Missing cache counters are unproven,
not an automatic pass. Output disagreements are failures of exact agreement,
with broader numerical equivalence unproven until controlled investigation.

Minimum meaningful effect is 2%; a paired 95% interval must exclude parity.
Report tradeoffs in tail latency and long-request latency. No broad speedup or
quality claim while relevant correctness gates remain failed or unproven.

Before each measured block require 30 continuous nominal thermal seconds.
Check native pressure and competing processes during timing; discard the whole
affected cell if either fails. Preserve partial evidence and allow one fresh
retry with longer equal cooldowns. Repeated failure becomes a concrete blocker.
Per-process open-file soft limit is at least 2,048. Jobs are serialized through
JY and stop their own server process groups on exit.

## Execution records

Live jobs, job-spec digests and heartbeat ID are in
[the campaign ledger](../../target/scheduler-four-models/campaign.json).
Each job exports `suite.json`, `doctor.json`, `fixtures.json` and a bounded
synthetic evidence archive. Full private host paths are recorded for recovery.
Paseo heartbeat monitoring advances the registered stages and is removed only
after verification or a concrete terminal blocker is recorded.

## First-screen observations

The initial Llama run stopped in its first warmup when usage reported 385 tokens
for a 384-content-token prompt. All four settings preflights had completed.
The repaired harness queries both tokenizers with automatic special tokens
enabled, compares their effective token IDs, and checks usage against that
count. It preserves the original content-token hashes and the failed evidence.
The Llama round is restarted in fresh processes; no failed-run timing is reused.

Qwen3.5-4B round 0 completed all four arms. Its artifact audit verified 16
inferencekit requests and 28 diagnostic requests, zero reported cached tokens,
all four native settings records, and nominal thermal boundaries. Saved output
text matched across all arms and each arm's same-process repeat control passed.
This is an initial screen, not a four-round performance or full quality result.

[Qwen3.5-4B first-screen audit](../../target/scheduler-four-models/artifacts/qwen3.5-4b-r0/audit.json)

[Llama failed warmup evidence](../../target/scheduler-four-models/artifacts/llama-r0-failed/suite.json)

Qwen3-Coder-30B-A3B round 0 also completed all four arms. The audit verified
16 inferencekit requests and 28 diagnostic requests, zero reported cached
tokens, full native settings and nominal thermal boundaries. Saved text matched
across arms, and all same-process serial repeat controls passed. Its balanced
rounds 1–3 are queued as `job_g0n06e1p8pwp4my`. Qwen3.5-4B rounds 1–3 are
running as `job_bfy8c11y1hpezqa`. Neither first screen establishes a speedup.

[Coder first-screen audit](../../target/scheduler-four-models/artifacts/coder30b-r0/audit.json)

## Qualification screening advancement — 2026-11-24 02:45 UTC

Qwen3.5-4B completed all four balanced screen rounds: 64 inferencekit requests
and 112 custom diagnostic requests. Audits found no cross-arm text mismatches,
all serial repeat controls matched, and all cache counters were zero. The 16
arm/prompt combinations in saved inferencekit outputs also matched across fresh
process rounds. This remains a limited exact-output screen, not full task quality.
The [frozen load reference](../../target/scheduler-four-models/qwen35-frozen-reference.json)
is 791.173594 ms: the median of the four unpatched Skippy round medians. Every
arm uses this same arrival-rate reference. Four balanced c64 rounds are queued;
c128 and c256 depend on preceding-level audits.

All four exact Flash-Next shards passed the separate hash admission. A dedicated
untimed native admission is now queued, with 16 lanes and 8,192 tokens per lane,
32 generated tokens per arm, full Metal offload and native allocation accounting.
It requires at least 2 GiB between logged Metal allocations and the measured
recommended working-set limit, both before and after generation. This is a
conservative screening gate, not proof of peak memory at every concurrency.
The job permits 1,800-second Skippy startup and 1,860-second readiness per arm;
this changes only untimed startup allowances. No precision, weights, KV geometry
or offload settings change. A failed capacity gate blocks timed Flash screening
until equal revised settings are registered and validated across arms.

[Remaining Qwen3.5 screen audit](../../target/scheduler-four-models/artifacts/qwen35-4b-r123/audit.json)

[Flash exact-shard admission](../../target/scheduler-four-models/artifacts/flash-admission/model-admission.json)

## Qwen3.6 screen discrepancy — 2026-11-24 03:07 UTC

The initial Qwen3.6 screen completed 16 inferencekit and 28 diagnostic requests.
Input counts, zero-cache counters, native settings and nominal thermal checks
passed. The exact-text audit found 16 pairwise differences from Skippy base
against the two vanilla layouts (eight request cases per layout, including
repeated instances of the same prompt). Patched Skippy matched base on all saved
cases. Each arm's serial A/A and serial-versus-mixed anchor matched. The vanilla
layouts do not match each other on every diagnostic output either. No rounding,
near-tie or quality-equivalence explanation has been established.

Remaining Qwen3.6 performance stages are held. The registered control job uses
six unique failing prompt texts, two fresh processes per arm in forward/reverse
order, and serial normal/repeated-normal/top-five-logprob/repeated-logprob
requests. It keeps the exact model, 16x8192 context configuration, cache-off and
nominal thermal gates. No tolerances are relaxed; the controls diagnose the
existing discrepancy and cannot establish full task quality.

[Qwen3.6 screen audit](../../target/scheduler-four-models/artifacts/qwen36-r0/audit.json)

[Qwen3.6 memory and repeat controls](../../target/scheduler-four-models/artifacts/qwen36-r0/diagnostic-controls.json)

## Coder screening advancement — 2026-11-24 03:51 UTC

All four Coder rounds completed and passed the saved evidence audit: 64
inferencekit requests and 112 custom diagnostic requests, zero reported cached
tokens, matching text across all arms, and matching serial repeats. The 16
arm/prompt inferencekit combinations and 28 arm/wave/prompt diagnostic
combinations also matched across all four fresh-process rounds. Full task
quality remains unproven. The shared frozen load reference is 749.4787085 ms,
the median of the four unpatched Skippy round medians. Its four-round c64 load
job is queued; Qwen3.5 c64 is running. Subsequent load levels require audits.

[Coder remaining-round audit](../../target/scheduler-four-models/artifacts/coder30b-r123/audit.json)

[Coder frozen load reference](../../target/scheduler-four-models/coder30b-frozen-reference.json)

## c64 audit and diagnostic outcomes — 2026-11-24 07:26 UTC

Qwen3.5 and Coder c64 each completed four rounds, 16 cells and 1,024 requests.
Both audits verified lengths, effective input counts, settings, cache counters,
and saved evidence hashes, with zero cross-arm text mismatches. The load CLI
returns a capacity wrapper; the auditor now follows its explicit measurement ID
to the saved serve-benchmark record and verifies the request artifact hash there.
The initial wrapper-reading audit error is a harness error, not missing request
evidence. c128 is submitted for each model using the original frozen reference.
These capped bursts do not establish production capacity or N*.

Flash's first native run failed because the harness resolved its snapshot shard
symlink to a hash-named blob, preventing native split-file discovery. The retry
preserves the original snapshot filename and sibling shard names; file hashes,
precision, runtime and serving settings remain unchanged. Native support and
capacity remain unproven pending that retry.

Qwen3.6 controls completed 192 responses across two fresh processes per arm.
All same-process normal repeats and fresh-process normal outputs matched.
Logprob repeats and fresh-process logprob arrays also matched within each arm.
Five of six serial prompts still differed between Skippy and both vanilla
layouts; the sixth had differed in the mixed screen. For long-2, requesting
logprobs changes Skippy's output in both base and patched builds. Therefore that
probe cannot directly stand in for its ordinary generation path. Performance
qualification remains held; no numerical tolerance is relaxed.

[Qwen3.5 c64 audit](../../target/scheduler-four-models/artifacts/qwen35-c64/audit.json)

[Coder c64 audit](../../target/scheduler-four-models/artifacts/coder30b-c64/audit.json)

[Qwen3.6 controls](../../target/scheduler-four-models/artifacts/qwen36-correctness/audit.json)

[Flash failed admission](../../target/scheduler-four-models/artifacts/flash-native/native-admission.json)

Qwen3.6's normalized top-five logprob audit locates the short-0 disagreement at
the first generated token: vanilla chooses “ and”, Skippy chooses two newlines.
Vanilla's preference gap is 3.398212388 nats in both KV layouts; ordinary and
logprob-enabled text agree for this case and A/A repeats are exact. This is not
supported as a near-tie. It localizes this case to the initial prediction path,
without identifying an operator or proving which engine is correct. The long-2
logprob trajectory differs from ordinary Skippy generation and remains labeled
separately. The reproducible audit verifies all 192 response hashes, token/cache
accounting, finite reported logprobs and saved nominal thermal boundaries.

## Qwen3.5 c128 thermal retry — 2026-11-24 07:51 UTC

Native thermal pressure rose to level 1 during the first c128 attempt, with no
competing process detected. The guard stopped the job; partial evidence is
retained as diagnostic only. The registered single thermal retry reruns all four
balanced rounds in fresh processes, with 120 continuous nominal seconds before
each server and after each warmup, equally for every arm. The common arrival
reference, model, precision, context, workloads and correctness gates remain
unchanged. No partial timings are combined with this retry. A second thermal
failure becomes a concrete qualification blocker for this level.

[Preserved c128 thermal failure](../../target/scheduler-four-models/artifacts/qwen35-c128-thermal/suite.json)

Coder's first c128 attempt also stopped at native pressure level 1 with no
competing process detected. Its single registered retry repeats all four rounds
with the same 120-second equal cooldown policy and frozen arrival reference.
Neither model's partial first-attempt timings are combined into retry results.

[Preserved Coder c128 thermal failure](../../target/scheduler-four-models/artifacts/coder30b-c128-thermal/suite.json)

## Flash multipart harness corrections — 2026-11-24 08:18 UTC

Preserving shard names allowed native startup and 49/49 layer offload. Admission
then stopped at tokenizer HTTP409 because the harness supplied the first shard
hash instead of the aggregate split-source identity. The correction implements
`aggregate_source_sha256` from the exact pinned Rust source: domain separator,
ordered shard count, indices, byte lengths and hex SHA256 strings. The expected
identity is `8bf3015492e87a91e857e3c69c678379096ce5e7279368a999ba5b68cddf221c`.
Every shard still undergoes file digest and length verification before use.

The first memory parser also retained only the largest buffer per category,
which undercounts multiple shards and multiple KV caches. That initial memory
result is invalid for admission. The corrected parser sums every native
allocation line, including host buffers sharing physical memory, and therefore
may conservatively overcount repeated reserves. On the saved startup log the
sum is 110,881,344,061 bytes, leaving 4,567,381,443 bytes below Metal's recommended
working-set limit. This is startup allocation evidence only; no generation or
peak-memory admission has passed. A new untimed all-arm admission is queued
with both harness corrections and the existing 2 GiB headroom gate.

[Preserved tokenizer-identity failure](../../target/scheduler-four-models/artifacts/flash-native-shard-path/native-admission.json)

[Corrected startup memory audit](../../target/scheduler-four-models/artifacts/flash-native-shard-path/corrected-memory-audit.json)

## Qwen3.5 c128 audit and correctness controls — 2026-11-24 09:53 UTC

The longer-cooldown retry completed all 16 cells and 2,048 requests with valid
settings, exact input-token accounting, nominal guards and zero cached tokens.
Patched and unpatched Skippy outputs match in every paired cell. Nine pairwise
differences from vanilla occur across three prompts: `load-0190` differs from
vanilla unified in all four rounds, `load-0100` from vanilla slots in all four,
and `load-0000` from vanilla slots in round zero only. The latter is also the
only same-arm output change across the four loaded fresh-process replays.
Realized batching can differ between these replays; they are not serial A/A
controls. The mechanism and numerical equivalence remain unproven.

Before c256, register serial controls on those exact three saved prompts with
32 output tokens: normal, normal repeat, supported top-five logprobs, and
logprob repeat, in two fresh processes per arm, forward then reverse arm order.
This is 96 responses using the same pinned model/settings, fixture digest,
cache-off policy and nominal guards as the earlier correctness harness. Compare
the serial text to the loaded outputs as well as within-arm repeatability and
normal/logprob path agreement. Do not infer that a serial pass clears a loaded
failure or relax the registered exact-agreement gate.

[Completed c128 audit](../../target/scheduler-four-models/artifacts/qwen35-c128-retry/audit.json)

[Loaded repeat audit](../../target/scheduler-four-models/artifacts/qwen35-c128-retry/loaded-repeat-audit.json)

## Separate mixed-request timelines — 2026-11-24 10:00 UTC

Queue one diagnostic CPU/Metal capture per arm for Qwen3.5-4B and Coder, using
the same mixed-c4 screen fixtures and 16-lane cache-off native configuration.
The screen motivates two questions: why the later short request waits much
less with the patch, and why the original decode still pauses for roughly
half a second when long prefills arrive. Compare all four arms; do not presume
Skippy is slower or attribute the delay to KV without timeline evidence.

Use 120 continuous nominal seconds before each fresh server and after its
warmup. Each Instruments Metal System Trace plus Time Profiler window is
bounded to 60 seconds, with the four-request replay starting after attachment.
Skippy additionally exports scheduler spans and request links through its
existing debug telemetry. Model settings, input token identities, output
lengths, cache counters and thermal/contention guards remain enforced.
These instrumented requests are diagnostic only and cannot establish a speedup.
Qwen3.5's c128 correctness hold remains independent of this attribution work.

Raw Instruments captures, OTLP files and later XML exports stay on the Mac.
Only synthetic response metadata, settings logs and derived summaries leave
the host. After capture, run the existing private trace analyzer and require
PID-attributed CPU/GPU rows and complete request windows before interpreting
idle gaps, scheduler steps or active-KV geometry. Missing symbols, dropped
telemetry, incomplete windows or missing request links remain explicit limits.

## Coder c128 audited; c256 registered — 2026-11-24 11:32 UTC

Coder's single longer-cooldown c128 retry completed all four balanced rounds:
16 cells, 2,048 requests, exact input-token accounting and zero cached tokens.
All paired output text matches across four arms; all 512 arm/prompt groups also
match across the four fresh-process loaded rounds. Native settings and thermal
guards passed. This is the registered exact screen, not full task-quality proof.

Advance Coder to c256 with the same frozen 749.4787085 ms baseline reference,
four Williams rounds, 256 capped arrivals per cell, and 120 continuous nominal
seconds before each server and after warmup, equal across all four arms. Retain
the three-hour bounded lease and existing per-cell caps. The longer starting
cooldown follows the c128 thermal evidence; it changes no model/runtime/settings
or output gate. Permit only the registered single fresh equal-longer-cooldown
retry on thermal failure. Capped arrivals remain distinct from actual in-flight
concurrency or production capacity.

[Coder c128 audit](../../target/scheduler-four-models/artifacts/coder30b-c128-retry/audit.json)

[Coder c128 loaded repeats](../../target/scheduler-four-models/artifacts/coder30b-c128-retry/loaded-repeat-audit.json)

## Flash repeated-reserve accounting correction — 2026-11-24 11:44 UTC

The aggregate-identity admission loaded 49/49 layers and generated all 32 probe
tokens with zero cache reuse, then falsely rejected the post-generation memory
report. It summed two identical scheduler reserve reports as new allocations.
The [pinned native source](https://raw.githubusercontent.com/ggml-org/llama.cpp/8954f194837d2ec4d5f67f20c81b84abe63883ba/src/llama-context.cpp)
reads each backend's current scheduler buffer size during `sched_reserve` and
logs those totals after every reserve pass. The saved destructor also reports
the single expected 7,840.5951 MiB Metal and 1,109.1302 MiB CPU compute buffers.

The parser now sums separate model shards, KV/state and output allocations,
but sums compute backends within each completed reserve pass and retains their
per-backend high-water totals across passes. This preserves multiple backends
with the same printed name. Partial or unknown compute reports fail admission.
The corrected post-generation estimate is 110,881,344,061 bytes, leaving
4,567,381,443 bytes below the recommended limit. It does not prove transient or
all-concurrency peak memory. The existing 2 GiB gate and 16-lane configuration
are unchanged; rerun all four arms before any Flash timing.

Checks cover duplicate reserve passes, two same-name backends within a pass,
growing buffers, an insufficient-headroom rejection and an incomplete-pass
rejection. Earlier evidence and its failed verdict are preserved.

[Preserved admission failure](../../target/scheduler-four-models/artifacts/flash-native-identity/native-admission.json)

[Corrected reserve accounting](../../target/scheduler-four-models/artifacts/flash-native-identity/corrected-memory-audit.json)

## Qwen3.5 serial controls and transport control — 2026-11-24 12:09 UTC

All 96 saved serial responses passed artifact, length, cache and finite-logprob
audits. Every same-process and fresh-process repeat matched within each arm;
ordinary and logprob-enabled text matched. All four arms produced identical
serial text for the three selected prompts. However, serial text differs from
the loaded text on some prompts in every arm. These serial requests were
non-streaming, whereas inferencekit load requests streamed. Therefore this
evidence does not isolate batching as the cause or clear c128 exact agreement.

Register one transport control using the same three prompts and exact settings:
repeat the same two fresh-process passes, with normal and normal-repeat requests
now streamed through the guarded fixed-request client; keep supported logprob
requests non-streaming. Preserve event timing and complete usage alongside text.
This is again 96 diagnostic responses. Audit repeats, normal/logprob path
agreement and comparisons with both earlier serial and loaded outputs before
making any claim about load-dependent computation. No performance gate changes.

[Serial-control audit](../../target/scheduler-four-models/artifacts/qwen35-c128-correctness/audit.json)

[Serial versus loaded text](../../target/scheduler-four-models/artifacts/qwen35-c128-correctness/serial-vs-loaded.json)

Qwen3.5's four private mixed-c4 captures completed. The metadata audit verifies
16 complete requests, matching output text across arms, cache counters zero,
unchanged model/build identities and native settings, and nominal boundaries.
Both Skippy captures link all four requests to scheduler spans and report no
dropped telemetry. Raw trace PID attribution and complete-window checks remain
pending in the queued private-host analysis; these observations are diagnostic.
The auditor also rejects a synthetic nonzero-cache record.

[Qwen3.5 timeline metadata audit](../../target/scheduler-four-models/artifacts/qwen35-timeline/audit.json)

## Coder timeline metadata audit — 2026-11-24 12:52 UTC

All four private mixed-c4 captures completed. The exported metadata audit verifies
16 complete requests, matching text across all arms, zero cached tokens, pinned
model/build identities, unchanged native settings, and nominal thermal boundaries.
Both Skippy captures link all four request IDs and report no dropped telemetry;
each records 17 prefill, 126 decode, and one mixed scheduler iteration.

CPU/GPU PID attribution and complete capture-window checks remain pending in
private analysis job `job_exj4ce2v5zm6cmh`. Raw traces remain on the host; the
profiled timings are diagnostic. Coder c256 started in the next serialized lease.

[Coder timeline metadata audit](../../target/scheduler-four-models/artifacts/coder30b-timeline/audit.json)

## Coder c256 completion gate failure — 2026-11-24 14:49 UTC

The job completed all 16 cells, but the strict qualification audit failed on
request completion. Each Skippy build completed 144 of 256 requests and rejected
112 with HTTP 429, “generation queue is full; retry later”, in every round.
Both vanilla layouts completed all 256 in every round. Across the campaign cell
set this is 4,096 attempts, 3,200 completions, and 896 queue rejections.

The pinned source default computes waiting capacity as eight times the active
lane count, clamped to 16–256: 16 lanes imply 128 waiting requests. The observed
144 admitted requests agree with 16 active plus 128 waiting. Both builds use
that default; no queue override was supplied. This is a bounded admission
capacity finding, not evidence of a new patch regression or a thermal failure.
Do not silently enlarge queues, retry the same test as a thermal failure, drop
rejections, or compare successful-only latency as equivalent completed work.

The separate failure auditor preserves the failed qualification verdict while
checking every request artifact hash, unchanged identities/settings/fixtures,
native input counts, successful output lengths, zero cache counts, and nominal
boundaries. All 1,728 comparisons among shared successful outputs match, and
successful same-arm fresh-process repeats match. Rejected requests have no output
comparison. A synthetic nonzero-cache response is rejected by this auditor.

[Strict qualification failure](../../target/scheduler-four-models/artifacts/coder30b-c256/qualification-failure.json)

[Capacity failure audit](../../target/scheduler-four-models/artifacts/coder30b-c256/failure-audit.json)

Flash's corrected all-arm native admission started next. Remaining Qwen3.5
transport controls and both private trace analyses remain queued.

## Flash all-arm native admission — 2026-11-24 15:09 UTC

The corrected admission completed all four configurations. Exact CI shard pins,
aggregate source identity, immutable runtime/candidate identities, requested
settings and native 49/49-layer Metal offload pass the saved-evidence audit. All
four 8-input/32-output probes match exactly with zero cached tokens; recorded
before/resident/after native thermal pressure is zero. Recomputed allocation
totals meet the unchanged 2 GiB headroom gate in all arms: 4.254 GiB for each
Skippy build, 13.854 GiB vanilla slots, and 9.767 GiB vanilla unified.

Both Skippy builds resolve native `n_seq_max=32`; vanilla resolves 16, despite
the same requested 16 active serving lanes. Retain this implementation difference
in settings and memory reporting; do not describe native sequence reservations
as identical. This untimed probe does not prove peak concurrent memory or full
numerical equivalence. Timed preflight records the complete fixture token IDs.

Register Flash screen round0 with the existing16lanes/8192context/F16/fullMetal
settings and unchanged exact weights. Use equal120-second continuous nominal
cooldowns for all four arms and a bounded7200-second lease. Audit the first
screen before scheduling rounds1–3 or frozen-reference load tests. No reduced
lane, precision or offload substitution is needed. Job `job_mrd5na8tze0bs42`
is queued behind the controls and private trace analyses.

[Flash native admission audit](../../target/scheduler-four-models/artifacts/flash-native-reserve-accounting/audit.json)

## Streaming controls and private trace analysis — 2026-11-24 15:36 UTC

All96 streaming-control responses passed token/cache/artifact and repeat audits.
Their text equals the96 earlier non-streaming responses. All48 saved loaded
request bodies for the three selected prompts match the serial streaming bodies
exactly (including sampling settings and prompt text), yet33 loaded responses
differ from their serial counterpart. Thus transport alone does not explain
the loaded discrepancies. Concurrent execution, batching and request history
remain coupled; the responsible operator is not identified. The nine c128
cross-arm discrepancies persist, and Qwen3.5 c256 remains blocked. Qwen3.6's
previous controlled numerical discrepancy remains an independent blocker.

[Streaming controls](../../target/scheduler-four-models/artifacts/qwen35-c128-stream-controls/audit.json)

[Transport versus loaded comparison](../../target/scheduler-four-models/artifacts/qwen35-c128-stream-controls/transport-vs-loaded.json)

Both private analyses completed: four captures per model, all16request windows
per model fully contained, nonzero target-PID CPU/GPU rows, and no unattributed
rows. Derived phase durations agree with saved client event timestamps. Other
process GPU rows are excluded from target busy-time unions. Raw traces stay
private; profiled figures are mechanism evidence only.

Late short-request TTFT is7.571s/base versus1.551s/patch on Qwen3.5 and
8.179s/base versus1.436s/patch on Coder. Target GPU intervals cover97.5–98.2%
of those waits. Request-linked first scheduler work moves from7.239s to1.218s
after arrival on Qwen3.5, and7.820s to1.026s on Coder. These observations support
a scheduling-order mechanism, rather than CPU bookkeeping or GPU starvation,
for the patch's late-request improvement. They do not isolate every admission
or execution boundary. Aggregate recorded scheduler overhead is7.6–17.5ms
across each roughly10-second Skippy wave.

The anchor request still has496–640ms maximum token-event gaps, with475–616ms
of Metal activity inside those gaps. Attention geometry is recorded, but these
command/encoder traces do not provide shader-level attention/KV attribution.
Do not infer that attention/KV is clear or that all scheduler problems are fixed.

[Qwen3.5 trace coverage and mechanism audit](../../target/scheduler-four-models/artifacts/qwen35-timeline-analysis/audit.json)

[Coder trace coverage and mechanism audit](../../target/scheduler-four-models/artifacts/coder30b-timeline-analysis/audit.json)

## Flash readiness retry and completed statistics — 2026-11-24 15:58 UTC

Flash screen job `job_mrd5na8tze0bs42` failed before any timed cell: Skippy base
did not become ready within the harness's 360-second limit. The archive,
suite and doctor artifacts were fetched with digest verification. `fixtures.json`
was not exported because preflight did not complete; its absence is preserved.
This does not overturn the successful untimed all-arm native admission.

Retry job `job_qaqgfkw3v2kh44z` uses the same 1,800-second startup allowance as
native admission, with 1,860-second harness readiness, equally for all arms.
No weights, precision, offload, lane count, timing workload, correctness gate or
120-second cooldown changed. The bounded lease is 14,400 seconds at $0, and the
spec SHA256 is `4f4c990d41c8e87d4dc9020996cc7c60cfa423db74af53011fadec0c78a67bd9`.
This is a harness-startup correction, not use of the thermal retry allowance.
Audit the screen before advancing balanced rounds or loads.

[Failed readiness artifacts](../../target/scheduler-four-models/artifacts/flash-screen-r0-readiness/suite.json)

The statistics script re-audited all 96 completed Qwen3.5/Coder cells and
recomputed actual client occupancy, TPOT p95 and lag from hashed request records.
Four-round paired bootstrap resamples rounds, with all 256 possible samples
enumerated. Occupancy overlap/tie and constant-ratio/parity controls pass.
Single-user lag is missing and reported N/A rather than zero. All c64/c128
cells fail the configured 30-second p95 TTFT SLO despite complete requests.
No single/c64/c128 patched/base metric satisfies the combined 2%/95% criterion.
Mixed diagnostic late-request improvements have long-request and anchor-gap
tradeoffs; these are recorded separately, with no general speedup claim.

[Completed statistics](SCHEDULER_FOUR_MODEL_STATISTICS.md)

## Flash screen mismatch and registered controls — 2026-11-24 17:08 UTC

Readiness retry `job_qaqgfkw3v2kh44z` completed at 17:01:02 UTC. All four fetched
artifacts pass SHA256 verification. The screen audit verifies four cells,
16 inferencekit and 28 diagnostic responses, zero cache counts, full 49/49 Metal
offload, settings and nominal boundaries. Its exact model, baseline and candidate
identity records equal the successful native admission records. Preserve the
earlier failed-readiness archive independently.

Four pairwise text mismatches remain: inferencekit `load-0001` and mixed-wave
`short-9` each differ between Skippy base and both vanilla layouts. Patched/base
outputs match throughout, and same-process short repeats are exact in every arm.
Hold screen rounds 1–3 and c64/c128/c256; do not treat JY success as equivalence.

Register `job_zgqevpjrcz2z8f2` for the two exact saved prompts: `load-0001` with
32 output tokens and `short-9` with 48. Two fresh processes per arm, forward then
reverse arm order, each perform normal streaming, normal streaming repeat,
non-streaming logprobs=5, and logprob repeat: 64 responses total. These controls
separate serial repeatability, supported logprob path and mixed execution effects;
they cannot by themselves identify an operator or clear all loaded behavior.
Keep exact weights/runtime/native16lane configuration/cache0 and the equal
1,800-second startup allowance. Use existing correctness nominal gates of
60 seconds before launch and 30 seconds before each prompt; no timed performance
comparison is accepted from these controls. The lease is bounded at14,400s/$0.
Spec SHA256: `ad251efbc4de5a61872582b25c3389ca4ab22e4e313d5bbd39b7266ebd4c8c28`.
Python/embedded compilation,64-response spec/target/budget checks pass; the prior
192-response Qwen3.6 evidence audit still runs unchanged after adding Flash cases.

[Flash round-0 audit](../../target/scheduler-four-models/artifacts/flash-screen-r0/audit.json)

## Bounded closeout — 2026-11-24 17:33 UTC

Flash controls `job_zgqevpjrcz2z8f2` completed at17:29:57 UTC. Both exported
artifacts pass SHA256 verification; all64 responses pass token/cache/boundary,
text-hash and finite-logprob audits. Every same-process and fresh-process repeat
is exact within its arm. Normal streaming and non-streaming logprob text match.
`load-0001` still differs between Skippy and both vanilla layouts at reported
token index8 (“ each” versus two newlines), with a vanilla preference gap of
0.089606285 nats. No responsible operator or tolerance clearance is established.

`short-9` agrees serially across all arms. All16 control-versus-screen text
differences are vanilla's mixed-wave `short-9`; Skippy matches its serial result.
The comparison verifies immutable model/runtime/candidate, exact launch arguments,
native settings and KV allocation values, fixture hashes and saved inferencekit
request bodies. Raw native log timestamps are excluded from allocation comparison.
The auditor rejects a synthetic startup-argument drift. Mixed request bodies
were not independently exported; those prompt checks use the exact saved fixture.

All32 registered jobs are terminal. Numerical blockers hold Qwen3.5 c256,
Qwen3.6 rounds1–3/all loads, and Flash rounds1–3/all loads; Coder c256 fails
completion at the pinned default admission limit. No further unchanged reruns,
weight/runtime/offload substitutions or silent queue overrides are eligible.
Completed occupancy is14.936 node-hours; every sealed job budget cap is$0.
Close the heartbeat after report links, job/spec hashes and `git diff --check`
are verified. Historical Thoughtworks16/227 and old hybrid-MoE findings remain
unresolved. CPU/Metal traces support a scheduling-order mechanism but do not
isolate attention/KV shader costs; no new Rust change is justified here.

[Flash controls audit](../../target/scheduler-four-models/artifacts/flash-correctness/audit.json)

[Screen-to-control comparison](../../target/scheduler-four-models/artifacts/flash-correctness/screen-comparison.json)
