# Mac Studio scheduler and KV qualification

This campaign measures the three [locally validated fixes](SCHEDULER_KV_FIXES.md)
on `macstudio-2l0352`, an Apple M1 Ultra with 128 GB. It compares patched
Skippy with unchanged Skippy and both KV configurations of pinned vanilla
llama.cpp. Skippy uses the same unified native KV runtime in both builds.

The campaign began with a $0, 45-minute lab allowance. The user later
authorized continuing the $0 lab work without further time approvals.
Lab occupancy includes the candidate build, native startup checks and measured waves. The
baseline source is `671c6ebbf7661b236ce44d352024c51741b08f9c`. The patch
SHA-256 is
`b3cbe9f85ad166ffff4dd86043870ba780e447240d63b6c48373a6d67741a8d6`.
Vanilla llama.cpp is pinned to `8954f194837d2ec4d5f67f20c81b84abe63883ba`.

## Comparisons

| Workload | Conditions and decision |
| --- | --- |
| Cache-off mixed arrivals | Four balanced fresh-process rounds on hybrid-dense Qwen3.5-4B. Primary metric is the late short request's TTFT. A signal is a median paired reduction of at least 10%, with improvement in every completed round. Report the long-prompt TTFT and whole-wave makespan too. |
| Cache-on mixed arrivals | Four balanced fresh-process rounds on the same model. Primary metric is the decode anchor's longest stream-event gap. A signal is a median paired reduction of at least 20 ms, with improvement in every completed round. Report request p95 TTFT, makespan and output agreement. |
| Resident churn | One fresh process per arm on dense Qwen3-4B. Count immediate and post-churn hits and restored tokens as each engine reports them. Compare repeat output hashes with the initial seed. Timings are descriptive because the arms can perform different prefill work. |
| High offered concurrency | Run the pinned Thoughtworks workload at 64, 128 and 256 with 16 active lanes. Compare unchanged and patched Skippy on the registered Llama 3.2 1B model, with the same native runtime and warmup protocol. |

The timing replay uses four live lanes, a 32,768-cell aggregate KV budget,
F16 K/V, 512-token batches and microbatches, full Metal offload, identical
tokenized prompts and fixed requested output counts. Native settings are
checked in separate startup runs. The timed cells use external client
timestamps, nominal thermal pressure and a competing-process check. The
vanilla cache-enabled arm uses the same 2 GiB RAM cache configuration as the
previous study. The cache replay measures both engines' configured retention
policies. Generated text hashes establish exact agreement on these fixtures
where they match. Full task-quality equivalence is outside this campaign.

## Jobs

- Candidate build `job_21evz553b885jp3`, run `run_3spjavkv5tt1ykf`.
- Cache-off replay `job_pkhx4ckvxk2r29t`, run `run_n70x3ysbt2pknmj`.
- Cache-on mixed replay `job_ehz8c4zm4c9gaxc`, run `run_sa626m9ecwwhepx`.
- Thoughtworks manifest staging `job_bpmeef8ndcn0ztp` and `job_qt5crsyn89vzvte`.
- Thoughtworks executable and model preparation `job_c3vzezbb8k5meqt`.
- Dense resident churn `job_ccqc7gscbh9xza5`, run `run_zk44874x102h173`.
- Native Thoughtworks c64/c128/c256 `job_8tfyts6e7k97c00`, run `run_d8qr7zsmeaxn4za`.
- Strict c128 retry `job_phbxrkw8b4x302k`, run `run_5tvstp5578hhpav`.
- Vanilla churn recovery `job_q1f03vwd7d6c6g6`, run `run_beb4q1a19b58ngh`.
- Phase-gated native launcher `job_4nr9wqjtjvp6z0j`.
- Phase-gated native c128 `job_j95mck4sstkxdwa`, run `run_9rg848g9b58ca2a`.
- Initial phase-gated c256 `job_d24kdcfgck0m66n`, run `run_hvd74yweg1wvkqm`.
- Cooled c256 retry `job_sttrffzyy45s9ph`, run `run_3j1cg7dracnw6gt`.
- Descriptor-limit c256 retry `job_rmh455nww9dv9n2`, run `run_gmf245xxh2f14ah`.

## Cache-off mixed arrivals

Medians over four fresh runs per arm. Times are in seconds.

| Arm | Late short TTFT | First long TTFT | Second long TTFT | Anchor max gap | Wave makespan |
| --- | ---: | ---: | ---: | ---: | ---: |
| Skippy baseline | 7.570 | 3.656 | 7.324 | 0.496 | 9.808 |
| Skippy patched | **1.380** | 6.788 | 7.636 | 0.492 | 9.761 |
| Vanilla separate slots | 4.716 | 7.801 | 4.920 | 0.494 | 9.831 |
| Vanilla unified KV | 4.894 | 7.914 | 4.998 | 0.526 | 9.966 |

