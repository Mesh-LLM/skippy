# Scheduler qualification failure investigation

## Coder current-candidate balanced screen after selected serial pass

The versioned Coder selected serial audit passed 64/64 responses and exact
text across both vanilla layouts, orders and modes. Advance only this eligible
model to the registered original four-arm, four-round single-user screen.
Use the exact archived Coder fixture and CI GGUF, original vanilla separate
slots/unified, original Skippy server/native, and the combined Skippy server
with fixed Metal. Run all 16 timed cells in Williams order with a fresh process
per cell, inferencekit 0.1.1, original four serial 512/32 requests plus the
registered serial/mixed waves, one warmup per cell (192 total requests), 120-second nominal
cooldown, 16×8192 context, F16 KV, native batch/microbatch 512, full Metal,
both caches off, unchanged seed/generation settings. The registered 120-second
cooldowns alone exceed the initial 10,800-second cap; before model submission,
raise that cap to 21,600 seconds while keeping the $0 limit and zero retries.
Seal model, source, runtime/binary, fixture and selected-screen audit hashes.
Cost cap $0, lease cap 21,600 seconds, no automatic retry. Require complete cells, request
and token accounting, native/thermal gates, output digests, and the archived
exact-output checks before promoting Coder to c64/c128/c256 load. Pair four
rounds against **both** vanilla layouts under the registered 2% rule; retain
all losses and the separate 30-second TTFT capacity verdict. This model's
screen cannot clear Qwen3.5, Qwen3.6 or Flash.

## Coder current-candidate harness schema repair

The first selected Coder run, `job_8mjr45cadydxmhb`, failed before any
generation: the shared historical model record has a single-shard `name` and
SHA but no `expected_bytes`, while the new candidate wrapper required that
field. The independent exact CI admission record did contain the expected
size and had passed before submission. Preserve the failed job and its
spec/log. The sealed v2 attempt `job_5nanbzjty7gzdr6` then passed model
preflight but failed before generation because the shared control script
hardcoded the Qwen3.6/Flash fixture SHA; Coder's archived fixture has a
different SHA. Preserve that failure and its spec/log too. For
`coder-current-correctness-v3`, let the original suite verify
the staged model's bytes, then explicitly compare the resolved file's SHA,
basename and actual size to the exact CI pin before adding `expected_bytes`
to the report schema. Pass the archived Coder fixture SHA as an explicit
control-script argument and report it. Change no model, binary, native bundle, generation
request, cache setting or thermal gate. Seal a new spec and stage payload;
do not retry either failed job. Run the same 8-process/64-response selected
screen at $0, then audit both the new harness provenance and outputs. The two
pre-generation failures remain harness failures, not inference results.

## Coder current-candidate serial admission before balanced timing

The completed Coder c256 queue256 retest used the original server/native
identity; it cannot certify the combined server plus fixed-Metal candidate.
Register a selected-prompt serial screen on the exact Coder CI shard and
original fixture `load-0001` (512 input/32 output) and `short-9` (384/48).
Compare original Skippy, current combined Skippy, and both original vanilla
layouts. Run two reversed fresh-process blocks, ordinary/repeat/logprobs/repeat
per prompt: 8 processes and 64 responses. Keep 16×8192 context, F16 KV,
batch/microbatch512, full Metal, caches off, exact requests and generation,
nominal thermal, zero cache counts and no automatic retry. Lease ≤10800
seconds, cost $0, exact sealed source/model/runtime identities. The old c256
completion and single-user timing records remain historical. This selected
screen can reject current-candidate exact output; passing it would only permit
the preregistered balanced full screen, not establish non-regression by itself.

## Qwen3.6 serving/Metal composition isolation after current replay

The current combined-server plus fixed-Metal replay completed 64 selected
serial responses, but all eight registered exact comparisons against the two
vanilla layouts failed. The original-server plus original-Metal control
reproduced its archived output. Those two corners change both components, so
they cannot attribute the changed current text. Register the two missing
corners: original Skippy server plus fixed-Metal native bundle, and combined
Skippy server plus original native bundle. Use the same exact Qwen3.6 CI pin,
`short-0`/`long-2` fixture text, 128/24 output tokens, generation bodies,
16×8192 context, F16 KV, native batch/microbatch512, full Metal, both caches
off, and no generation warmup. Run two reversed fresh-process blocks, each
with ordinary/repeat/logprobs/repeat for both prompts: 4 processes and 32
responses. Require exact binary/native/model/fixture hashes, sealed staging,
native settings, complete usage, zero cache counters, nominal thermal and no
automatic retry. Lease ≤3600 seconds, charge $0 on `macstudio-2l0352`.
Compare text hashes to the two already audited corners and both vanilla
controls. This identifies which component changes the selected text, without
asserting which output is better, changing the exact-output gate, or claiming
full-workload quality/timing parity. Further runtime edits need a measured
operator or serving mechanism and the required Just/serial checks.

## Current-candidate Qwen3.6 and Flash serial correctness replay

Before further load timing, replay the original divergent prompts against the
current combined Skippy server plus qualified fixed-Metal native bundle. Use
the unchanged pinned original Skippy server/native bundle and both original
vanilla layouts as controls. Qwen3.6 cases are `short-0` (128 output tokens)
and `long-2` (24); Flash cases are `load-0001` (32) and `short-9` (48). The
original fixture and exact CI model shards, generation bodies, 16 lanes ×8192
context, F16 KV, native batch/microbatch 512, full Metal, and both caches off
are required. Do not substitute a runtime, weight, or precision.

For each model run two reversed four-arm blocks, with one fresh process per
arm in each block. In each process send both cases in the original fixture
order, each as ordinary, ordinary repeat, top-five-logprob, and logprob repeat
requests. This is 8 fresh processes and 64 responses per model. Require exact
file/model/binary/bundle hashes, native settings, request and output hashes,
token/cache/completion checks, nominal thermal/contention guards, and no
automatic retry. The registered lease cap is 10800 seconds and charge cap $0
on `macstudio-2l0352`. Preserve first divergences and all prior failures.
This serial screen can establish whether the current candidate reproduces the
recorded failures; it cannot clear concurrent, full-workload, quality, or
timing gates by itself. A remaining mismatch blocks the planned balanced
timing promotion under the current exact-output contract.

## Qwen3.5 unchanged-vanilla `load-0100` concurrency control

