# Scheduler and KV investigation on Metal

The question is where Skippy spends extra time relative to vanilla llama.cpp
under concurrent decoding, mixed-length arrivals and KV reuse. Earlier tests
covered single-stream inference and a Skippy-only mixed-scheduler comparison.
The source-pinned binaries and offline GGUFs carry
forward from the [baseline](REPORT.md). The user authorized up to 60 additional
minutes of host occupancy at $0 on macstudio-2l0352.

## Findings at a glance

- **Mixed-load scheduling adds seconds of tail latency.** In three
  hybrid-dense repeats, median late-short TTFT was 7.459 s on Skippy,
  4.695 s on vanilla slots and 4.813 s on vanilla unified. Linked iterations
  show the short request waiting for the two earlier long prefills.
  The attention-MoE trace shows the same ordering symptom.
- **Prefix caching changes which request waits.** In a fresh cache-enabled
  replay, Skippy's late-short TTFT was 1.397 s, while the active decode
  stream paused for 1.272 s. Both vanilla arms paused for about 0.535 s.
  A Skippy trace reproduced the gap and linked it to two consecutive
  prefill batches ahead of the next streamed token.
- **Indexed attention controls physical-span growth.** The dense cold
  probe after churn addressed 4,352 padded positions with indexed
  attention and 28,160 with ordinary attention over the same Skippy pool.
  Immediate prefix hits were intact. Four fresh prefixes displaced every
  retained original in the tested four-entry policy.

| Arm | KV layout | Purpose |
| --- | --- | --- |
| Vanilla slots | Force `--no-kv-unified` | Primary baseline with separate slots |
| Vanilla unified | Force `--kv-unified` and cap each slot at 8,192 | Isolate the layout change within vanilla |
| Skippy | Shared pool, default attention path | Measure the implementation in use |
| Skippy ordinary | Shared pool, `--kv-row-attention=false` | Test the read path where trace evidence warrants it |

Use four configured lanes with room for 8,192 tokens per lane and 32,768
aggregate cells, full Metal offload, F16 K/V and 512-token batches/microbatches.
The offered workload uses concurrency 1, 2 and 4 without changing allocation
between waves. No speculation. Vanilla polling stays off as in the qualified
host protocol. Verify realized settings with a separate startup trace when
normal logging omits them. Predictable, fixed-arrival synthetic text is
tokenized and hashed at each engine. Verify generated counts and record output
hashes. Full task-quality equivalence is outside this diagnostic screen.

1. **Cold scheduling.** Replay homogeneous short bursts, long-only controls
   and staggered 4K prefills while an anchor is decoding. Measure client
   TTFT, completion time, cohort makespan and the anchor's maximum and p95
   nonempty stream-event gap. Establish actual overlap from event times.
   Count-normalized TPOT uses the output token count, while event gaps may
   represent more than one token. Treat at least a 10% difference or 20 ms
   of additional anchor gap as a signal to investigate, with repeat runs
   required before treating any timing signal as reproducible.
2. **Scheduler attribution.** Keep the timed replay free of debug tracing.
   Repeat separately with Skippy iteration and unsplit GPU spans and
   vanilla batch/slot logs. Correlate phase mix, attention extent, occupancy
   and GPU-active intervals with the fixed arrivals. A layout-only contrast
   comes from the two vanilla arms. The unified Skippy versus unified vanilla
   contrast still includes differences in server scheduling and native graphs.
3. **Attention and KV lifecycle.** Enable in-memory reuse separately. After
   seeding distinct prefixes, replay those inputs concurrently and test novel
   cold work before/after retention and churn. Track actual reused tokens
   and output agreement on repeats. Compare cache cost alongside cache work.
   Run ordinary versus indexed Skippy independently of vanilla's layout test
   if there is evidence tying the observed slowdown to attention extents.
   This synthetic churn records the fragmentation that occurred in the replay.
4. **Coverage.** Pilot the dense and recurrent 4B models first. Use measured
   run time to admit repeated pairs or the two MoE models within the approved
   host allowance. Preserve partial or failed evidence and avoid pooling
   traced and untraced timings.

## Verified before new hardware results

- The offline scheduler suite passed 38 unit tests and 8 integration tests,
  including bounded prefill, mixed work, resident-affinity and overload
  behaviors. It uses simulated iteration costs.