The late short request reached its first token before both long requests on
the patched build in all four runs. It came after both in three baseline
runs and between them in one. The four paired reductions were 80.0%, 81.3%,
73.0% and 82.4%. This clears the registered scheduler signal. The first
long request gave up some TTFT and the wave makespan stayed close.

Both long requests arrived while the anchor was decoding in all 16 cells.
Dispatch slipped by up to 151 ms, and in one cell of each vanilla arm the
late short request dispatched before the second long. All eight Skippy cells
retained the registered dispatch order. The vanilla timings describe the
observed waves, including the two arrival-order changes.

All 24 measured output hashes per Skippy build agreed. Vanilla separate slots
matched baseline Skippy on all 24. Vanilla unified matched on 23 of 24.
All 96 measured responses reached their requested token counts.

[Cache-off raw evidence](../../target/scheduler-fix-validation/lab-cold-artifacts/qualification.json)

## Cache-on mixed arrivals

Medians over four fresh runs per arm. Times are in seconds.

| Arm | Anchor max gap | Late short TTFT | Wave p95 TTFT | Wave makespan |
| --- | ---: | ---: | ---: | ---: |
| Skippy baseline | 1.181 | 1.552 | 7.669 | 10.048 |
| Skippy patched | **0.743** | 1.524 | 7.749 | 10.067 |
| Vanilla separate slots | 0.533 | 1.148 | 7.426 | 10.071 |
| Vanilla unified KV | 0.536 | 1.152 | 7.426 | 10.053 |

The patch reduced the longest pause by 421–449 ms in each pair, clearing
the registered 20 ms signal. It leaves about 0.21 s between patched Skippy
and either vanilla arm on this fixture. Time to first token at p95 rose
about 80 ms and makespan stayed close. All cells retained dispatch order,
with up to 151 ms of dispatch slip. Measured requests reported zero cached
tokens. The cache-enabled serving path is the mechanism this wave exercises.

The two Skippy builds matched on 15 of 16 outputs. The difference is the
anchor in the first pair. Both builds' anchor output also varied across
runs relative to the identical cache-off solo fixture. The cache-on timing
signal is descriptive pending broader output-equivalence checks.

[Cache-on raw evidence](../../target/scheduler-fix-validation/lab-cache-mixed-artifacts/qualification.json)

## Resident churn

The dense Qwen3-4B-Instruct-2507 replay used 4,096-token prefixes, four
resident entries and the original study's extra cold probes.

| Arm | Immediate hits | Hits after four novel prefixes | Tokens restored after churn |
| --- | ---: | ---: | ---: |
| Skippy baseline | 4/4 | 0/4 | 0 |
| Skippy patched | 4/4 | 0/4 | 0 |
| Vanilla separate slots | 4/4 | 0/4 | 0 |
| Vanilla unified KV | 4/4 | 0/4 | 0 |

All 19 measured output hashes matched across builds. The earlier compact
2,048-token result on a different local Qwen3 revision retained three
prefixes. The larger Mac Studio workload did not reproduce that gain.
The initial thermal guard stopped the vanilla unified arm during churn.
Both vanilla arms completed in the recovery with 105-second cooldowns.
Every immediate and post-churn repeat matched its arm's seed output.

An instrumented local replay reproduced the Mac Studio misses on the exact
2507 GGUF and matched the lab's input token hashes. The cold probe after
reuse arrived with 16,444 occupied cells in a 32,768-cell pool. Skippy's
default 16,384-token retention budget triggered eviction of a 4,111-token
entry for the 60-token retention overage. A paired diagnostic on the
patched build, with only the retention setting changed to unbounded,
restored one of four prefixes after churn. All output hashes matched
between those diagnostic runs. Physical capacity and whole-entry
eviction remain relevant in the larger workload.

[Dense churn evidence](../../target/scheduler-fix-validation/lab-cache-artifacts/qualification.json)

[Vanilla churn recovery](../../target/scheduler-fix-validation/lab-cache-vanilla-artifacts/qualification.json)

[Default retention trace](../../target/scheduler-fix-validation/lab-dense-local/exact-dense-trace-summary.json)

[Retention ablation trace](../../target/scheduler-fix-validation/lab-dense-local/unbounded-retention/exact-dense-trace-summary.json)

## Thoughtworks preparation

The native trajectory reader and manifest producer reproduced
`d0b0f913f3a3095453820eb0e3e1f58b102e25910ee1bc0da78d62ab0041ee15`
for the 256 prompts in the registered workload. The lab inventory found
`sessions.parquet` in the shared model store. The prepared run and cache
operator manifests were absent from the inspected roots.

The pre-extraction native launcher set a 131,072-token total as the
per-lane context. On a 16-lane standalone Skippy process that allocated
2,097,152 cells and caused an out-of-memory error in a local smoke test. The
launcher compatibility change explicitly sets 8,192 per lane and
131,072 total cells and enables flash attention for both Skippy arms.
Startup logs confirmed the intended pool, full Metal offload and unified KV.
The corrected native launcher completed 64 measured requests and 128
warmups on the local M5 Max. Those results are a launcher check.