The fixed-Metal Skippy serial replay now matches both fixed Skippy c128 output
groups, while vanilla's saved c128 `load-0100` text differs from its old serial
controls. Test the **unchanged pinned vanilla binary** with the exact archived
`load-0100` request, keeping the original separate-slot and unified layouts,
16×8192 context, F16 KV, 512 logical/microbatch, full Metal, cache off and
32 generated tokens. Do not rebuild or substitute vanilla in this control.

For each layout use two fresh processes. Alternate one- and sixteen-identical-
request waves twice, with starting sizes 1 and 16 and reversed arm order in
the second block. Four processes, 16 waves and 136 responses are expected.
Require exact model/binary/fixture/request hashes, real native load and full
offload, complete token and zero-cache accounting, nominal thermal/contention
guards, and no automatic retries. Lease ≤3600 seconds, cost $0 on
`macstudio-2l0352`. Compare all serial and concurrent text hashes to the
archived serial and heterogeneous c128 outputs. Preserve all distinct outputs
and failures. This diagnostic separates identical-peer batching from a need
for heterogeneous peers; it does not name a faulty operator, change an exact-
output gate, establish a timing win, or replace the registered comparator.

Started 2026-11-24 UTC after the user requested fixes and retests. The
[original campaign](SCHEDULER_FOUR_MODELS.md) remains closed with its failed
gates intact. New jobs and occupancy are recorded separately in
[the fix ledger](../../target/scheduler-fixes/campaign.json).

## First experiment: Qwen3.6 observation and history isolation

The existing controls reproduce a large initial-prediction disagreement on
`short-0` and a normal-versus-logprob disagreement on `long-2`. They do not
identify a faulty operator. Speculation was already disabled, and `ignore_eos`
excludes the ordinary backend-sampler fast path; neither is assumed causal.

Use the existing exact Qwen3.6 artifact pin and the original Skippy baseline
and vanilla separate-slot binary identities, with 16 lanes, context 8192,
F16 KV, batch/microbatch 512, full Metal offload and both caches disabled.
For each prompt and arm, launch a fresh process starting with ordinary requests
and another starting with logprob requests. Alternate modes twice per process:
8 processes, 32 responses. Retain the original output lengths (128 and 24),
tokenizer verification, zero-cache/completion checks, thermal/contention gates,
and 60-second startup / 30-second per-request cool-down. Record request bodies,
response hashes, launches, native settings and logs. Maximum lease 3600 seconds,
maximum charge $0, no automatic retry.

Compare first-request outputs with the archived controls and each other, then
compare repeats and mode switches. This separates prior-request dependence from
an observation-path difference; it does not establish operator-level causality
or authorize relaxed correctness tolerances. No runtime fix is claimed until a
mechanism is reproduced and a targeted fix passes regression and native retests.

## Remaining issues

- Qwen3.5 c128: isolate batch shape and request history behind loaded-versus-serial
  differences. Serial streaming/non-streaming controls already agree.
- Flash: retain the serial `load-0001` disagreement and vanilla mixed-versus-serial
  `short-9` difference. Do not assume these share Qwen3.6's cause.
- Coder c256: bounded waiting-queue admission rejects 112/256 requests per Skippy
  cell. Evaluate an explicit queue configuration as a separate experiment;
  increasing the queue is not inherently a scheduler bug fix or an SLO win.
- Keep historical Thoughtworks indices16/227 and old hybrid-MoE discrepancies
  unresolved until directly replayed. Preserve measured scheduler latency
  tradeoffs; no speculative Rust edits or cache-retention work.

## Coder queue configuration retest

The CLI already provides `--generation-queue-capacity`. Register 256 waiting
requests for **both** Skippy arms, retaining 16 active lanes and all model/native
settings. This corrects this workload's admission configuration without changing
the engine's bounded-queue default. Vanilla retains its original configuration,
which completed all 256 requests. The original 128-waiting-slot results remain
failed completion-gate evidence, not replaced by the retest.

Repeat c256 across all four arms in four balanced fresh-process rounds, with the
original frozen reference latency 749.4787084999999 ms, 120-second cool-down,
same inferencekit 0.1.1 client, prompts, sampling, 30-second TTFT / 1-second TPOT
SLOs, precision and cache/offload checks. Lease cap 10800 seconds and charge cap
$0. Record the explicit option in both preflight and timed launch records and
reject an unregistered or unequal override. Source inspection verifies this CLI
option flows to generation admission; the zero-error completion check must
demonstrate that the additional requests actually enter and complete. Do not
infer a precise runtime queue bound solely from 256 successful requests.

Success requires 4096/4096 complete requests, exact cross-arm and repeat outputs,
valid token/cache counts and nominal thermal/contention checks. Report latency,
throughput and SLO failures regardless of completion success. Use a paired
four-round comparison and the original 2% / 95% CI criterion for any performance
claim; successful admission alone is not a speedup. One equal 240-second cooldown
retry is allowed only for thermal/contention invalidation, preserving the failed
attempt. No retry or tolerance relaxation hides output or completion failures.

## Qwen3.6 driver trace follow-up

The 32-response fresh-process audit reproduced the `long-2` mode difference
regardless of which mode ran first; all responses match their archived mode.
The current source routes ordinary requests through the built-in driver and
logprob requests through the feature driver. These paths use different prefill
code, but the executed steps have not yet been measured for this case.

Register two fresh Skippy-baseline processes on `long-2` only, alternating
ordinary/logprob modes twice and reversing the starting mode: eight responses.
Keep the same binaries, model, generation settings and cooldowns as the first
experiment. The only diagnostic additions are `SKIPPY_TELEMETRY_STDERR=1`,
`--telemetry-level debug`, and `--telemetry-queue-capacity 65536`. Record each
request's wall-clock window and native scheduler telemetry; require an
unambiguous route event, no reported event drops, and unchanged outputs before
using the trace to attribute prefill differences. This is not timing evidence.
Lease cap 1800 seconds, charge cap $0; queued after the running Coder retest.

The [progress report](SCHEDULER_FIX_PROGRESS.md) records completed audits and
retains the original failures.

## Qwen3.5 offered-concurrency and history isolation