- The source sets recurrent models to one prefill sequence per iteration and
  disables mixed prefill/decode for them. The earlier Studio54 study recorded
  a 12.1% throughput loss with a recurrent mixed probe and used the guard in
  the final candidate. This is a specific policy to check against current
  vanilla, whose continuous batching adds pending prefill after decode.
- Prior CUDA pressure evidence found ordinary attention time grow as idle
  prefixes filled the unified pool and remain stable under indexed reads.
  The older Spark result establishes the mechanism on that source and
  workload. It leaves the current Metal path and hit/eviction policy open.

[Scheduler source](../../crates/skippy-serving/src/frontend/iteration_scheduler.rs)
· [historical mixed run](../../docs/MIXED_PREFILL_DECODE_STUDIO54.md)
· [indexed attention history](../../docs/design/INDEXED_ATTENTION_STATUS.md)
· [pinned vanilla scheduling source](https://github.com/ggml-org/llama.cpp/blob/8954f194837d2ec4d5f67f20c81b84abe63883ba/tools/server/server-context.cpp#L3656-L3669)

## First cold replay

The first admitted pilot returned all 84 measured responses and six warmups at
the declared token counts with zero reported cache hits. Both late long requests
arrived during anchor decode on every four-way mixed wave. The initial submission
stopped after its first arm because normal logging hid required startup fields.
The repaired pilot kept normal logging and delegated full native setting checks
to instrumented starts. Its anchor prompt varied between the serial and mixed
waves. Cross-engine comparisons within a wave used the same prompt. The
registered repeat uses one identical anchor prompt across all three waves.

### Hybrid-dense model, one four-way mixed wave

| Client observation | Vanilla slots | Vanilla unified | Skippy |
| --- | ---: | ---: | ---: |
| Late short request TTFT | 4.397 s | 4.345 s | 7.656 s |
| First long arrival TTFT | 4.617 s | 4.689 s | 3.658 s |
| Second long arrival TTFT | 7.804 s | 7.739 s | 7.330 s |
| Anchor maximum stream-event gap | 511 ms | 519 ms | 491 ms |
| Cohort makespan | 10.094 s | 9.937 s | 9.795 s |

The tail for late short work warrants investigation even though the cohort
completed sooner. Vanilla's KV-layout switch did not produce that ordering
tradeoff on this wave. All arms had roughly half-second anchor stalls on both
4B mixed traces, compared with roughly 11–16 ms in the serial short controls.
The cross-arm stall differences are small compared with the shared mixed-load
effect. These are descriptive pilot values.

Evidence: `job_vs5f93sjaezm3wb`, `run_cvztqh9f34vaama`.

### Dense KV layout control after prefix churn

The trace-fidelity pass verified binary identity, four slots,
8,192 tokens per slot, batch and microbatch 512, full GPU offload, flash
attention and F16 K/V for both vanilla arms. Their startup logs reported the
same 4,608 MiB K/V buffer, partitioned into four 8,192-cell streams in
vanilla slots and one 32,768-cell stream in vanilla unified.

| Four 4,096-token prompts | Vanilla slots | Vanilla unified |
| --- | ---: | ---: |
| Immediate exact repeats with 4,095 tokens reused | 4/4 | 4/4 |
| Same repeats restored after four novel prefixes | 0/4 | 2/4 |
| Repeat output hashes equal to initial seed | 8/8 | 8/8 |

This is a cache-lifecycle difference for the pinned 2 GiB vanilla RAM-cache
configuration. Under unified KV, the native server can free live cells from
idle slots by saving their prefixes to RAM. With separate slots, that live
capacity cannot be reassigned. A cache-policy comparison should report the
reused work alongside latency. The partial cache job preserved both complete
vanilla arms and aborted its Skippy arm when the host entered heavy thermal
pressure. Raw logs for that arm remain private and its samples are excluded
from direct comparison.

Evidence: `job_32enyvj2kts8gvk`, `run_mhzs6kb67528400`.
[Vanilla idle-slot handling at the pinned revision](https://github.com/ggml-org/llama.cpp/blob/8954f194837d2ec4d5f67f20c81b84abe63883ba/tools/server/server-context.cpp#L1529-L1541)

## Scheduler mechanism on hybrid dense

The instrumented four-way replay linked all four client requests to native
scheduler iterations. This identifies the work selected ahead of the late
short request. Timings in this section are trace diagnostics.

| Request | Arrival target | First prefill iteration, elapsed | Prefill iterations |
| --- | ---: | ---: | ---: |
| Short decode anchor | 0 s | 0.007 s | 1 |
| First long request | 0.650 s | 0.705 s | 8 |
| Second long request | 0.750 s | 4.387 s | 8 |
| Late short request | 0.850 s | 8.108 s | 1 |

The late request took about **0.005 ms** through the frontend admission phase.
Its first prefill iteration began about **7.26 s after dispatch**. Each long
request received eight 512-token prefill iterations before the scheduler
selected the late short request. The trace records 18 prefill-only and 127
decode-only iterations, with no mixed iterations. The anchor continued decoding
between prefill turns. Skippy's scheduled iteration overhead totals 6.77 ms on
this wave. Native Metal execution covers 9,482.77 ms of the 9,844.36 ms client
window, about 96.3%, and no dropped-event counters were observed. The active
attention extent reached 4,352 cells with row lists, while occupancy reached
9,071 cells in the shared pool.

These observations explain the delay as **head-of-line waiting between
prefills**. The active sequence walk repeatedly selects the earlier prefill
under the recurrent limit of one prefill sequence per iteration. GPU work for
the earlier prompts occupies the time. Instrumented vanilla late-short TTFT
varies with its slot and batch ordering, from 1.18 s with separate slots to
4.85 s with unified KV in this capture. Skippy took 7.45 s in the same trace
replay. The request-level trace identifies the mechanism. The cold repeat reproduced the delay outside tracing.

All three starts verified a 1,024 MiB F16 attention-KV allocation, a
32,768-cell aggregate pool, 512-token batch and microbatch, flash attention
and full GPU offload. Skippy reserved eight native sequence IDs for four
serving lanes, compared with four in vanilla.

Evidence: `job_j8xmsy2kr4whw9z`, `run_0zte82egvec363k`.
[Recurrent scheduling configuration](../../crates/skippy-serving/src/frontend/iteration_scheduler.rs#L1660)
· [Prefill selection](../../crates/skippy-scheduler/src/engine.rs#L133)

### Attention MoE screen

The pinned 30B attention-MoE model also showed a long wait for the late short
request in its one untraced four-way replay. TTFT was **8.058 s** on Skippy,
**4.595 s** in vanilla slots and **4.566 s** in vanilla unified. Cohort
makespans were 10.410 s, 10.349 s and 10.869 s, respectively. This establishes
a second observed scheduling symptom on a non-recurrent model. The recurrent
trace cannot establish its exact cause. The run spent time cooling and reached
its lease limit while attempting hybrid MoE. Only the three complete
attention-MoE cells enter the comparison.

Evidence: `job_k0w1n5y1nnavfr1`, `run_7fvf1ktak58zskm`.

## Attention extent and cache reuse under churn

Dense Qwen3-4B used the same 4,608 MiB attention-KV allocation across both
vanilla arms and both Skippy arms. Within Skippy, the ordinary control changed
only the indexed-attention switch. Trace instrumentation and the 10-second
inter-wave cooldown were the same for those two arms. The cold probe requests
are distinct 4,096-token prompts and each reports zero cache reuse.

| Probe | Indexed extent | Ordinary extent | Indexed GPU active | Ordinary GPU active |
| --- | ---: | ---: | ---: | ---: |
| Cold before seed | 4,352 | 4,608 | 3,987 ms | 3,969 ms |
| Cold with retained prefixes | 4,352 | 21,248 | 3,986 ms | 4,136 ms |
| Cold after churn | 4,352 | 28,160 | 3,990 ms | 4,183 ms |

Extent is the largest attention extent recorded by the native command metadata.
GPU-active time is the union of all unsplit command execution intervals in
the client request window. It includes model work beyond attention. The
ordinary arm grows with the physical span of the unified pool. The indexed
arm bounds that extent to the request-local rows in this replay. With churn,
ordinary gained about 214 ms of GPU-active time over its first cold probe,
while indexed stayed within 4 ms. This is evidence to preserve indexed reads
on this workload. It is a trace-time mechanism comparison on one model.

Both Skippy arms reused 4,095 of 4,096 tokens on each of four immediate
repeats. After four novel prefixes, eight repeat attempts across those two
arms reused zero original tokens. Repeat output hashes matched the original
responses in every case. The indexed path fixes physical-span scaling for
this probe while the four-entry retention limit still permits eviction and
recomputation. Vanilla unified retained two of four prefixes using its
separately configured 2 GiB RAM cache. Those hit rates describe the tested
retention policies and budgets.

Evidence: `job_84rnpttrx8bwn85`, `run_h14nf4qmbnn2j9t`.

## Independent Metal timeline check

Instruments recorded CPU samples and process-attributed Metal activity for
all three hybrid-dense arms, with all twelve traced requests entirely within
their captures. During the late short request's send-to-first-event interval,
the attached-process GPU was idle for 1.2% of the separate-slot run, 1.1% of the
vanilla unified run and 2.1% of the Skippy run. This independently supports the
linked-iteration observation that Skippy's delayed short request waited
behind GPU work. CPU sample weights measure CPU activity across threads.

Evidence: `job_n9ve84eep3fr0qe`, `run_d66vn630gawj3vw`.

## Unprofiled reproduction on hybrid dense

Three fresh, rotating rounds put every layout first, second and third once.
They replayed one identical anchor prompt in the serial and mixed waves.
Each process warmed with a separate short prompt. The focused retry used a
30-second process cooldown and a six-second gap between waves after the
broader repeated replay stopped at the thermal gate. All nine cells passed
the native thermal and process checks. All 54 scored requests completed
the declared work, reported zero cached tokens and matched output hashes
across arms and rounds.

| Median across three rounds | Vanilla slots | Vanilla unified | Skippy |
| --- | ---: | ---: | ---: |
| Serial short TTFT | 0.373 s | 0.370 s | 0.366 s |
| Late short request TTFT with mixed traffic | 4.695 s | 4.813 s | 7.459 s |
| Anchor maximum stream-event gap | 493 ms | 525 ms | 497 ms |
| Cohort makespan | 9.813 s | 9.993 s | 9.802 s |

Late-short TTFT was 4.518–4.757 s with separate slots, 1.175–4.935 s with
vanilla unified and 7.427–7.532 s with Skippy. Skippy was slower for that
request in every matched round, against both vanilla layouts. The short
serial controls and similar cohort completion times show how a throughput
aggregate misses the delay. All arms stalled the anchor for short bursts
while processing 4K prefills. The large Skippy-specific effect is the order
in which its prefill work reached the device.

Evidence: `job_x14ymb9qdszh41k`, `run_ytsq0gsh3pcmxdb`.

## Mixed traffic with prefix caching enabled

A fresh hybrid-dense cohort starts with novel prompts and the prefix store
enabled in each arm. All twelve scored requests report zero reused tokens.
Skippy uses the feature generation path when prefix caching is enabled.

| One unprofiled wave | Vanilla slots | Vanilla unified | Skippy |
| --- | ---: | ---: | ---: |
| Late short request TTFT | 1.181 s | 1.172 s | 1.397 s |
| Anchor maximum stream-event gap | 535 ms | 535 ms | 1,272 ms |
| Cohort makespan | 10.067 s | 9.989 s | 10.082 s |

This mode changes who waits. The large cache-off late-prefill delay is smaller
in this replay. The already-decoding request instead sees a longer gap. This
is one descriptive three-arm control on four novel prompts. A separate
Skippy trace checks the work inside its largest delivery gap.

Evidence: `job_m1ppevg32v18399`, `run_x0gh4d0wfyj7hb7`.

## Attention MoE trace confirmation

All four requests linked to scheduler iterations on Qwen3-Coder-30B-A3B.
The late short took 8.095 s to first token and its prefill first ran at
8.699 s on the wave clock. The first long began prefill at 0.721 s and the
second at 4.668 s. The late short shared only the second long's final prefill
batch. The trace records 17 prefill-only steps, one mixed step and 126
decode-only steps. Device activity covered 10.060 of the 10.573 s client
window. Frontend admission for the late short was 0.006 ms. The earlier
unprofiled three-arm screen had identical generated text for all scored
requests.

This reproduces the ordering delay on a non-recurrent model where a mixed
iteration actually ran. It also bounds the observed amount of mixing.
Choosing the specific calibration or timing guard that limited further mixed
iterations would require the corresponding scheduler state.

Evidence: `job_6x6x22kqx3j14pt`, `run_1b74nrgsm8ecrb1`.

## KV placement evidence from the dense trace

Native row-list traces show up to nine separate physical runs for a query
slice during churn and repeated requests. Cold post-churn requests observed
up to 16,452 occupied cells in both Skippy arms, while ordinary attention
addressed a 28,160-cell physical span. The indexed path addressed at most
4,352 padded positions with at most 4,119 actual rows for that probe.
This distinguishes occupancy, physical span and the useful context.

Native graph input preparation increased from 7.66 to 14.85 ms in the
ordinary cold probes, versus 8.18 to 9.00 ms with indexed attention.
These are clipped trace interval unions. Native cache lookup decisions
record four resident hits on immediate replay and four misses after churn
for each Skippy arm. These decisions corroborate the API's token counts.

Evidence: `job_e02s605jaq572be`, `run_908fvkk87zym7zz`.

## Cache-enabled decode gap attribution

A separate feature-path trace reproduced a 1.187 s gap on the already-decoding
short request. Inside it, Skippy ran two consecutive single-sequence
512-token batches, followed by a 258-token batch serving three sequences.
Speculation was disabled. The two single-sequence batches occupied about
908 ms, and the following batch occupied about 279 ms. The GPU was active
for 1.162 s of the gap. An exact-state checkpoint operation started within
the final millisecond of the gap. The bulk of the gap came from launched
prefill work ahead of the next anchor token.

The cache-off scheduler trace exposes prefill fairness. The cache-enabled
trace exposes decode service under prefill pressure. The two paths are
separate control points. A scheduling change should test both the late short
request's TTFT and the anchor's longest gap in the same replay.

Evidence: `job_gprs0hfdap8xrm8`, `run_55bkvdmt6xy6fch`.

## Hybrid MoE recovery

The narrowed hybrid-MoE replay completed both vanilla arms before its lease
expired. A fresh Skippy-only job then replayed the same fixture digests and
cooldowns. All three complete cells passed thermal checks. All 18 scored
requests matched input/output counts and generated text, and their reported
cached-token counts were zero.

| One unprofiled wave across two leases | Vanilla slots | Vanilla unified | Skippy |
| --- | ---: | ---: | ---: |
| Late short request TTFT | 4.383 s | 4.218 s | 7.007 s |
| Cohort makespan | 9.429 s | 9.612 s | 9.404 s |
| Anchor maximum stream-event gap | 526 ms | 556 ms | 465 ms |

This also exhibits the late-prefill delay. Split-lease collection provides a
descriptive comparison. The first differing operator for the previous
hybrid-MoE correctness fixture remains open.

Evidence: `job_yqqpn21r0assggf`, `run_pqky5tv10ye7196`, and
`job_6zkk5b0aqyc67ty`, `run_1arg08jew5s7w2q`.

## Validation and scope

- The fixed-work cold screens cover all four Q4_K_M models. Three
  matched repeats were completed on hybrid dense. The two larger models
  contribute one unprofiled three-arm observation each.
- The retention/churn mechanism was measured on dense Qwen3-4B with a
  synthetic four-entry policy and distinct 4K prefixes. Sustained production
  traffic, larger contexts, and durable restores remain outside this dataset.
- The traced and untraced waves retain separate evidence records. Raw
  Instruments documents, native logs and XML exports remain on the lab Mac.
- The offline scheduler test command
  `just with-lld cargo test --locked -p skippy-scheduler --offline`
  passed 38 unit tests, eight integration tests, and the doc-test phase.
  The Python replay and analysis scripts passed compilation. The model-backed
  screens above use request counts, usage and output hashes from the actual
  engines.
- The campaign used **54.46 of 60 authorized host minutes** and **$0**.
  Thermal stops, lease recoveries, artifact hashes and job receipts are in
  the [campaign ledger](scheduler-kv-campaign.json).

Regenerate the combined metrics locally with:

```sh
python3 scripts/profile-kv-scheduler-summary.py \
  target/kv-profiling/scheduler-*/replay.json \
  --output target/kv-profiling/scheduler-summary.json
```