[Native launcher patch](../../target/scheduler-fix-validation/lab-thoughtworks-build-artifacts/thoughtworks-launcher.patch)

[Pinned dataset](https://huggingface.co/datasets/thoughtworks/agentic-coding-trajectories/blob/cef72d1f4d0caabf85937adf8337a14b7522c782/sessions.parquet)

## Native Thoughtworks results

Llama 3.2 1B Q4_K_M through the native competitive cell runner, with 16
active lanes and the registered corpus. Both c64 arms completed every
warmup and all 64 measured requests at eight generated tokens each.

| Offered concurrency | Arm | p95 TTFT | Output tokens/s | Cached prompt tokens |
| --- | --- | ---: | ---: | ---: |
| 64 | Skippy baseline | 10.41 s | 48.70 | 216,952 |
| 64 | Skippy patched | 7.05 s | 71.11 | 229,914 |
| 128 | Skippy baseline | 19.64 s | 48.79 | 436,689 |
| 128 | Skippy patched | 16.56 s | 51.34 | 440,741 |
| 256 | Skippy baseline | 34.05 s | 59.62 | 899,200 |
| 256 | Skippy patched | 29.41 s | 69.05 | 917,624 |

The output hashes agreed on all 64 requests. Both arms received 250,192
prompt tokens. In this pair the candidate restored 12,962 more tokens,
so total prefill work differed. The c64 figures describe a single pair
and include that change in cache reuse.

At c128, both arms completed all 256 warmups and all 128 measured
requests at eight generated tokens each. Both received 500,384 prompt
tokens, and the patched build restored 4,052 more tokens. Output hashes
agreed on 127 of 128 requests, with a mismatch at request index 16.

At c256, both arms completed all 512 warmups and all 256 measured
requests at eight generated tokens each. The pair matched the model,
runtime, cell settings and prompt token counts. Each arm received
1,000,768 prompt tokens, and patched Skippy restored 18,424 more tokens.
Output hashes agreed on 255 of 256 requests, with a mismatch at request
index 227. Patched p95 time to first token was 13.6% lower and output
throughput was 15.8% higher in this single pair. These figures include
the change in cache reuse. Exact output parity remains open at c128
and c256.

The thermal guard stopped the initial c128 attempt. The first c128 retry,
with a 70-second cooldown between arms, completed the patched arm and
stopped the baseline arm when the host throttled. The subsequent
phase-gated c128 retry completed both arms. A local M5 Max c256
pair completed all 256 measured responses in each arm with 254 matching
output hashes. Its timings are excluded from the Mac Studio comparison.

The next native attempts insert checkpoints between groups of 32 warmup
requests. A checkpoint holds until nominal thermals. The measured wave
begins after a continuous 12 seconds of nominal thermals and the guard
requires nominal pressure throughout the measured wave. The warmup input,
order and count are the same for both builds. The checkpoint logic passed
a local c64 smoke test.

At c256, the first phase-gated baseline completed 512 warmup requests,
then reached thermal pressure level 2 before the measured wave started.
The cooled retry added a 15-second nominal pause after each group of
32 warmup requests and required 75 continuous nominal seconds before
measurement.

The cooled baseline entered the timed wave and completed 243 of 256
requests. Seven failed with open-file exhaustion, five with connection
errors, and one was canceled. The successful retry used a verified soft
limit of 2,048 open files per process and the same checkpoint schedule
for both arms. Each arm passed all 17 checkpoints and completed under the
nominal measurement guard. The incomplete baseline has no comparable
timing.

[Initial Thoughtworks evidence](../../target/scheduler-fix-validation/lab-thoughtworks-artifacts/thoughtworks-qualification.json)

[Mac Studio c128 evidence](../../target/scheduler-fix-validation/lab-thoughtworks-128-artifacts/thoughtworks-qualification.json)

[Mac Studio c256 warmup-only attempt](../../target/scheduler-fix-validation/lab-thoughtworks-256-partial/thoughtworks-qualification.json)

[Mac Studio c256 descriptor-limit failure](../../target/scheduler-fix-validation/lab-thoughtworks-256-cooled-artifacts/thoughtworks-qualification.json)

[Mac Studio c256 evidence](../../target/scheduler-fix-validation/lab-thoughtworks-256-artifacts/thoughtworks-qualification.json)

[Local c256 evidence](../../target/scheduler-fix-validation/thoughtworks/local-native-pair-256/qualification.json)

## Lab occupancy

The campaign used 94.04 minutes of Mac Studio occupancy, including
preparation, cooldowns and failed attempts. The successful c256 retry used
20.98 minutes.

[Lab occupancy ledger](../../target/scheduler-fix-validation/lab-occupancy.json)