Use the saved `load-0190` prompt (512 input / 32 output tokens), whose original
c128 output differed from serial controls in every arm and round. Keep all
four pinned arms, 16 active lanes, context 8192, F16 KV, batch/microbatch 512,
full Metal offload and caches off. Each request has the exact archived streaming
body. Submit waves containing either one or 16 identical copies, all with zero
scheduled offset. This controls peer content but does not force native batching
or reproduce the original heterogeneous c128 arrival schedule.

For each arm use two fresh processes, alternating wave sizes 1/16/1/16 and
16/1/16/1. Reverse arm order in the second block. There are eight processes,
32 waves and 272 responses. Tokenizer verification performs no generation;
there is no generation warmup before the first wave. Require 60-second nominal
startup soak, 30-second nominal per-wave soak and the existing thermal/contention
guard. Record launches, native settings, exact request bodies, wave/response
hashes, stream arrival windows, completions and zero cache counters. Lease cap
3600 seconds, charge cap $0, no automatic retry; queue behind existing jobs.

Compare every response with its arm's archived serial and all four archived
c128 subject outputs. Compare serial waves before/after concurrent waves and
the concurrent-first fresh process with the serial-first fresh process. Preserve
all differences. A reproduction narrows the stimulus needed; a non-reproduction
does not clear c128 or exclude sensitivity to heterogeneous peers, scheduling,
slot assignment or larger batches. Native batch/operator attribution requires
additional measured evidence. This diagnostic makes no performance claim and
does not change a runtime or tolerance.

## Flash mixed-wave and prior-history isolation

The saved Flash controls agree between ordinary and logprob modes; they do not
reproduce Qwen3.6's mode discrepancy. `load-0001` still differs between engines
in serial generation, while `short-9` agrees serially but differs in vanilla's
mixed screen. Keep these as separate unresolved observations.

Replay `short-9` alone and the exact original four-request mixed schedule:
`short-8` (the screen's alias of `short-0`) at 0 seconds / 128 output tokens,
`long-2` at 0.65 seconds / 24 tokens, `long-3` at 0.75 seconds / 24 tokens,
and `short-9` at 0.85 seconds / 48 tokens. Retain the 0.85-second scheduled
offset for `short-9` in the serial wave. Record exact streaming request bodies
and actual arrival windows; do not claim native batch shape is controlled.

For each of all four arms, alternate serial/mixed/serial/mixed in one fresh
process and mixed/serial/mixed/serial in another, reversing arm order in the
second block. Eight processes, 32 waves, 80 responses, no generation warmup;
tokenizer checks do not generate. Keep the exact four Flash shards, original
16 lanes / 8192 context / F16 KV / batch and microbatch 512 settings, caches
off and full Metal offload. Use the previously registered 1800-second startup
allowance, 60-second nominal startup soak and 30-second per-wave soak with
thermal/contention guards. Lease cap 10800 seconds, charge cap $0, no automatic
retry; queue after the already registered jobs.

Before submission, recheck the saved `flash-admission/model-admission.json`
against current CI pins and the successful `flash-native-reserve-accounting`
audit. That audit verifies actual generation, 49/49 Metal layers and positive
logged headroom (minimum 4.254 GiB); it is not a transient peak-memory guarantee.
The exact mixed workload already completed in the original screen; this is an
untimed correctness replay, not an expanded full-model load test.

Audit native settings, hashes, counts, cache counters and thermal boundaries;
compare all mixed responses with the archived screen and `short-9` with both
archived serial controls and the opposite starting order. Preserve disagreements
and errors. Reproduction in a mixed-first process would exclude preceding test
waves as necessary, but not effects of peers within that wave. This cannot
identify an operator, clear serial `load-0001`, or support a speed claim.

## Qwen3.6 equal prompt-boundary control

The completed eight-response driver trace preserves every archived mode output
and reports zero telemetry drops. Ordinary requests prefill all 4096 tokens in
eight scheduler steps of 512, then feed back 23 generated tokens. Logprob
requests report 4095 prefill tokens and 24 single-token feature iterations.
The feature prefill chunk counter describes its wrapper call, not native
microbatches. This establishes different executed serving paths, not a faulty
operator or causality for the output difference.

Register a separate eight-response replay of both fresh starting orders with
`--batch-size 4096` in both modes, keeping `--microbatch-size 512`, the exact
model/binaries, 16 lanes, context 8192, F16 KV, full Metal, caches off, telemetry
and 60/30-second nominal cooldowns unchanged. No generation warmup. The source
permits whole-prompt feature prefill when the prompt fits the admitted batch;
the new trace must verify what actually executes. All setting differences are
explicitly recorded; the original batch512 observations remain authoritative.
This is an untimed diagnostic, not a replacement qualification configuration.
Lease 1800 seconds, maximum charge $0, no automatic retry; enqueue after the
already registered Qwen3.5 and Flash diagnostics.

Compare first/repeated outputs across modes and with the batch512 records.
Agreement would narrow the difference to the changed batch/prefill conditions,
not prove which operator or path change caused it. Disagreement also remains
valid evidence. Native settings, full offload, memory observations, exact
requests, token counts, cache counters, thermal boundaries and telemetry must
be audited before conclusions. No runtime fix or tolerance change is claimed.

## Direct-prefill admission fix and regression replay

The batch4096 job failed after one successful ordinary response. Telemetry ends
between feature-prefill stats-before and direct iteration; the client retained
HTTP400 but omitted its body. Pinned source admits whole-prompt prefill against
native `n_batch=4096`, then rejects it against the scheduler's 2048-token direct
limit. Fix admission to require both limits, preserving the existing fallback
and all scheduler limits. This does not align the original two drivers or claim
to fix their numerical difference.

Patch `9b2e8840f9cce88b7711a593681e010ea89b4710c6c3e926d6ec0f4957e49e59`
contains only three serving files on source `671c6ebbf7661b236ce44d352024c51741b08f9c`.
The earlier scheduler candidate is excluded. An isolated checkout passed Just
serial formatting, focused check, all-target Clippy with warnings denied, and
full serving tests: 1351 passed, 32 ignored; zero documentation tests. These are
offline checks, not hardware qualification. Build job `job_cp3tgxv699f18ac`
uses a fresh pinned JY workspace and offline Cargo, existing dependencies, a
3600-second lease and $0 cap. Reuse the unchanged verified native bundle.

After the build succeeds and its identity is fetched, run 32 attempts across
eight fresh processes: baseline/fixed, batch4096/batch512, normal-first/logprobs-first.
Each process alternates modes twice on the exact `long-2` request. Reverse arm
order with starting mode. Keep microbatch512, 16 lanes, context8192, F16KV,
full Metal, caches off, telemetry, 60-second startup and 30-second request
nominal cooldowns. No generation warmup, downloads or substitutions. Lease3600,
charge$0, no automatic retry. Capture every response/error body and hash.

Expected baseline result is four batch4096 logprob HTTP400s containing the
2048-token configured-iteration rejection. Every other attempt must complete:
28 successful responses total, with correct tokens, zero caches and valid
logprobs. Both binaries' batch512 outputs and numerical logprobs must match the
archived mode exactly; fixed responses must repeat across both starting orders.
Retain batch4096-vs512 differences explicitly. Audit identities, launch/native
settings, thermal boundaries, request windows and telemetry before calling the
admission fix qualified. This test cannot clear original cross-engine or
ordinary/logprob differences. Leave unrelated CI reservations untouched.

## Qwen3.6 microbatch455 prompt-boundary control

The admission fix passed its native before/after replay, but both logical-batch
settings retained the original mode difference. Pinned serving source chunks
the ordinary path by `n_ubatch`; the cache-off feature fallback chunks the first
4095 tokens by the same bound and handles the final prompt token separately.
The observed 512 configuration therefore ends prefill as 512 versus 511+1.

Register baseline-only logical batch512 / microbatch455 on the unchanged
4096-token `long-2` request: two fresh processes, normal-first and logprobs-first,
alternating modes twice (eight responses). Since 4095 = 9 * 455, source predicts
nine 455-token prefix chunks followed by one token in both paths. Preserve the
exact CI model, original baseline binary/native bundle, 16 lanes, context8192,
F16KV, full Metal, caches off, sampling and 24-token output limit. Use the same
telemetry debug/65536 queue, 60-second startup and 30-second request nominal
soaks, no generation warmup, lease1800, cost$0, no automatic retry. No Rust or
native changes are introduced. This is a diagnostic setting, not a replacement
for the original qualification configuration.

Require native logs to resolve n_batch512/n_ubatch455 exactly, full offload,
unchanged native state allocation, exact requests/counts/cache counters,
nominal boundaries/guards and no reported dropped events/export errors.
Trace must show ordinary prefill [455]*9+[1], feature prefix4095 followed by
24 single-token feature iterations. The feature wrapper does not expose each
native microbatch: its nine-prefix-chunk shape is a source-derived prediction,
not independently measured operator evidence. Preserve every output/logprob
and compare modes, starting orders, repeats and archived microbatch512 results.
Neither agreement nor disagreement proves a faulty operator; both are evidence.
Agreement would narrow the discrepancy to the changed prefill/batch conditions;
it cannot isolate rounding, native API path, output-row selection or sampling
setup by itself. Keep Q35/Flash and cross-engine failures unresolved.

## Qwen3.6 microbatch511 negative boundary control

The455 diagnostic passed all evidence gates and all eight outputs agreed,
including first requests and repetitions. Its ordinary output shifted to the
archived512 logprob text. It changed both every prefix chunk and the final
prompt boundary, so it does not establish which caused agreement.

Register eight responses with the same baseline, exact model/native bundle,
logical batch512, full Metal/F16KV, caches off, 4096-token long-2 prompt and
24-token output, but explicit microbatch511. Both fresh starting orders alternate
modes twice. Keep telemetry debug/65536, 60/30-second nominal soaks, zero
generation warmup, lease1800, $0 cap and no automatic retry. Native logs must
resolve511 exactly. Ordinary trace must be eight511-token steps plus8tokens;
feature trace must retain4095-prefix and24single-token iterations. The feature
source predicts eight511-token chunks plus7 then1, but its wrapper does not
independently measure these native chunks.

Audit exactly as455, including hashes, requests, token counts, caches, full
offload, thermal/contention guards and telemetry loss. Compare every mode/order/
repeat and both archived512/455 outputs and available numerical logprobs. If
both modes also agree at511, an aligned final-token boundary is not necessary
for agreement in these cases. If they differ, the boundary hypothesis remains
plausible but is not proven: the overall batch dimensions also changed. Preserve
either outcome; do not alter production settings, tolerances or Rust from this
experiment alone. This is an untimed mechanism control, not qualification.

## Qwen3.6 unchanged-runtime direct API probe

Both455/511 controls agree across modes but produce different text; final-token
alignment alone is not necessary. Register a diagnostic C++ caller linked to the
original native bundle, with no edits to Rust, native runtime or model. Fetch no
new dependencies. Compile through Just against public headers archived from the
bundle's exact patched commit6704435209b0fa8eabc3aa91e3c0a22f92b89863, using the
existing shared native Git object store. Require the registered manifest hash
c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481
and verify every runtime file hash. The local compile/ABI smoke uses an existing
local build of the same native source; it is not the remote qualification binary.
The remote probe must link only the exact registered remote bundle.

Run four native paths twice in fresh processes, reversing order on repeat:
`whole` uses iteration API eight512-token chunks and samples the final row;
`whole-logprobs` keeps those chunks and additionally requests native logprobs;
`split-iteration` uses iteration API seven512 plus511 prefix tokens, then samples
the final prompt token separately; `split-prefill` uses the raw prefill API for
that same4095-token prefix, then the same sampled iteration API for the last
token. Split paths request logprobs. Every path emits24greedy tokens with
ignoreEOS, seed42 and original sampling settings. Read post-call vocabulary
logits through the existing context accessor; keep float rows private on host,
export only hashes/top20 IDs/logits/logprobs and response/token IDs. These are
post-call logits, not an assertion that sampling never transforms the buffer.

Use exact CI weights and original long-2 fixture; require its4096token-ID hash
before generation. Keep logical/microbatch512,16lanes,8192context, F16KV,
full Metal41/41, no rollback, prefix/disk caches absent. Direct unsplit model
loading bypasses the server planner: compare whole output against archived
ordinary and split-prefill against archived logprob output before attributing
server behavior. A failed calibration limits applicability and is preserved.
No new qualification or performance claim follows from this diagnostic.

Eight processes/192generated tokens, zero generation warmup, lease3600, $0, no
automatic retry. Before each process require60seconds nominal; after native
load, validate resolved settings and require30seconds nominal before signaling
GO. Poll thermal/contention during generation, bounded to300seconds per process.
Record before/after host snapshots, requests/launch/config, binary/header/source
identities and every response/raw-row hash. Audit repeats and compare logits
only while generated histories share a prefix; after divergence comparisons
cannot identify the cause. This can separate caller path/chunk/logprob effects,
not identify a faulty tensor operator by itself.

### Native probe header preparation repair

The first probe could not archive headers because the cached vanilla repository's
shared Git object store had been removed. It generated no responses. Transport
only the exact pinned header diff from upstream8954f194 to patched67044352 in
three SHA256-bound private JY chunks (120seconds/$0 each, no inference). Verify
all upstream working headers, apply the sealed diff in a private directory, and
verify every resulting header against local Git objects at the native manifest's
patched commit. Retain licenses. No runtime/model replacement or download.
Rebuild only the standalone caller with Just, then repeat the same registered
eight-process probe. Preserve the failed setup and both immutable specs.

## Cache-off logprob prefill alignment fix

The direct native probe calibrated both original server outputs and repeated all
192 output logit rows exactly across fresh processes. Whole versus whole-logprobs
is exact; split-iteration versus split-prefill is exact. Whole versus split changes
post-call logits at output0 and the selected token at output2 with identical prior
generated tokens. This isolates the registered mode discrepancy to prefill chunk
shape, without establishing a faulty operator or cross-engine quality result.

Implement only cache-off logprob prefill alignment: reuse the built-in scheduler's
actual positive prefill chunk bound, submit complete prompt chunks through the
same direct queue, and sample only the last row of the final chunk. Retain the
feature driver/session so recorded logprobs survive until collection. Preserve
ordinary generation, cache-enabled contracts, prefill-only and single-token paths,
and the existing native/direct admission bounds. Per-chunk cancellation remains
checked by the direct queue. Do not widen queues, tolerances or native budgets.

Develop on isolated source671c6ebb plus the qualified admission patch. Run serial
Just formatting, focused check, warning-denying all-target Clippy and full serving
tests. Native replay must reproduce baseline failures and demonstrate fixed normal/
logprob text equality, repeatability, readable logprobs and unchanged ordinary
output at batch512 and4096, fullMetal/cachesOFF/exactpins/nominalthermal. Include
short and long prompts. No fix claim before that replay passes; Q35/Flash and
cross-engine differences remain separate unresolved work.

Native replay registration: candidate patch
`bd1956c5ea17cc588598b49d3c6be1d5f725d34fecaca2afb28304327b59cca0`, binary
`f6798db48c310477627b3366ee99e64f1b00cab2f6ede005f64aa206be42f6de`.
Eight fresh processes: batches4096/512, normal-first/logprobs-first, baseline/fix
with arm order reversed by starting mode. Each process alternates four long-2
requests (4096input/24output), followed by both short-0 modes (384input/128output).
48attempts: four expected original baseline4096/logprob400errors,44completions,
24fixed completions. Fixed normal/logprob text must equal archived ordinary text
for each prompt; repeated fixed logprobs must match across starting orders and
batch settings. Preserve baseline512 mode differences and exact baseline error
bodies. Require feature prefill telemetry4096tokens/eightchunks for fixed long
requests and384tokens/onechunk for short requests. Existing token, cache, native,
artifact and60/30second thermal/contention gates apply. Lease5400seconds, cost$0,
no automatic retries, no generation warmup. This serial replay does not qualify
concurrent, cache-enabled, other-model or cross-engine behavior.

## Combined scheduler candidate qualification

The isolated alignment fix passed its48-attempt native replay:44completed,
24fixed and4expected baseline errors. Apply the alignment-only delta over the
existing scheduler candidate without replacing its changes. Preserve a backup
of the pre-integration diff and seal the full combined source. The one-context-
line transport patch must reconstruct the same Git tree as the full diff.

Build the combined candidate from the same pinned baseline through the existing
Just builder, original native bundle unchanged, fresh checkout,3600s/$0lease,
no retries. After its build artifact is hash-verified, explicitly preregister its
patch and binary identities in a separate replay spec. Repeat the exact existing
48-attempt protocol and independent audit: two prompts, both mode orders,
batch4096/512 with micro512, original baseline versus combined candidate,
8fresh processes, expected44completions/4baselineerrors/24fixed, same native,
cache, thermal, trace and exact parity gates. Keep5400s/$0lease and no retries.
This is combined-candidate serial correctness evidence, not concurrency, other-
model or performance clearance. Preserve both isolated and combined artifacts.

## Qwen3.5 unchanged-baseline scheduler trace

The previous272-response history experiment found all Skippy serial requests
stable and every identical c16 wave internally identical but different from
serial, in both fresh starting orders. Before attributing a native operator,
measure the executed scheduler steps using the unchanged original Skippy baseline.
No new runtime, native library, model, precision or sampling changes.

Repeat the same load-0190 prompt (512input/32output) with two fresh processes:
waves1/16/1/16 and16/1/16/1,68responses total. Add only debug telemetry,
SKIPPY_TELEMETRY_STDERR=1 and queue65536. Preserve16lanes8192context,
batch/micro512,F16KV,fullMetal and cachesOFF, zero generation warmup,
60second startup/30second per-wave nominal soaks and continuous wave guards.
Lease1800seconds,$0,no retries. Verify all original binary/model/fixture hashes.

Require exact request/response accounting and text calibration against each
corresponding saved baseline history wave. Audit request windows, native settings,
zero-cache counts and thermal boundaries. Require complete built-in route counts,
no telemetry drops/export errors, and scheduler sums512prefill and31decode tokens
per request. Retain step shapes and active sequence counts for each wave. These
are serving scheduler observations, not independent proof of native kernel/row
layout or a faulty operator. No timing claim, original c128 clearance, tolerance
change, queue widening or production fix follows from this diagnostic alone.

## Qwen3.5 unchanged-native decode grouping/residency control

The68-response trace calibrated every saved output. Each serial wave has one
512-token prefill then31single-token decode steps. Each c16 wave has16separate
512-token prefill steps and45decode steps, beginning2..16rows, followed by
steady16 and a completion ramp-down. No mixed prefill/decode step was observed.
This motivates separating live-session residency, decode grouping and staggered
positions using a standalone caller of the original native library.

Use exact Q35 CI pin, original remote native bundle/header hashes and load-0190
512-token ID hash83de68a4172aa4dab09da1e24667392b20f8b52fcaf916593d77a40b5713281e.
Build the diagnostic caller through Just; no native/Rust/model change. Four modes:
solo creates one session; resident-serial prefills16sessions separately then
runs each decode individually in round-robin order; resident-batched uses the
same resident prefill state but decodes all16together; staggered creates/prefills
lanes successively, decodes active lanes after the second and each subsequent
prefill, then drains active lanes. Completed sessions remain resident until the
end of each process, unlike any server cleanup differences; calibration must
therefore pass before server applicability claims. Every lane generates32tokens.

Repeat each mode fresh twice in reversed order:8processes,98lane completions,
3136generated tokens, no warmup. All config stays16lane capacity/8192context,
batch/micro512,F16KV,33/33Metal,ignoreEOSgreedyseed42,no cache objects. Gate exact
native settings after load, then30seconds nominal;60seconds nominal before each
process; thermal/contention polling and300second generation deadline. Lease3600s,
$0,no retries. The host hashes original model/library/header bytes before work.

Capture subject lane0 post-call full-vocabulary logits privately after each of
its32steps; export hashes/top20 and token IDs, not raw float arrays. Native sampled
request indexes must preserve the registered mapping. Read the original batch
row used by the native sampling API (511for prefill,0for subject decode); record
all call lane IDs/input positions. Audit all98lane lengths,3136tokens,256subject
rows, exact call plans, hashes/native/thermal gates and repeatability. Require solo
text to calibrate saved serial output and every staggered lane to calibrate saved
c16 output before conclusions about the server. Preserve failures and differences.
Compare subject logits only through the first differing token; later histories
are unequal. This probes native batching sensitivity, not a specific faulty
operator, production fix, performance, c128 clearance or permission to relax gates.

## Qwen3.5 public-context activation localization control

The calibrated native grouping probe isolates a batch-triggered difference: solo
and resident-serial subject logits are exact for32outputs, while resident-batched
and staggered first differ in logits at output index1 and token IDs at index3.
No faulty operator is established. Register a diagnostic public llama caller,
linked to the identical original native bundle, to access the context-construction
cb_eval hook without changing library/Rust/model code. This is an explicitly
changed caller, not a substitute runtime or a production candidate.

Run solo-plain, batch-plain, solo-trace and batch-trace, twice fresh in reversed
order:8processes,68lane completions,2176tokens. Each lane gets its own complete
512-token prefill, then31decode steps (one or16rows),32outputs. Preserve exact
Q35pin/prompt IDs,131072total context,16active lanes/8192capacity each,32native
sequence slots,zero rollback,512batch/micro,F16KV,33Metal layers,thread counts16,
no caches. Use direct greedy selection with EOG excluded; unlike the previous
Skippy sampling wrapper this caller does not apply chat sampling. Require every
lane's token IDs/text AND all32subject full-logit hashes to match the archived
solo/resident-batched probe before any attribution. Explicitly test tracing-off
and tracing-on equivalence and fresh repeats. If calibration fails, retain the
failure and do not infer a model/operator defect from this caller.

The callback observes only output indexes1..3 and nonleaf F32 tensors with
shape[features,decode_width,1,1], contiguous first dimension, at most300000features.
Save the complete first token column, dimensions,strides,op,name and source-shapes;
3D/4D tensors and weights are outside this capture. This is a first observed
activation difference, not necessarily the earliest native operation difference.
Callback boundaries may change graph fusion; calibration is mandatory. No
post-token-flip causal comparison, timing claim or correctness tolerance.

Raw activation/logit arrays stay private on the host. Export their hashes and
privately computed pairwise max-absolute/RMS differences, with unmatched nodes
explicit. Audit finite values,coverage,matching metadata/hash decisions and
repeatability. Retain original artifacts and all unobserved-node limitations.
Build caller through Just with warnings denied; use existing exact header
transport/model/native hashes.60second nominal beforeprocess,30second postload,
continuous thermal/contention guard,300second generation deadline,$0/3600slease,
zero retries. No downloads, offload substitutions or native/Rust edits.

## Qwen3.5 captured-projection arithmetic reference

The calibrated activation probe observed identical layer0 normalized inputs but
small QKV projection differences. Register a read-only arithmetic comparison over
its existing private arrays; no new generation or serving configuration change.
Read `blk.0.attn_qkv.weight` directly from the exact pinned GGUF using the original
library's public GGUF metadata and type-traits dequantizer. Validate[2560,8192]
shape, packed row/total bytes, file bounds and full model/native/header hashes.
No weight conversion is substituted into model execution.

For output indexes1,2,3 in solo-trace and batch-trace, both archived repeats,
verify private probe-record and input/output-array SHA256 against the sealed
activation evidence before and after reading. Twelve cases,98304dot products.
Compute left-to-right FP64 and compensated FP64 sums of the same dequantized F32
weights and recorded F32 inputs. Compile without contraction or fast math through
Just. Save full FP64 vectors and original packed weight bytes privately; export
hashes, max/RMS/relativeL2 errors, worst-row summaries and accumulation checks.
The same input must produce the same reference bytes across modes/repeats.

Report the conservative generic FP32 forward-error expression
`gamma(5120)*sum(abs(weight*input))` and FP64 summation expression
`gamma(2559)*sum(abs(weight*input))`, where `gamma(n)=n*u/(1-n*u)` and
u=2^-24 or2^-53. These are diagnostic classical arithmetic bounds, not an
assertion of Metal's actual reduction algorithm, a calibrated model-quality
budget, permission to relax existing gates, or proof all operators are correct.
No acceptance threshold is inferred from the observed errors. Shared native
dequantization is an explicit dependency of this reference.

No model load/generation is performed by the arithmetic executable. Prior
fullMetal/native/calibration results remain authoritative; this CPU calculation
is not offload of a generation arm. Use nominal host gate and telemetry around
the bounded CPU work,300second calculation deadline,$0/900secondlease,no retries,
no downloads/substitutes. Preserve all original arrays, failures and limitations.
An offline synthetic8192-row F32GGUF checks exact arithmetic/row addressing and
an injected error before submission; it does not qualify real model behavior.

## Qwen3.5 captured gate-projection controls

Existing calibrated captures show `z-0` / `blk.0.attn_gate.weight` has a much
larger solo/grouped difference than QKV despite the same exact `attn_norm-0`
input: maxabs0.011259,0.016582,0.014408 at output indexes1,2,3. This branch is a
separate matrix multiplication before gated recurrent normalization; do not
attribute its discrepancy to amplification of the QKV error.

Register the same read-only private arithmetic workflow for gate weight shape
[2560,4096],12cases/49152dots per reference variant. Retain baseline FP64/plain
and compensated accumulation. Add explicit diagnostic FP64 dot products with
(a)only inputs rounded through IEEEFP16,(b)only dequantized weights rounded
through IEEEFP16,(c)both rounded. These are CPU hypotheses over saved operands,
not substituted serving weights/precision, offload, quality thresholds or proof
of a particular GPU path. The pinned Metal source has shape/type-dependent MMA
dispatch; its generic path uses float operands, so an FP16 explanation must not
be assumed. Report all variants even when none explains the observed difference.

Require source/model/native/header/private-array hashes, unchanged captures,
repeat/reference equality, finite errors and full4096row accounting. Raw packed
weights and FP64 vectors remain private. Original Metal generation/calibration
is reused, not rerun. Just warnings-denied/no-contraction build and synthetic
4096row exact/injected-error tests plus nontrivial FP16-rounding verification
must pass before submission. No library/Rust/model change. Nominal host guard,
300second calculation deadline,$0/900secondlease,no retries,no downloads.

## Qwen3.5 Q4_K intermediate scale controls

Gate-reference job localized the larger discrepancy to Q4_K grouped projection;
whole-operand FP16 controls failed to explain it. The exact pinned Metal helper
`ggml/src/ggml-metal/kernels/dequantize.h` divides the upper-nibble scale by
`16.h` before float assignment. Register a separate, read-only CPU calculation
on the same 12 private captures and packed gate tensor. No library/model change.

Compare FP64 sums after three explicit dequantization hypotheses: float scale
`d/16.f`; half-rounded scale `half(d/16)`; and that half result with subnormals
flushed to zero. Preserve normal lower-nibble scale and packed scales/minima.
Compute F32 reconstructed weights in each hypothesis and retain the original
pinned CPU dequantizer / FP64 and compensated sums as references. Export all
three error summaries, even if none explains the observation. Raw arrays stay
private. These CPU simulations do not measure Metal's actual selected kernel,
compiler semantics or subnormal policy, and do not authorize a precision change.

Require original exact input/model/native/header hashes, 12 cases / 49152 dots
per variant, same-input reference equality across modes/repeats, finite metrics,
nominal host guard, $0/900-second lease, 300-second calculation deadline, no
retries/downloads. Just warnings-denied build and synthetic Q4_K tests covering
all eight subblocks, both nibble halves and normal/subnormal scales must pass.
No inference-quality threshold or production patch is inferred from this test.

## Isolated original Metal gate projection replay

After scale control identifies the half-rounded upper-nibble scale as a close
match to grouped outputs, preregister an unchanged-library Metal operator replay.
Use the same exact Q4_K gate tensor and 12 saved input/output cases, two archived
repeats of indexes 1..3, widths 1 and 16. Replicate the saved normalized input
column across all 16 lanes. This is an explicit synthetic replication of the
captured column; no assumption that unrecorded peer columns were byte-identical.
Therefore require byte-exact first-column calibration to the archived full-model
projection before applying the isolated operator conclusions to that model.

Create a single `ggml_mul_mat` graph through pinned public headers and original
Metal backend; no scheduler, model generation, altered weights, replacement
native library or modified shader. Execute each graph twice, assert finite
outputs, record complete repeated-output byte equality and replicated-peer
agreement. Capture all output columns privately, export SHA256 and comparisons.
Require 12 cases / 24 graph executions, widths and counts, exact original
model/library/header/input/source identities, Metal backend, nominal thermal
boundaries/continuous guard. $0/900-second lease, 300-second execution deadline,
no retries/downloads. Build via Just with warnings denied; ABI smoke required.
A calibration failure remains explicit and blocks server applicability claims.
This operator diagnostic is not a full-model qualification or timing benchmark.

## Isolated Q4_K scale fix qualification

Measured evidence now includes calibrated original Metal projection replay and
an exact one-term synthetic regression. The isolated candidate promotes the
upper-nibble division from `16.h` to `16.f`, preserving small quantization scales
before F32 assignment. It adds a native CTest case at widths 1,2,3,8,16,17,32,
with eight half scale bit patterns including subnormals. Expected one-term
results are exactly representable; require byte equality, no tolerance.

Candidate ordered core patch SHA256
`7b9ff3fd8f0f5e9dec6d2800e6f95e5de449e178d4e570fbedfa443b71d733f0`,
native source `e09c584b22b280a93903058129a76ac7b90df8d4`; root native queue stays
unchanged until qualification. ABI/header interfaces and model precision remain
unchanged. Run full native replay through Just using supplied dependencies and
retain every failed test. Local checks do not replace the primary M1 Ultra
operator and model retests. Keep the original library beside the candidate.

Before a remote build, inventory existing pinned llama.cpp and whisper.cpp
sources. If Git objects are missing but indexed working files remain, an offline
recovery may copy those existing bytes into a new private Git repository only
when every blob hash, mode, complete tree hash and imported original commit hash
match the pinned identity. Do not modify the original cache or fetch replacement
sources. Reject missing/corrupt inputs explicitly. Source reconstruction is not
model or hardware qualification.

M1 Ultra acceptance still requires the exact synthetic regression, calibrated
isolated gate replay with the candidate library, and full-model replay under the
registered original native settings, exact CI weights and caches-off requests.
Record all output/logit differences and repeated-run stability. A passing scale
regression proves this arithmetic fix, not that all concurrency, cross-engine or
other-model failures are resolved. No tolerances, queues or performance claims
are widened to accommodate new outputs. Seal candidate runtime/compiler/source
identities before launch and preserve the original comparison arms.

## Paired Metal-only component qualification build

The exact upstream llama source was recovered offline, but the pinned speech
source is absent on the M1 Ultra. Register an explicit component experiment:
rebuild only `ggml-metal` twice, from original native6704435209b0 and candidate
e09c584b22b28, with the same host compiler, SDK and Just recipe. Configure the
pinned llama source and build only the Metal target; do not build or replace
libllama, CPU, speech or any other library. Copy the original verified runtime,
replace its three Metal aliases with relocated/signed builds, and require every
other runtime file to retain its original SHA256. Record composition metadata
separately from the original primary-library build identity. This is not a full
native-bundle rebuild or production release artifact.

Require the original M1 manifest c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481,
exact prepared source/patch hashes, compiler/SDK/CMake settings, both component
manifests and retained-file hashes. Run the exact synthetic regression at widths
1/2/3/8/16/17/32 after 30 seconds nominal cooldown per arm. Original mismatch
counts must be 0/0/9/24/48/51/96, candidate all zero, with expected exits2/0.
Export all logs/errors and thermal boundaries. Lease4200seconds,$0,no retries,
no source/model downloads. No model generation in this build job.

Local component composition already passed this exact regression, retaining
non-Metal files from the separately identified local development bundle. That
check and the native suite (336passed,1skipped) do not qualify M1 model behavior.
Before using the rebuilt pair for model conclusions, the rebuilt original must
calibrate against the archived original operator and full-model outputs. A
calibration failure is explicit and blocks causal attribution; no silent runtime
substitution or tolerance widening. All other campaign failures remain open.

## Paired Q4 component operator and native model replay

After both component builds pass the exact synthetic regression and provenance
audit, replay the 12 saved gate inputs through each component (24cases,
48graph executions). Require rebuilt-original first-column outputs byte-exact
against the archived full-model captures, repeat/replicated-peer equality, exact
model and input hashes, and nominal thermal/contention gates. Compare candidate
outputs with the existing private FP64 CPU reference for each input; report all
max/RMS/relative errors without creating a new quality threshold. Raw weights,
activations, references and logits remain private on the host.

Then run eight fresh native processes: original/fixed x solo/resident-batched x
two repeats, reversing arm and mode order in repeat1. This is68lanes and2176
output tokens. Reuse the unchanged batch-probe C++ caller, original public
headers and exact Q35 load-0190 fixture (512input/32output). Keep33Metal layers,
16lanes/8192context, F16KV, logical/microbatch512 and caches absent. Require the
rebuilt original to match every archived token and all32subject-logit hashes in
both modes/repeats before model applicability. Record candidate repeat stability,
solo/grouped token and logit parity, and first disagreement even if unresolved.
No quality/performance clearance follows solely from improving one operator.

Use60second nominal soak before each process and30seconds after native readiness,
300second per-process execution deadline, continuous thermal/contention guard,
$0/3600second lease and no retries. Build callers through Just with warnings
denied. The component manifests remain explicit experimental compositions;
original libllama/CPU/other libraries are unchanged. No root native patch yet.

The sealed replay exceeds the JY command-size limit. Stage its compressed harness
in three hash-addressed chunks on the same authorized host, each $0/300seconds,
no model load/build/download. Verify each stage receipt, every chunk digest and
the aggregate digest before extraction into a fresh job directory. The final
spec hashes all supplied scripts and compact input records, and retains hashes
of the full original reports from which those records are selected. Submit only
after component audit and staging receipts pass; no duplicate submissions.

## Streaming serving integration replay after Q4 native qualification

The native retest reproduces every baseline capture and shows exact solo/grouped
tokens with the fix, although logits still differ. Integrate the exact qualified
core patch and native regression target into the root queue; preserve all existing
Rust candidate files. Root preparation must resolve the already-tested native
revision e09c584b22b280a93903058129a76ac7b90df8d4. No ABI/API change.

Register a streaming load-0190 replay with three explicit arms: original Skippy
binary plus rebuilt original Metal; the same binary plus fixed Metal; and the
previously qualified combined scheduler binary d80dd044eb06c96e917945afa517470cb991082d259542ffe7d23fa8dc029d58
plus fixed Metal. All other native libraries retain original bytes. Two fresh
starting histories per arm, c1/c16/c1/c16 and c16/c1/c16/c1; reverse arm order in
the second block. Six processes,24waves,204responses,512input/32output per request.
Require the original arm to match every archived serving output, then report
fixed-arm serial/concurrent/repeat and direct-native output equality without
relaxing checks. This is not original heterogeneous c128 load qualification.

Keep exact Q35 CI pin,33Metal layers,16active lanes/8192context,F16KV,
batch/micro512,prefix/disk caches off, identical streaming request bodies,
telemetry debug/65536 and no generation warmup. Require60second nominal startup
soak and30seconds per wave, thermal/contention guard, native settings/headroom
observations, zero cache counters,512prefill+31decode tokens per request and zero
telemetry drops/export errors. Serving-step traces do not independently identify
native kernels. $0/3600second lease,no retries. Stage two sealed harness chunks
with $0/300second caps; verify receipts and aggregate digest before launch.

The audit's archived synthetic204response/24wave test must retain original output
differences and reject binary substitution, changed offload and cache requests.
Those tests validate the audit only, not candidate generation. Preserve C++ facade
missing-GoogleTest limitation and every unrelated historical/model failure.

The first serving harness was superseded before any model launch. The diagnostic
component manifest placed `component_rebuild` at top level, which the actual CLI
strict schema rejects. The v2 harness creates a private serving view with that
unchanged provenance under the supported `build` map. It preserves the complete
original manifest separately, preregisters each view manifest hash, and verifies
all25runtime files remain byte-identical. No runtime schema/ABI changes. A local
CLI loader test verifies original rejection and corrected acceptance; this is
packaging validation, not model qualification. The original two staging jobs
completed and their receipts/occupancy are retained; their main job was never
submitted. Register fresh content-addressed v2 stages and a single v2 replay.

## Original heterogeneous c128 fixed-component replay

After the registered Q4 serving integration passed, replay the original Qwen3.5
c128 four-round workload and frozen791.173594ms arrival reference. Use the
original fixture, inferencekit0.1.1 client, four balanced arm orders, 128
heterogeneous requests/cell, and original vanilla slot/unified binaries. The
Skippy baseline arm uses the original binary with qualified fixed Metal; the
Skippy patched arm uses the qualified combined scheduler binary with the same
fixed Metal. These are explicit new identities, so the original failed c128
record remains immutable. Exact CI shard, full Metal33,16lanes8192ctx,F16KV,
batch/micro512, caches off,120second cooldown,30second TTFT/1second TPOT SLO,
$0/10800second lease and no retries. Two content-addressed harness chunks are
staged and receipt-verified before the main job. Compare completions, outputs,
repeat parity, latency and throughput with the original c128 evidence. Keep
original failures visible, and make no winner claim without all checks.
