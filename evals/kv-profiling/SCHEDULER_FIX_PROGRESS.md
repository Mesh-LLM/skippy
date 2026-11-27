# Scheduler fix and retest progress

## 2026-11-27: Qwen3.6 first paired c1 quality remains unproven

The current-source Qwen3.6 Skippy cell `job_h7m1kpd0xhw8s2s` completed
512/512 and passed independent exact-pin, source, full 41-layer Metal,
F16 KV, 16×8192 context, batch/microbatch 512, cache-off and cooled nominal
thermal audits. It scored **356/512** correct. Both vanilla layouts scored
**353/512** and reproduced identical raw text on every task.
The registered scorer, replayed exactly from saved raw responses, finds
**three adverse** Skippy answers among 353 vanilla-correct tasks:
`assignment-004`, `assignment-026`, and `assignment-103`. Each is a concrete
wrong integer rather than a parser or incomplete-output artifact. The exact
one-sided 95% upper bound is **2.18175%**, crossing the strict 2% limit:
**UNPROVEN** against both vanilla layouts. Skippy correctly answered six
tasks vanilla missed, but those wins do not cancel adverse items under the
registered rule. Raw text differs on 16 tasks.

The [compact first-cell archive](pr-evidence/task-quality-v3-qwen36-c1-first-cells.tar.xz)
(SHA256 `3e3d695e08ca19adc19a0c23a819168ecd5239a5269cd452de173e873eb3f573`)
contains all three raw score inputs, scores, independent audits, both
comparisons and source/job/spec/artifact identities; its
[summary](pr-evidence/task-quality-v3-qwen36-c1-first-cells-summary.json)
has SHA256 `11877a2cabcf3a7a1a6ff0a502496c6f090e5d2f118c21d6917c0eb8dea1540e`.
The ignored local builder reproduced both hashes twice. The presealed
reverse-order A/A and candidate repeats are pending. The archived confident
Qwen3.6 numerical divergence also remains unresolved; this task score alone
cannot clear it. Loaded quality, current-source fixed-output timing and the
four-model no-regression claim remain **UNPROVEN**.

## 2026-11-27: Coder first paired c1 quality comparison audited

The current-source Coder Skippy cell `job_z2jb5x6k3tgc7vf` completed 512/512
requests and passed the independent exact-pin, source, full 49-layer Metal,
F16 KV, 16×8192 context, batch/microbatch 512, cache-off and cooled nominal
thermal audit. It scored **313/512** correct. Both separately audited vanilla
layouts scored **314/512** and produced identical raw text on all 512 tasks.
The registered scorer, replayed from saved raw responses, finds two adverse
Skippy tasks among 314 vanilla-correct tasks (`assignment-114` and
`ordering-013`). Its exact one-sided 95% upper bound is **1.99136%**, narrowly
below the strict 2% margin, so this **first paired comparison passes** against
both layouts. Candidate raw text differs from vanilla on 29 tasks; those
exact-output discrepancies remain visible.

The [compact first-cell evidence](pr-evidence/task-quality-v3-coder-c1-first-cells.tar.xz)
(SHA256 `9df7e86f57a4a8f2fc782543643e78fd33ed7612b7f94084b9723e6bf694cd90`)
contains all three raw score inputs, scores, independent audits, both
comparisons and a manifest of job/spec/source/artifact hashes. Its
[summary](pr-evidence/task-quality-v3-coder-c1-first-cells-summary.json)
has SHA256 `ce19fe2727017d99dc9ef1bf67e4c0850e9b190948a202f3d8a5ab327c86a27f`.
The ignored local builder reproduced both hashes twice.

This is **not yet a Coder c1 gate pass**: the three presealed, reverse-order
fresh-process A/A and candidate repeats have not completed. Loaded quality,
current-source fixed-output timing, and four-model non-regression remain
**UNPROVEN**. The original exact-output and latency failures are preserved.

## 2026-11-27: three more c1 reference baselines audited

The cooled, source-pinned vanilla separate-slot c1 cells for the other three
CI models completed 512/512 requests each. Independent audits verified the
exact pinned model and server, 512 effective input tokens, 16×8192 context,
F16 KV, batch/microbatch 512, full Metal offload, caches off, seven ≥120-second
pauses and nominal thermal boundaries. Coder scored **314/512**, Qwen3.6
**353/512**, and Flash **387/512** correct, each above the registered
200-correct comparator prerequisite. The [compact evidence archive](pr-evidence/task-quality-v3-other-model-c1-baselines.tar.xz)
(SHA256 `6eaaaeba89f24e339aee6b931a8e8d2367ce4a28ff8fb31f48b10f5ed0f24bcf`)
contains the raw score inputs, scores, independent audits, and job/spec/source
hashes; its [summary](pr-evidence/task-quality-v3-other-model-c1-baselines-summary.json)
has SHA256 `ca775ff6f4e0852c9b2742642c8c83319a7db07226fa2ef2bce8ee342e8a328c`.
The ignored local builder produced the same archive and summary hashes twice.

The other two first-arm cells per model and all nine presealed reverse-order
fresh-process repeats are queued or running on the authorized $0 Mac Studio.
These three reference baselines alone provide **no candidate quality verdict**.
Loaded quality, current-source fixed-output performance, and the confident
Qwen3.6 numerical discrepancy remain open. The four-model no-regression claim
is **UNPROVEN**.

## 2026-11-27: one-off scheduler tooling removed from the PR

The owner asked to remove profiling scripts that are not durable tools and to
keep a local backup. Before pruning, 121 files were copied into the ignored
`target/scheduler-fixes/backups/profile-scheduler-sources-20261127.tar.gz`
(SHA256 `bb5c99bd0a30abfb3f047642bfe31723ce04b6b686de4a78f502bc681ba60d67`).
The archive was reopened and every file checked against its byte size and
SHA256. Each file was also checked against immutable source commit
[`75c04415`](https://github.com/Mesh-LLM/skippy/tree/75c04415c299bb30858639a92880dec77f28f351/scripts).
The [source hash manifest](pr-evidence/scheduler-tool-source-hashes.json)
records all 121 paths, the 118 one-off files removed from the PR, and the
three files retained for the registered task-quality work. The sealed JY
payloads, original failure logs, audited result snapshots, and four PR tables
remain unchanged. The backup is local and intentionally ignored by Git.

## 2026-11-27 00:10 UTC: Coder balanced screen audited; tooling archived

The sealed $0 Coder screen `job_trqshv7hbbqdmzv` completed on
`macstudio-2l0352`. Its [independent audit](pr-evidence/coder-current-balanced-audit.json)
verified the exact CI GGUF and original fixture, sealed source/binary/native
bundle identities, full 49-layer Metal offload, 16×8192 F16 KV, batch and
microbatch 512, cache-off launches, nominal thermal boundaries, all 16 cells
and 192/192 requests. Exact text matches both vanilla layouts across the four
balanced rounds, with no cross-round output drift. The source was the sealed
pre-port candidate, so this does not qualify the upgraded `main` source.

At the registered single-user workload, patched Skippy measured TTFT p95
410.67 ms, E2E p95 759.69 ms, TPOT p95 11.44 ms and 42.61 output tok/s.
Vanilla separate slots measured 403.93 ms, 762.08 ms, 11.80 ms and
42.77 tok/s; vanilla unified measured 403.84 ms, 800.51 ms, 12.99 ms and
40.41 tok/s. Patched Skippy passed the 2% paired non-inferiority checks
against unified. Against separate slots the E2E p95 paired interval was
0.9668–1.0237, crossing the 1.02 adverse bound; that comparison is
**UNPROVEN**. All four patched rounds passed the separate absolute TTFT/TPOT
SLO at this single-user load. This result does not repair the Coder c256
capacity SLO failure or establish a four-model non-regression claim.

Spec SHA256 `5ed0241dd4743447ccaad72c12df3f2a07237391c599a7c437ef588c8d15a204`;
fetched suite `bea3ccedb0069f15f1da962ea6079f1706a84f410f4f1447b96c17763d557678`;
archive `9ce772bf8edd2b72db6f5bc075ec0d16aba53424b3a63febb888bbffe6d258ab`;
audit `89887766ba7354de92a460da3c3996b2b9404778e89d8e0d2daa9888122714ae`.
The auditor initially rejected vanilla preflight logs because they reported
both aggregate and per-slot contexts; its exact expected context set now
checks both values. The failed audit invocation is retained in this record,
and the corrected audit passed without changing run data or settings.

The profiling-tool inventory found 119 `profile-scheduler-*` files. Sixty-six
completed one-off sources with no scanned active/spec/document references were
moved byte-for-byte to [the historical archive at commit `75c04415`](https://github.com/Mesh-LLM/skippy/tree/75c04415c299bb30858639a92880dec77f28f351/scripts/scheduler-archive),
with individual SHA256 values in its manifest. The active qualification and
PR audit entry points remain at their original paths. Historical sealed job
payloads preserve the original executable layout for exact replay. Python
compile, the current Coder audit, and the four-table timing recomputation
passed after the move. The archive is source evidence, not a current test gate.

## 2026-11-26: Coder balanced screen staged, model submission pending

The selected current-candidate Coder serial screen passed 64/64 responses and
its registered exact comparisons. The four-round balanced screen has three
sealed stage jobs queued: `job_6ry23tkymg86tdk`, `job_x58mxyreh6b17zx`, and
`job_471dt2e1xkzjfv7`. No model job has been submitted. The stage specs and
payload retain their hashes after correcting the model job cap to 21,600
seconds and the declared count to 192 requests (`coder-current-screen-v1.spec.json`
SHA256 `5ed0241dd4743447ccaad72c12df3f2a07237391c599a7c437ef588c8d15a204`).
The original 120-second nominal cooldown, $0 limit and zero retries remain.
Verify all three stage receipts and payload chunks before model submission.
This screen can test Coder timing and registered exact outputs; it cannot clear
the other three models or the separate 30-second TTFT SLO.
The independent `profile-scheduler-current-screen-audit.py` is prepared;
its sealed-stage/payload, safe-archive extraction, and non-inferiority-bound
self-checks passed locally. It checks the exact CI shard, fixed native bundle,
192 request schedule, cross-round output stability, both vanilla comparisons,
paired 95% non-inferiority bounds, and the separate absolute SLO. It has not
yet audited a current Coder timing run.
At the latest JY check the three stage jobs remained queued, while the pinned
host agent `agt_e85gsewhhp1hd6p` reported an unrelated Llama Upgrade CI build
(`job_qey2w7862s5vh4w`) as its active run. No unrelated job was interrupted.
An offline check of the archived current-candidate Metal pipeline logs narrows
one proposed diagnostic: Qwen3.6 Skippy and vanilla unified both compile
`rows=1`, yet their selected text differs; vanilla separate slots uses
`rows=0` but agrees with vanilla unified. Flash uses `rows=0` in all four
arms while its `load-0001` text still differs. Row-list activation alone does
not explain either cross-engine mismatch, so disabling it is not treated as a
fix or a qualification result.

## 2026-11-26 04:03 UTC: Coder current serial screen passed

Versioned Coder job `job_c0rr7k9nyrer4aj` succeeded at $0. The
[independent audit](pr-evidence/coder-current-candidate-audit.json) verified
64 responses across eight fresh processes, the exact CI shard and archived
fixture, original/combined binary and native identities, full 49-layer Metal
offload, F16 KV, 16×8192 context, native batch/microbatch 512, cache-off
launches and counters, nominal thermal boundaries, response hashes, and
complete token usage. Both selected prompts (`load-0001`, `short-9`) match
both vanilla layouts and repeat identically across both Skippy arms, both
process orders and ordinary/logprob modes. All eight selected cross-engine
normal comparisons pass, with no first token divergence.

The two earlier Coder attempts remain failed **harness** jobs, not model
failures. This v3 pass is limited to selected serial prompts; it does not
clear the prior c256 TTFT p95 >30 s capacity failure, current-candidate
single-user timing, balanced four-round load, or the four-model claim.

Spec SHA256 `e28cf2d10d8d088e1619f18ecdf35c097a3d112d279c1973b71ca34a1b704bba`;
report `bd2c38c847fa53796db266e7f62d563700ab923dd809930e70fae38e66978a98`;
archive `d4acf4301139e46b4868c30b019d85af5d309308b6335a91ce121e34e21f6f34`;
composition `8b7d95fc3ec28095d99af9a2659955f530cda2bd43b79fed48f08686b2f0b174`;
audit `e9878d75846cea421eee21288053c0bae93a8caefa8b6a23e79a972ec431820a`.

## 2026-11-26 03:43 UTC: Coder selected replay harness repaired

The first Coder current-candidate job `job_8mjr45cadydxmhb` failed before
generation with `KeyError: 'expected_bytes'`. Its historical single-shard
suite record has a name and SHA but omits size; the independently verified
admission record and exact CI pin include that size. The failure remains in
the ledger with original spec SHA256
`48b46ab2b3334d084d715beb3bf6aa1bfee008818a7c971b7c8620964ef5e8c4`.
The v2 harness verified the resolved shard's SHA, basename and byte size
against the pin before filling the report's size field. Versioned v2 spec SHA256
`c28bd489cdcd4b3e5f33575c0b0e359c77805e68ee11c5b0364f9a3dbedfe319`
retained all model, binary, native, request, cache, Metal and thermal settings.

The v2 job `job_5nanbzjty7gzdr6` also failed before generation: the control
script rejected Coder's archived fixture SHA
`cfdb255ba1b2848f97a4a5ec10808de398bab60c38736f396e933d0a81c967e6`
because it hardcoded the Qwen3.6/Flash fixture. Its failed log remains
preserved. The v3 harness passes the archived fixture SHA explicitly, with no
fixture or inference setting change. Sealed v3 spec SHA256
`e28cf2d10d8d088e1619f18ecdf35c097a3d112d279c1973b71ca34a1b704bba`
retains the selected 8-process/64-response screen. Both v3 stage receipts
matched the sealed chunk hashes; job `job_c0rr7k9nyrer4aj` was submitted
at $0 with no retry of either failed job. No current Coder inference
verdict follows until its responses are independently audited.

## 2026-11-26 03:35 UTC: Qwen3.6 server/Metal composition isolated

Job `job_2jz7thcz5n87asn` completed at $0. The
[independent audit](pr-evidence/qwen36-composition-audit.json) verified all 32
selected responses in four fresh processes, the exact CI model pin and sealed
server/native identities, full 41-layer Metal offload, F16 KV, 16×8192
context, native batch/microbatch 512, cache-off counters, and nominal thermal
boundaries. Both prompts repeat exactly across order and logprob modes.

With the original server and **fixed Metal**, both prompts match the current
combined-server/fixed-Metal text. With the combined server and **original
Metal**, both match the original-server/original-Metal text. Thus the Metal
component explains the new Skippy text in this registered two-prompt serial
screen. Neither old nor new Skippy text matches either pinned vanilla layout;
the exact-output gate still fails. This is component attribution, not a proof
of faulty model output or full-workload quality parity. No timed promotion or
further runtime change follows from this diagnostic alone.

Spec SHA256 `1ddda5b344b4acaef586bfe7811a388c306831036b8d98793ae18ebc9947af05`;
report `f003cc37ae1c22b326692a4c8988d440a47d7c2267b7b79bb6e2a66df07696e4`;
archive `08ca8d70d0191dc9e533f18c662307e024fdfefeac83b8df6f18da81a0fb34d8`;
composition `22664770a11fa4a461bfd4e082370222485c5bd5f716a09aeca54f9bf1c7c0cb`;
audit `1ca4847932e62ea68e0c1cd9e0f2a81e27828909a90c179dc899fa025b10fbec`.

## 2026-11-26 03:30 UTC: packaged native recipe synchronized

Core Metal patch `0046` is selected by the contiguous ordered patch queue.
The packaged native recipe now records its patch-queue SHA256
`cd7c2ab9408da56d1ab9ca09daf15594c31b781fcda347f5cae18402a0fb830a`.
The [source attestation](pr-evidence/source-attestation.json) verifies the
recipe against the queue and the tested native/Rust patches. The offline
`just with-lld cargo check --locked --offline -p skippy-api --features
dynamic-native-runtime` and eight upgrade-contract Python tests passed. This
source-identity check does not resolve the model-output differences.

## 2026-11-26 03:21 UTC: current Flash serial exact-output gate failed

Flash job `job_v3c9nvgw7497t5e` succeeded as an execution. The fetched
[independent audit](../../target/scheduler-fixes/artifacts/flash-current-correctness/audit.json)
(SHA256 `866e5cd1cc216df03ce1a099d5f089c452e6a06c9b3ff9b9183ffd2c53b29609`)
verified 64 responses from eight fresh processes, exact four-shard CI pin,
original and combined server/native identities, sealed stage receipts, full
Metal/F16 KV, 16×8192 context, cache-off launches and counters, request and
response hashes, and nominal thermal boundaries. All eight original-arm
selected serial outputs matched their archived controls. The current
candidate repeated exactly across modes and process orders, but `load-0001`
still differed from both vanilla layouts in both orders (4/4 comparisons);
`short-9` matched both vanilla layouts serially (4/4). The first `load-0001`
token difference is index 8: vanilla chooses ` each`, candidate `\n\n`,
with the latter only about 0.09 logprob units below vanilla's chosen token.
That identifies a stable numerical decision boundary, not a proven runtime
bug or a quality equivalence pass. The previous vanilla mixed-versus-serial
`short-9` difference remains separately unresolved. Flash exact-output is
**FAIL** and is not promoted to balanced timing under this protocol.

Qwen3.6 composition stage receipts match both sealed chunks; its model job
`job_2jz7thcz5n87asn` is running from spec SHA256
`1ddda5b344b4acaef586bfe7811a388c306831036b8d98793ae18ebc9947af05`.
Coder current-candidate stage receipts also match; its model job
`job_8mjr45cadydxmhb` is queued from spec SHA256
`48b46ab2b3334d084d715beb3bf6aa1bfee008818a7c971b7c8620964ef5e8c4`.
Both remain $0 and are separate from the failed Flash result.

## 2026-11-26 02:48 UTC: current Qwen3.6 serial exact-output gate failed

Qwen3.6 job `job_c9cy74twqp7rnp8` succeeded as a JY run, and its three
artifacts were fetched by digest. The [independent audit](../../target/scheduler-fixes/artifacts/qwen36-current-correctness/audit.json)
(SHA256 `c4679ebcfd7c94c90af3ad8afb68627163e42adeb27a1d4f68fb364b55b29304`)
verified all 64 responses from eight fresh processes, exact model and runtime
pins, sealed stage receipts, full Metal/F16 KV, 16×8192 context, caches off,
native settings and nominal thermal boundaries. Both original vanilla layouts
and original Skippy reproduced their saved serial controls. The current
combined Skippy plus fixed-Metal arm was repeat-stable, including
ordinary/logprob agreement, but differed from both vanilla layouts on
`short-0` and `long-2` in both process orders (8/8 registered comparisons).
Its first divergent tokens were index 3 and index 2 respectively. The
`short-0` candidate token ` no` was absent from vanilla's saved top five at
that step; on `long-2`, vanilla assigned the candidate `\n\n` token about
4.86 logprob units less than its chosen ` equipment` token. These are
measured output differences, not a diagnosed faulty operator. The
original Skippy baseline also differed from vanilla; this replay does not
attribute the new candidate text solely to the server or Metal change.
The exact-output gate is **FAIL**, so Qwen3.6 is not promoted to balanced
timing under the registered protocol. The saved job success is an execution
status, not a qualification pass.

The [registered composition control](SCHEDULER_FIX_PLAN.md) seals the two
missing Qwen3.6 server/Metal corners. Its spec SHA256 is
`1ddda5b344b4acaef586bfe7811a388c306831036b8d98793ae18ebc9947af05`;
stage jobs `job_4cm07m98wz4wcqq` and `job_mbqsbb4wyytb82r` are queued behind
Flash. No model process is submitted until both stage receipts are verified.
This control can attribute the selected text change to a component identity;
it does not change the failed exact-output gate or support timing promotion.

Flash's sealed stage receipts for `job_nts4d99gqwk99nw` and
`job_2rf8p4h16qqkc23` matched the registered chunk hashes. Its model replay
was submitted as `job_v3c9nvgw7497t5e` from the existing spec SHA256
`9d09ab2eb68b7f31a9a66c570bf4c1d660460aaf85ac8f07eee62f00919f5fb3`;
it remains a separate 64-response serial correctness screen.

Coder's original queue256 retest does not cover the combined server plus
fixed-Metal candidate. A separate [selected serial screen](SCHEDULER_FIX_PLAN.md)
has spec SHA256 `48b46ab2b3334d084d715beb3bf6aa1bfee008818a7c971b7c8620964ef5e8c4`;
its sealed stage jobs `job_zyyawnqtywr3dyt` and `job_2n705yv05ekjht7`
are queued. Its model process awaits receipt verification. The historical
single-user patched/vanilla-separate TTFT p95 paired interval
1.0133–1.0217 crosses the prospective 1.02 margin, so historical c256
completion cannot support a whole-model non-regression claim.

## Review evidence snapshot prepared

The [small review snapshot](pr-evidence/README.md) includes thirteen result
snapshots, four per-cell metric projections and timing-only rows for all
6,176 measured requests. Its
[manifest](pr-evidence/manifest.json) binds copies to SHA256 source-suite hashes,
evidence IDs, JY jobs and specs. The [PR description draft](SCHEDULER_PR_DESCRIPTION_DRAFT.md)
now links to these files rather than ignored local `target/` paths. They are
historical observations plus failed current Qwen3.6 and Flash serial checks;
prospective balanced timing results are still missing,
so the four-model claim remains
UNPROVEN. The snapshot files must be included in the eventual PR diff to be
reviewer-accessible.
The [source attestation](pr-evidence/source-attestation.json) applies the
tested combined Rust patch to a clean `HEAD` file set and compares all 21
resulting crate files byte-for-byte with this worktree. It also checks that
the PR's native Q4 Metal patch hash equals the component-build patch. This
closes source identity for those tested changes, without clearing model
correctness or timing gates.
The [table audit](pr-evidence/table-audit.json) independently recomputes the
four displayed tables from all 6,176 reviewer-visible timing rows and verifies
their request counts and SLO-round labels. It verifies arithmetic, not output
parity, current-candidate timing or a non-regression verdict.

## 2026-11-26 02:24 UTC: current-candidate Qwen3.6/Flash serial replay started

The [registered selected-prompt replay](SCHEDULER_FIX_PLAN.md) now targets
the current combined server and fixed-Metal bundle, with original Skippy and
both unchanged vanilla layouts as controls. It retains the exact CI model
pins, full Metal, 16×8192 context, F16 KV, batch/microbatch512, caches off,
two reversed fresh-process blocks and both ordinary/logprob transports.
Qwen3.6 tests `short-0` and `long-2`; Flash tests `load-0001` and `short-9`.
Each model has 64 registered responses, a $0 cap and no automatic retry.

Qwen3.6 stage jobs `job_z7vfcjnz4k3vvej` and `job_kzdk4cwy5wts25d`
succeeded, and both fetched stage receipts match their sealed chunks. The
main job `job_c9cy74twqp7rnp8` is running from [spec](../../target/scheduler-fixes/qwen36-current-correctness.spec.json)
SHA256 `f9bd7bda45922da811c4600b98051b7692284a259d97b439365b03017416a13a`.
Flash stage jobs `job_nts4d99gqwk99nw` and `job_2rf8p4h16qqkc23` are
queued from [spec](../../target/scheduler-fixes/flash-current-correctness.spec.json)
SHA256 `9d09ab2eb68b7f31a9a66c570bf4c1d660460aaf85ac8f07eee62f00919f5fb3`;
the Flash model job has not been submitted. The [fix ledger](../../target/scheduler-fixes/campaign.json)
is authoritative for live status and occupancy. These are serial correctness
screens, not concurrent quality or timing clearances. The original failures
remain intact until an audited current result resolves them.

## 2026-11-26 02:12 UTC: unchanged-vanilla Qwen3.5 c16 variation reproduced

The preregistered [vanilla concurrency probe](../../target/scheduler-fixes/q35-vanilla-concurrency.spec.json)
ran as `job_8m3anrpf87t0h1j` on the $0 M1 Ultra host. Both staging receipts
match their sealed chunk hashes. The [report](../../target/scheduler-fixes/artifacts/q35-vanilla-concurrency/vanilla-concurrency.json)
(SHA256 `19f37eaf166beaa75b505dc0a7dc1c742bcfb3b72f88afbbf5b016ca795d210c`)
and [archive](../../target/scheduler-fixes/artifacts/q35-vanilla-concurrency/vanilla-concurrency-evidence.tar.gz)
(SHA256 `9845f55993b0921a737b35e19d23cde94270ff7a9f141a0a20aa62c442f51f89`)
were fetched by digest and independently [audited](../../target/scheduler-fixes/artifacts/q35-vanilla-concurrency/audit.json).
Four fresh processes completed 16 waves/136 responses with the unchanged
vanilla binary, exact Qwen3.5 CI shard and `load-0100` request, both vanilla
KV layouts, 16×8192/F16 KV, batch/microbatch512, full Metal33, prefix cache
off, achieved c1/c16 client in-flight counts, zero cache counters and nominal
thermal boundaries. No substitute model, binary or native component was used.

| Vanilla layout | Serial exact | c16 matching own serial | c16 matching saved c128 | Distinct c16 texts |
|---|---:|---:|---:|---:|
| Separate slots | 4/4 | 0/64 | 16/64 | 4 |
| Unified | 4/4 | 0/64 | 21/64 | 4 |

The [audit's variant distribution](../../target/scheduler-fixes/artifacts/q35-vanilla-concurrency/audit.json)
retains all four hashes and counts per layout. Identical peer requests suffice
to produce the four vanilla c16 texts, including each layout's archived c128
text; heterogeneous peers and prior waves are unnecessary. The fresh fixed-
Metal Skippy serial controls match fixed Skippy c128 for both `load-0100` and
`load-0190`, but this does **not** make the vanilla outputs invalid or prove
which native operation creates the difference. The 12 cross-engine c128 exact-
output mismatches remain failed. No output tolerance, timing claim, or runtime
patch follows from this diagnostic. The next mechanistic control must inspect
the vanilla batch/logit path or establish an explicit, separately reviewed
quality policy; it cannot silently reclassify these failures.

## 2026-11-26 01:52 UTC: Qwen3.5 fixed-Metal serial controls audited

The registered [serial probe](../../target/scheduler-fixes/q35-fixed-serial.spec.json)
ran as `job_rjhtn7rec3h8neg` on `macstudio-2l0352` with a $0 cap. Both
staging receipts match their sealed chunks. The [fetched report](../../target/scheduler-fixes/artifacts/q35-fixed-serial/q35-fixed-serial.json)
(SHA256 `82d92281e14847c317d065798e13701b660e8f9d97e0257ce3cf393c464b8854`)
and [archive](../../target/scheduler-fixes/artifacts/q35-fixed-serial/q35-fixed-serial-evidence.tar.gz)
(SHA256 `73c35b9615654f5cd305f8e72c2cf03465ed2e027ab7776f59613d5bb52fc964`)
were fetched by hash and [audited](../../target/scheduler-fixes/artifacts/q35-fixed-serial/audit.json).
Eight fresh processes generated eight complete responses across two prompts,
two Skippy binary identities and reversed orders. The audit verifies the exact
CI model pin, selected binary and fixed-Metal component, 33/33 Metal layers,
16×8192 context, F16 KV, batch/microbatch512, cache-off launches and response
counters, request bodies, file hashes and nominal thermal snapshots.

Both fixed-Metal Skippy arms repeat exactly and match their respective fixed
c128 loaded output for `load-0100` and `load-0190`. The `load-0190` serial
response also matches the earlier fixed-Metal serving replay. Thus these two
Skippy c128 text groups do **not** require a Skippy serial-to-concurrent change;
the 12 registered cross-engine comparisons still fail because the vanilla
loaded text differs. This does not prove vanilla is faulty or locate a native
operator. Next isolate vanilla batching/numerics on the exact two prompts
before altering runtime code or changing the exact-output gate. No timing or
four-model quality clearance follows from this control.

## 2026-11-26: Qwen3.5 saved-output reconciliation

The [reconciliation](../../target/scheduler-fixes/artifacts/q35-c128-fixed/reconciliation.json)
hashes the fixed-Metal c128 output files and the two archived serial-control
rounds for `load-0100` and `load-0190`. All four loaded rounds and both old
serial rounds are stable within each arm. At `load-0100`, both fixed Skippy
loaded outputs match their **old-Metal** serial outputs; neither vanilla
loaded output matches its own serial output. At `load-0190`, all loaded arms
differ from their old serial outputs. The separately audited fixed-Metal
Skippy serial `load-0190` output matches fixed-Metal Skippy c128 exactly.
These comparisons do not identify a native operator fault or prove that
vanilla's concurrent output is incorrect. A fresh fixed-Metal serial
`load-0100` control is the next missing comparison; the old-Metal serial run
cannot stand in for it. The 12 cross-engine c128 mismatches remain failed.

Updated 2026-11-26 00:44 UTC. The admission, logprob chunk alignment and Q4_K Metal fixes are qualified for their registered cases and integrated in root. Q4 streaming integration completed and independently audited:204responses, including136fixed responses exact across serial/concurrent/reversed histories. The fixed-Metal heterogeneous Qwen3.5 c128 retest completed all2,048requests and was audited; cross-engine output and latency SLO gates still fail. This is not a four-model correctness or performance clearance.

| Model | Current evidence | Remaining work / limit |
|---|---|---|
| Qwen3.6 | Admission and cache-off logprob chunk alignment fixes qualified; combined replay44responses,24fixed,4expected baseline errors, exact registered ordinary/logprob outputs | Cross-engine and concurrent parity remain unresolved |
| Coder | Explicit queue256:4096/4096 complete, zero cross-arm/repeat mismatches | All16 latency SLO cells fail; default queue unchanged; no meaningful patched throughput gain |
| Qwen3.5 | Q4_K Metal scale fix integrated;336 local tests passed,1 skipped. Native16lane token parity and204response streaming audit passed; both fixed arms68/68exact. Fixed c128 retest:2,048/2,048complete, both Skippy arms exact across rounds | Solo/grouped logits still differ;12 c128 cross-engine comparisons and all16 TTFT SLO cells fail |
| Flash | 80responses audited: Skippy subject serial/mixed exact; vanilla mixed differences reproduced | Original cross-engine serial and vanilla mixed discrepancies unresolved |

Current evidence: [M1 arithmetic/component audit](../../target/scheduler-fixes/artifacts/q4-metal-components-v2/audit.json), [registered replay](../../target/scheduler-fixes/q4-component-retest.spec.json), and [ledger](../../target/scheduler-fixes/campaign.json). All jobs retain $0 caps; exact CI pins, F16KV, full Metal and caches-off constraints remain. C++ facade checks remain blocked by missing supplied GoogleTest; historical Thoughtworks16/227 and old hybrid-MoE discrepancies are not cleared.

The sections below retain the chronological evidence and earlier failures.

Qwen3.6 job `job_paq2jc7qz1g4pw5` completed all eight fresh processes and
32 responses. The [audit](../../target/scheduler-fixes/artifacts/qwen36-path-isolation/audit.json)
verifies binary/model identities, request bodies, fixture/token counts, launch
settings, full Metal offload, native KV allocations, zero caches and nominal
thermal boundaries. All 32 texts match their respective archived mode. Within
each mode, repeats and reversed-order outputs are exact; logprob values are
also exact. Only Skippy `long-2` differs between ordinary and logprob modes,
including their respective first requests. Vanilla separate-slot outputs agree
across both modes. This excludes dependence on earlier test requests, not
numerical sensitivity or a specific operator. The original cross-engine
disagreements remain unresolved.

The auditor initially rejected a schema difference: the new report includes
the derived `source_model_sha256` field, absent in the older Qwen3.6 report.
The [initial rejection](../../target/scheduler-fixes/artifacts/qwen36-path-isolation/audit-initial-failure.json)
is retained. The auditor now derives that missing historical field from the
identical pinned shard list and rejects any present value that disagrees.
All other model fields remain exact; the saved reports were not edited.

Artifact hashes: `path-isolation.json`
`bf063570fa1e98d7863c0bc15b8e62e4a7ec6da023f4246960cdf25df4205112`;
evidence archive `d158b78398043a8584996914588aac1fab0ade32d72d8bd6b22155f82d1cda75`.

The [ledger](../../target/scheduler-fixes/campaign.json) holds immutable job/spec
hashes, $0 caps, occupancy and current jobs. The original campaign and historical
Thoughtworks indices16/227 and hybrid-MoE failures remain unchanged.

Coder job `job_t6qt4fsegpdkwte` completed at 02:06:20 UTC. The
[audit](../../target/scheduler-fixes/artifacts/coder-c256-queue256/audit.json)
verified all 16 cells and 4096 requests, with zero cross-arm text mismatches
and zero cache counters. The extended audit also verifies exact cross-round
outputs and unchanged request bodies, model, source and binary identities.
Both Skippy arms explicitly use 256 waiting slots; the default remains 128.
This resolves this burst's admission configuration, not a runtime defect or
its latency failure. Original 896 rejections remain preserved.

| Arm | Completed | Aggregate output tok/s | TTFT p95 s | E2E p95 s | SLO rounds |
|---|---:|---:|---:|---:|---:|
| Skippy base | 1024/1024 | 59.22 | 129.85 | 137.13 | 0/4 |
| Skippy patched | 1024/1024 | 59.20 | 129.91 | 137.18 | 0/4 |
| llama separate slots | 1024/1024 | 50.76 | 152.15 | 160.16 | 0/4 |
| llama unified | 1024/1024 | 58.77 | 131.28 | 138.10 | 0/4 |

Medians of four balanced fresh-process round metrics, not pooled percentiles;
512 input / 32 output tokens per request, offered c256, 16 active lanes on
M1 Ultra, full Metal preflight, F16 KV, caches off. Frozen reference latency
749.4787085 ms. The [full table and limits](../../target/scheduler-fixes/artifacts/coder-c256-queue256/REPORT.md)
and [paired statistics](../../target/scheduler-fixes/artifacts/coder-c256-queue256/statistics.json)
retain p50/p95 latency, TPOT, per-round metrics and empirical intervals.
The patch shows no meaningful throughput improvement over base. All arms
miss the 30-second p95 TTFT target, despite passing the 1-second TPOT target.

The [thermal correlation](../../target/scheduler-fixes/artifacts/coder-c256-queue256/thermal-observations-audit.json)
places every saved JY thermal alert outside client measurement windows,
including one-second boundary uncertainty. Native boundaries were nominal;
the 0.5-second thermal/contention guard completed. No thermal retry is needed
on this evidence; sampling cannot rule out all transients.

Native settings and full offload are verified in diagnostic preflights with
matching timed model/runtime settings. Timed Skippy logs omit native settings;
they do not independently establish per-process native resolution. The
[extended-audit rejection](../../target/scheduler-fixes/artifacts/coder-c256-queue256/extended-audit-initial-failure.json)
records that limit. A separate [path-comparison rejection](../../target/scheduler-fixes/artifacts/coder-c256-queue256/extended-audit-path-failure.json)
was resolved by normalizing only the exact per-run candidate directory prefix,
with immutable binary identity unchanged. Neither correction modifies saved
evidence or relaxes output checks.

Qwen3.6 telemetry job `job_bbs6ra8pjcv0749` completed. Its
[audit](../../target/scheduler-fixes/artifacts/qwen36-driver-trace/audit.json)
verifies all eight responses against archived mode outputs, exact requests,
identities, full Metal/native KV settings, zero caches and nominal boundaries.
The [driver summary](../../target/scheduler-fixes/artifacts/qwen36-driver-trace/driver-summary.json)
accounts for 4096 prompt tokens and 23 fed-back output tokens in every request:

| Mode | Driver | Observed prefill | Subsequent iterations |
|---|---|---|---|
| Ordinary | Built-in | Eight scheduler steps of 512 tokens | 23 single-token decode steps |
| Logprobs | Feature | 4095 tokens via the feature prefill wrapper | 24 single-token feature steps, including the final prompt token |

These patterns repeat in both fresh starting orders. All 15,026 exported
telemetry counter samples report zero drops/export errors. Feature prefill's
chunk counter measures its wrapper, not native microbatches. This measures a
serving-path difference; it does not identify a faulty operator or establish
causality for changed text. The [thermal correlation](../../target/scheduler-fixes/artifacts/qwen36-driver-trace/thermal-observations-audit.json)
retains the 02:09:56 moderate/throttled observation, which preceded all requests;
coarse snapshots cannot prove continuous conditions.

Job `job_zfqe0dgtr8tafbg` failed at 03:45:15 UTC after one ordinary
response completed; the first logprob request returned HTTP 400. The
[failed experiment](../../target/scheduler-fixes/artifacts/qwen36-batch4096/path-isolation.json)
and [audit rejection](../../target/scheduler-fixes/artifacts/qwen36-batch4096/audit-failure.json)
are preserved. Native settings resolve batch4096/microbatch512 and 41/41 Metal
layers. Telemetry reaches feature-prefill admission and stats-before, then
session cleanup without a feature iteration. The original client did not save
the HTTP error body; no exact error message is inferred from that artifact.

Source inspection identifies a matching admission defect: whole-prompt feature
prefill checks native session batch size (4096), whereas direct iterations
reject more than 2048 tokens. A targeted fix checks both limits and retains the
existing long-prompt fallback; it does not widen scheduler limits or fix the
separate numerical discrepancy. Offline regression checks passed; native verification is pending.

Qwen3.5 job `job_28kytt57b3rnqpb` completed at 02:45:59 UTC. The
[audit](../../target/scheduler-fixes/artifacts/qwen35-batch-history/audit.json)
verifies 272 responses, all eight fresh processes, exact model/binary/request
identities, full Metal and native KV settings, zero caches and nominal thermal
boundaries. The guard completed and saved coarse observations show no thermal
alert overlapping requests. The [summary](../../target/scheduler-fixes/artifacts/qwen35-batch-history/summary.json)
retains every output group and archived c128 comparison.

| Arm | Serial responses matching archived serial | c16 responses matching serial | c16 responses matching all four archived c128 subject outputs | Distinct c16 texts |
|---|---:|---:|---:|---:|
| Skippy base | 4/4 | 0/64 | 64/64 | 1 |
| Skippy patched | 4/4 | 0/64 | 64/64 | 1 |
| llama separate slots | 4/4 | 8/64 | 20/64 | 3 |
| llama unified | 4/4 | 4/64 | 40/64 | 3 |

All concurrent waves reached 16 client requests in flight. Serial responses
remain exact before and after concurrent waves. The concurrent-first fresh
process reproduces differences in every arm, so prior test waves and
heterogeneous peer prompts are not required. Skippy base and patched yield
the same concurrent text; the patch does not explain this discrepancy.
Vanilla produces three texts even among identical peer requests. This narrows
the reproducer to a 512-token prompt at c16, but does not establish native
batch shape, faulty state reuse, numerical severity or an operator defect.
Original c128 failures and separate prompts remain unresolved. No runtime
change or timing claim follows from this diagnostic alone.

Flash job `job_07jqs1nvj065th3` completed at 03:40:37 UTC. Its
[audit](../../target/scheduler-fixes/artifacts/flash-history/audit.json) verifies
80 responses across eight fresh processes and 32 waves, including exact pins,
requests, native settings, full Metal, zero caches and nominal thermal boundaries.
Both Skippy arms keep `short-9` exact across serial/mixed waves. Both vanilla
arms reproduce a serial/mixed difference even when the mixed wave comes first.
Vanilla separate-slot mixed responses also vary between repeats; unified mixed
responses repeat exactly. Prior test waves are not necessary for the discrepancy;
within-wave peers and scheduling remain coupled. The separate serial `load-0001`
disagreement remains unresolved. No faulty operator or performance gain is claimed.

All six registered runs are terminal: five succeeded and batch4096 failed.
Completed occupied JY time is 4.9864 hours, cost $0; no job is active. The fix investigation continues.

All eight registered spec hashes match their ledger entries. The new sealed
payloads' script hashes, Python compilation, request counts, target and $0 lease
caps were verified; `git diff --check` passed. No duplicate jobs were submitted.

The admission fix is now available as a [three-file patch](../../target/scheduler-fixes/prefill-admission.patch),
SHA256 `9b2e8840f9cce88b7711a593681e010ea89b4710c6c3e926d6ec0f4957e49e59`.
It checks the direct scheduler limit as well as the native session batch limit,
retaining the existing long-prompt fallback. The original scheduler candidate
is excluded from the isolated source used to validate and build this fix.

[Serial validation](../../target/scheduler-fixes/prefill-validation.log) exited
successfully: `cargo fmt -p skippy-serving --check`, focused offline locked
check, offline locked all-target Clippy with `-D warnings`, and full offline
locked serving tests, all invoked by
[Just](../../target/scheduler-fixes/prefill-source/target/prefill-checks.just)
with `dynamic-native-runtime` and the supplied local native bundle. Tests:
1351 passed, 32 ignored, zero documentation tests. Ignored tests do not establish
model/hardware qualification. The initial formatting rejection is preserved
and was corrected before these successful checks.

Build `job_cp3tgxv699f18ac` succeeded at 10:03:54 UTC after the unrelated
reservation cleared. The [fetched identity](../../target/scheduler-fixes/artifacts/prefill-admission-build/scheduler-fix-build.json)
verifies source and isolated patch SHA; the [build log](../../target/scheduler-fixes/prefill-admission-build.log)
records successful Just release compilation. Binary SHA256 is
`cdbf4388d790c38ff36e0bdae9f7f376093fb29ffcea2d18f77914f9144e2e08`.
Build identity artifact SHA256 is
`f3f2d2530dc409aba93690826f40b86efd04e0942edf526d0c3bcf841f32b87a`.

The [32-attempt native replay](../../target/scheduler-fixes/prefill-admission-retest.spec.json)
completed as `job_f2y9rzcf8d1b60c`, run `run_jb469md8nph04ff`.
The [audit](../../target/scheduler-fixes/artifacts/prefill-admission-retest/audit.json)
passed 28 successful responses and four expected baseline HTTP400 rejections:

| Configuration | Baseline | Fixed |
|---|---|---|
| batch4096 ordinary | 4/4 complete | 4/4 complete |
| batch4096 logprobs | 4/4 reject at the 2048-token direct limit | 4/4 complete |
| batch512 ordinary | 4/4 complete, archived text exact | 4/4 complete, archived text exact |
| batch512 logprobs | 4/4 complete, archived text/logprobs exact | 4/4 complete, archived text/logprobs exact |

All 16 fixed requests completed and repeated exactly across both starting
orders. Every successful output matched its archived batch512 mode, including
numerical logprobs where requested. The exact baseline error body is retained:
`scheduler iteration exceeds the 2048-token configured iteration limit`.
This qualifies the admission-bound fix for the tested case without widening
scheduler limits or changing the original numerical acceptance criteria.

Identity, exact requests, 4096 input / 24 output tokens, zero cache counters,
41/41 Metal layers, F16KV 2560 MiB and native settings passed the audit.
The [trace/thermal summary](../../target/scheduler-fixes/artifacts/prefill-admission-retest/trace-thermal-summary.json)
records 55,178 samples each of dropped-event and export-error counters, all zero;
native thermal boundaries and saved coarse host observations are nominal.
Coarse telemetry cannot exclude all transients. This was an untimed correctness
replay, not a performance qualification.

The successful fixed batch4096 logprob path uses the existing 4095-token
prefix fallback and 24 feature iterations. Ordinary requests still execute
eight 512-token prefill iterations and 23 decode steps. Thus changing the
logical batch did not align the executed prompt boundaries. The original
ordinary/logprob difference remains; no faulty numerical operator is identified.
Further native prefill/batch attribution is the next investigation step.

Fetched report SHA256:
`da5f65f75d3b2f21b9f8d7ca606c4cc33029a9544fb38b3ca991bcf6e2a650d6`;
evidence archive SHA256:
`d3cac6357c84aff0ae30e4a74bb43bf945399eb15bf6b4fac56b2ca665915280`.
The [auditor self-test](../../target/scheduler-fixes/prefill-audit-selftest.log)
passed synthetic accounting and request/error mutations; it is not a native
result. Original Qwen3.5, Qwen3.6 and Flash numerical failures remain unresolved.

## 2026-11-25 10:57 UTC: prefill-boundary diagnostic queued

The admission fix remains qualified only for the registered Qwen3.6 rejection
case; the numerical differences remain unresolved. Preregistered an eight-response
baseline-only replay at logical batch512 / microbatch455, preserving model, binary,
native bundle, full Metal, F16 KV, caches off, sampling and thermal soaks.
The 4095-token prefix divides into nine equal455-token chunks; this tests whether
changing the ordinary/feature prefill boundary changes the output discrepancy.
Feature native chunking is a source-derived prediction, not independently observed.

Submitted once as `job_a28hsz963k4n0gb`; currently queued behind unrelated tierbench
work on `macstudio-2l0352`. Lease1800 seconds, maximum charge$0, no automatic retry.
[Spec](../../target/scheduler-fixes/qwen36-micro455.spec.json) SHA256
`f61ca71fa12c0fe28eb5d807b82d163e784883e09b684d766fec2c86707d3c98`.
[Receipt](../../target/scheduler-fixes/qwen36-micro455.submission.txt) and
[ledger](../../target/scheduler-fixes/campaign.json) retain submission and host evidence.

The parameterized harness/auditor passed archived8trace/32isolation/28admission
response checks, rejected a false455 launch claim, and verified sealed script
hashes, compile, request count, target and$0 cap. These are offline harness checks;
the native diagnostic has no result yet. Required next audit covers native455
resolution, full offload, exact requests/hashes, zero cache counters, thermal
boundaries/guards and dropped-event/export-error counters. Output differences
will be retained regardless of outcome; no tolerance or production setting changes.
Heartbeat renewed as `7ddd4a91` through16:57UTC for pending work.

11:00 UTC status: diagnostic remains queued with no attempts/runs and $0 cost.
Unrelated tierbench job `job_sznk9d92yk8w46d` still occupies the node; host
telemetry is nominal. No duplicate submission or inference result. Dated job/host
snapshots and their hashes are retained in the ledger.

11:10 UTC status: diagnostic started11:03:16UTC as `run_0656hght55qy0x6`.
Both fresh-process blocks have started; the job is still running, with no error
in the retrieved JY log. The node reports only this job active, nominal thermal
state and normal memory pressure. Completion/native trace audit remains pending;
process-start messages do not establish eight successful responses. Cost$0.

## 2026-11-25 11:22 UTC: microbatch455 diagnostic audited

Job `job_a28hsz963k4n0gb` succeeded11:12:45UTC after9m29s. The
[audit](../../target/scheduler-fixes/artifacts/qwen36-micro455/audit.json) verifies
eight responses, exact model/binaries/requests, native batch512/microbatch455,
41/41 Metal layers, unchanged F16KV allocation and zero cached tokens. Both
fresh starting orders and every repeated output agree across ordinary/logprob
modes. Logprob numbers repeat exactly within this run. All four logprob texts
match archived512; all four ordinary texts changed to that same text.

The trace verifies ordinary prefill nine455-token steps plus one token; feature
prefill remains4095 tokens plus24 single-token feature iterations. The feature
wrapper does not measure its individual native chunks. Each of15,407 reported
drop/export-error counter samples is zero; four process thermal boundaries are
nominal and requests completed under the thermal/contention guard. The saved
11:10 host observation was also nominal. No timing claim or new production
configuration follows from this diagnostic.

Report SHA256 `a6fd0815fa44dd1694c56de6d08efb1a1b8719dadbe6d67786a9aad1f5ac5f5f`;
archive SHA256 `3a3f8e7bc9fd32ac07e8517b6bc0e465a602e6587c8329609f24154bf221cbcb`.
Changing batch conditions removes this mode discrepancy on the registered
prompt, but455 changes every prefill chunk as well as the final boundary.
A microbatch511 control will distinguish final-token boundary alignment from
general batch-size sensitivity before any additional Rust change. Original512,
cross-engine, Q35 and Flash discrepancies remain unresolved.

Microbatch511 negative boundary control submitted once as `job_0zj0ny14mrtgbge`
([spec](../../target/scheduler-fixes/qwen36-micro511.spec.json), SHA256
`5388cd63f1d8e235eac808c94764492d2f4acc9306f60c019e355e6127f6aa05`).
Eight requests, lease1800, $0; no production/Rust changes. The host was idle
and nominal before submission. Archived8trace/32isolation/8micro455/28admission
responses reaudited successfully, false511 launch claim rejected, sealed payload
hash/compile/count/target/budget checks passed. Await native evidence before
concluding whether the final-token boundary is necessary for agreement.

Supplemental comparison: microbatch455 logprob values are also exactly equal
to archived512 logprob values, not merely equal decoded text
([comparison](../../target/scheduler-fixes/artifacts/qwen36-micro455/archived-logprob-comparison.json)).

## 2026-11-25 11:36 UTC: microbatch511 control audited

`job_0zj0ny14mrtgbge` completed11:30:18UTC, after7m18s. The
[audit](../../target/scheduler-fixes/artifacts/qwen36-micro511/audit.json) verifies
eight responses, both fresh starting orders, repeats, exact native511 settings,
41/41 Metal layers, F16KV, cache counters, requests and hashes. Ordinary trace
is eight511-token prefill steps plus8tokens; feature trace remains4095prefix
plus24single-token iterations. Four thermal boundaries and the saved near-end
host observation are nominal. All15,276 samples of each telemetry drop/export
error counter are zero. Requests completed under the thermal/contention guard.

| Microbatch | Ordinary/logprob text agreement | Ordinary text starts | Logprob comparison |
| --- | --- | --- | --- |
| 512 original | No | ` checks the each morning.` | Archived baseline |
| 455 diagnostic | Yes, all8 responses | ` checks the` then newline | Exactly matches512 logprob text and values |
| 511 diagnostic | Yes, all8 responses | ` checks the equipment and` | Changes from512/455; matches archived vanilla text, not its reported logprobs |

A one-token final prefill boundary is therefore not necessary for agreement:
the511 ordinary path ends with8tokens. Batch conditions affect both the mode
difference and the resulting text, so neither diagnostic setting is a verified
production fix. No tolerance, default microbatch or runtime has been changed.
The [archived comparison](../../target/scheduler-fixes/artifacts/qwen36-micro511/archived-comparison.json)
retains the remaining numerical differences; no cross-engine quality clearance.

Report SHA256 `16e11d7c931abff9d673b6321dbf4ebd6acb955e366c00d31e68cf0c511e50c4`;
archive SHA256 `a18feef8a2d2513512a3f31235019d8f4626e5d744a44b63699b4c4333d5b871`.
Fix-campaign completed occupancy is5.2662hours, cost$0; all10 registered jobs
are terminal. The campaign remains investigating, with no new job submitted.

Next work is a bounded native logits/tensor probe against the exact source and
native recipe, rather than another guessed microbatch. Source inspection found
existing tensor tracing is explicitly gated toGLM_DSA, so it cannot be treated
as Qwen evidence. Pipeline names in logs show compilation, not per-request
operator execution. No additional Rust/native fix is justified by those names.
Q35 concurrency, Flash serial/mixed and original Qwen3.6 discrepancies remain
unresolved; the admission fix and explicit Coder queue remedy remain as audited.

## 2026-11-25 11:59 UTC: native probe setup repaired; replay pending

The first standalone probe, `job_ax8ke3m120x5yt4`, failed in header preparation
before any model load or generation. The cached vanilla Git repository used a
shared object store from a deleted build workspace, so the pinned patched tree
could not be archived. The runtime/model/fixture digest checks preceding that
operation completed; no numerical inference result exists for this attempt.
[Failure artifact](../../target/scheduler-fixes/artifacts/qwen36-native-probe/native-probe.json)
and the original immutable spec remain preserved.

The repair copies SHA-verified existing upstream headers into a private directory,
applies the exact pinned header diff, and verifies all resulting files against
Git objects at native commit67044352. Three bounded120-second/$0 transport jobs
succeeded; all fetched receipts match their chunk SHA256. Compressed diff SHA256
`41be4e8e7a7fd2243411b5325af07320873838003cfb0c923fe1461c93544f6e`.
No runtime library or model was replaced or downloaded. Local exact reconstruction
and Just C++ build with `-Wall -Wextra -Werror` passed; sealed payload hashes,
Python imports/compilation, node, eight requests, zero warmup, $0/3600-second
budget and no retries were checked. This is setup validation, not qualification.

Revised eight-process probe submitted once as `job_6ebzdpxyactykbv`:
[spec](../../target/scheduler-fixes/qwen36-native-probe-headers.spec.json), SHA256
`e712bc6b36f8546874c5175c4c6ac452e2ed32aa25d5dbfa1742fae7f19ea299`.
Require remote native settings/identity, thermal guards, exact fixture tokens,
header provenance, complete responses and archived server-output calibration
before interpreting logits. Private full logit arrays remain on the host.
Admission fix stays qualified for its registered case; Qwen3.6 numerical,
Q35 concurrency and Flash serial/mixed differences remain unresolved.
Completed fix-campaign occupancy5.2712hours, cost$0. Heartbeat5820b24a tracks the
ledger through17:50UTC; the historical campaign remains closed.

Replay started11:57:41UTC (`run_kfh6jk10tqccg89`). The remote log reached
`NATIVE_PROBE 0 whole`, confirming reconstructed-header checks and Just build
completed. Eight-response completion and native generation audit are pending;
a process-start log is not a successful replay. Latest host state is retained
in the ledger, with no changes to unrelated jobs.

12:00UTC status: revised probe `job_6ebzdpxyactykbv` remains running and has
advanced from `whole` to `whole-logprobs` in its first block. This indicates the
runner completed the first case, but fetched evidence and the independent audit
are still pending. The node reports only this job active, nominal thermal state,
no throttling and normal memory pressure. Dated job/log/host snapshots and hashes
are retained in the ledger. No duplicate job, runtime edit or new inference claim.

## 2026-11-25 12:14 UTC: direct native probe audited; chunk mechanism isolated

`job_6ebzdpxyactykbv` completed12:12:45UTC after15m04s. Its
[audit](../../target/scheduler-fixes/artifacts/qwen36-native-probe-headers/audit.json)
verifies eight fresh processes/192generated tokens, original runtime/model/header
identities, exact4096prompt tokens, native512/512,41/41Metal,F16KV,32native
sequences/no rollback, nominal process boundaries and guarded request windows.
No prefix/disk cache APIs are invoked. Both archived server text calibrations
pass. All four paths repeat their text and every private post-call logit-row hash
exactly in the reversed fresh-process block.

| Controlled comparison | Text/token result | Post-call vocabulary logits |
| --- | --- | --- |
| Whole chunks, logprobs off versus on | All24tokens identical | All24row hashes identical |
| Split iteration API versus split raw-prefill API | All24tokens and recorded logprobs identical | All24row hashes identical |
| Whole eight512chunks versus seven512+511+1 | First two tokens agree; third differs | Rows differ already at first output |

At output index2, after the same generated tokens11988/279 (` checks the`),
whole picks1754 (` each`) with logit19.56615 versus newline-token271 at18.90910;
split picks271 with logit19.57558. This is not merely a different printed text
or an unobserved tie. Comparisons stop after the first differing generated token
because later histories differ. Raw full vocabulary rows remain private on host;
the local audit verifies the fetched hash attestations and top20 metadata.

This isolates the registered mode discrepancy to prefill chunk layout. Logprob
recording itself and the choice of these two split APIs are not necessary causes
in this replay. It does not identify a faulty operator, clear cross-engine
numerics, or establish quality/performance across models. Ordinary text remains
` checks the each morning.`; both split paths reproduce the archived logprob text.

Report SHA256 `2003ada748ae4a9d9a42c29527d2fef618549292dbd63f8f4369c080105253a8`;
archive SHA256 `31afdf5898e6d64d6af270f781e58bfb67c70a8a3bea6be0d142b5c0dc9e5255`.
Probe binary SHA256 `855c3c0a62e569cbfddfc892da64e7949075f2e719221c328b89b062c9f7754b`.
Completed fix-campaign occupancy5.52236hours, cost$0. Original failures preserved.

A targeted cache-off logprob alignment candidate is now in isolated
`target/scheduler-fixes/logprob-source` (baseline671c6ebb plus qualified admission
fix). It shares the ordinary driver's positive chunk bound, samples the final
row of the final complete prompt chunk, and retains per-chunk cancellation and
session/logprob collection. The separable sampled-prefill code moves into its
own module. Serial Just checks are running; no candidate build/native replay
has passed yet. Ordinary, cache-enabled, single-token and prefill-only paths
retain their existing selection. No numerical tolerance or native setting change.

## 2026-11-25 12:26 UTC: alignment candidate checked and built; native replay launched

Isolated candidate [patch](../../target/scheduler-fixes/logprob-prefill.patch) SHA256
`bd1956c5ea17cc588598b49d3c6be1d5f725d34fecaca2afb28304327b59cca0`
contains the qualified admission fix plus cache-off logprob chunk alignment.
Just formatting, focused check and all-target Clippy with warnings denied passed.
Full serving tests with `RUST_TEST_THREADS=1` passed1351, ignored32 explicit
fixture tests, and completed0documentation tests. Logs:
[compile/Clippy and initial test attempt](../../target/scheduler-fixes/logprob-validation-attempt3.log),
[successful serial full tests](../../target/scheduler-fixes/logprob-tests.log).

Validation limits: the first full run supplied the bundle child instead of the
parent expected by the dynamic test loader and failed34tests. After correcting
the path, a parallel run failed6tests that reached native FFI before loading
completed; [that run](../../target/scheduler-fixes/logprob-tests-parallel-loader-race.log)
is retained. Serial tests passed without disabling tests or changing assertions.
The dynamic-loader test-order issue is not claimed fixed. Earlier compiler errors
(private method visibility and missing test initializers) were corrected and
rechecked; no warning suppression was added. Offline checks do not qualify models.

Build `job_g1jqxxa2eaarm5r` succeeded12:24:13UTC on the authorized host.
[Fetched build identity](../../target/scheduler-fixes/artifacts/logprob-prefill-build/scheduler-fix-build.json)
SHA256 `a6362e2eaad686bd5c94d50e6c155085406fe870bacf2b25b600eab227aba5a0`;
fixed binary `f6798db48c310477627b3366ee99e64f1b00cab2f6ede005f64aa206be42f6de`.
The original native bundle/model pins remain unchanged. Completed fix-campaign
occupancy5.57304hours, cost$0.

Native48-attempt replay submitted once as `job_aegae36fxqsx6jc`:
[spec](../../target/scheduler-fixes/logprob-prefill-retest.spec.json) SHA256
`d104173be6dcbb51311888ac5fa3f293b0315f650dcbfce3cd1cb39354b8e9b7`.
Expected44completions/4original baseline errors/24fixed completions; both batch
settings and starting orders, repeated long prompt plus short controls. The
sealed harness checks exact candidate binary identity. Offline synthetic auditor
checks passed44responses/4errors and rejected altered cache requests and a false
chunk trace; these synthetic records are not native evidence. Lease5400s/$0,
no retries. Native replay and independent audit remain pending.

The alignment patch remains in the isolated checkout and reviewable patch file;
it has not been applied over the root's earlier scheduler candidate. That candidate
and all original failures are preserved. Q35, Flash, cross-engine and concurrent
qualification remain unresolved. Monitoring heartbeat replaced with `e0f62ddf`
through18:26UTC, using the authoritative ledger and current replay details.

12:30UTC status: `job_aegae36fxqsx6jc` is running in its first baseline4096,
normal-first process, started12:25:49UTC. The retrieved job log contains no
failure; only this job occupies the node. Coarse telemetry is nominal, not
throttled, with normal memory pressure. Dated status/log/host snapshots and
hashes are retained in the ledger. Per-request counts, baseline error bodies,
fixed parity and native/thermal evidence await terminal artifacts; no new
correctness or performance claim and no duplicate submission.

12:40UTC status: replay advanced to its third process, fixed4096/logprobs-first,
after the baseline and fixed normal-first blocks. Only this job is active; coarse
telemetry remains nominal, not throttled, with normal memory pressure. No retrieved
job error. Final response/error counts and parity await artifact audit. Dated
job/log/host snapshots and active occupancy are retained in the ledger.

Prepared an [alignment-only patch](../../target/scheduler-fixes/logprob-alignment-only.patch)
excluding the admission fix already present in the root. SHA256
`c589c5184442335b658e4faea7fb7cd8658eb592eb399501827e4aae59a506a4`.
`git apply --check` passes against the existing root scheduler candidate. It was
not applied. This check establishes clean integration, not combined-candidate
correctness; serving checks and separate native evidence remain required after
integration. The isolated candidate used by the running replay is unchanged.

12:50UTC status: replay has reached process6/8, fixed512/normal-first. All four
batch4096 blocks and baseline512/normal-first have returned to the runner; this
is progress, not independent verification of outputs. Only the registered replay
is active. Coarse host telemetry remains nominal, unthrottled, with normal memory
pressure. No retrieved job failure, no retry, no duplicate submission; cost$0.
Final response/error accounting and fixed mode parity remain pending. Dated
snapshots/hashes and elapsed occupancy are recorded in the ledger.

## 2026-11-25 13:03 UTC: isolated logprob alignment qualified; integrated candidate validating

`job_aegae36fxqsx6jc` succeeded at13:03:05UTC. The independent
[audit](../../target/scheduler-fixes/artifacts/logprob-prefill-retest/audit.json)
verified44completed responses,24fixed completions and4expected original
batch4096 HTTP400 errors. All fixed text equals archived ordinary output on both
prompts, across starting orders, repetitions and batch512/4096. Fixed logprobs
repeat exactly across orders/batches. Feature trace now reports4096tokens/eight
chunks and384tokens/one chunk. Exact model/build/native settings, full41/41Metal,
F16KV,16lanes8192context,micro512 and caches off passed. All16thermal boundaries
were nominal; the sealed runner guarded requests for thermal pressure/contention.
No positive telemetry drop/export-error counter was found. This qualifies the
registered serial mode discrepancy only, not cross-engine or concurrent parity.

[Report](../../target/scheduler-fixes/artifacts/logprob-prefill-retest/prefill-retest.json)
SHA256 `a566bc152979e8be612b0442a937251e7af19daa12278383d0fbd65453e4fbc4`;
[archive](../../target/scheduler-fixes/artifacts/logprob-prefill-retest/prefill-retest-evidence.tar.gz)
SHA256 `ac2edb4c0c932cde3c50685f87aebae078ef6e6b89dd76ec35d2b18d7acf2c7a`.
Completed campaign occupancy6.19416hours, cost$0.

The alignment-only patch is now applied over the preserved original scheduler
candidate. [Pre-integration backup](../../target/scheduler-fixes/root-before-logprob.patch)
retains that prior diff. Combined serial [Just checks](../../target/scheduler-fixes/combined-checks.log)
passed formatting, focused check, all-target Clippy-Dwarnings and full tests:
cache287+39integration, scheduler8, serving1354;1688passed/34ignored,0doctests.
The three additional serving tests belong to the existing scheduler candidate.
Ignored fixture tests are not hardware qualification. Broader workspace lint is
running separately; combined native replay has not yet run.

Combined [transport patch](../../target/scheduler-fixes/combined-logprob-u1.patch)
SHA256 `92afb221a210c0c3aa0a29320119af2abea1c9dc76650e169be554f885073701`
uses one context line to fit the JY command limit. Its reconstructed Git tree
`6d55f3dbc9ed913cdd5c54dd713a535e9ce53551` exactly matches the full combined patch;
no source content changed to shrink transport. The first full-context generator
attempt exceeded the command limit and submitted no job.
Build `job_g1vjwj6kznv80qx` is queued under a3600s/$0lease, no retries. The
[spec](../../target/scheduler-fixes/combined-logprob-build.spec.json) and sealed
source/patch/builder hashes were verified. The replay generator/auditor now accept
explicit candidate identities while retaining isolated defaults; synthetic audit
checks still pass and reject altered requests/chunk traces. Q35/Flash/cross-engine
and concurrent discrepancies remain unresolved; original failures are preserved.

13:10UTC: combined build `job_g1vjwj6kznv80qx` succeeded13:09:45UTC.
[Build identity](../../target/scheduler-fixes/artifacts/combined-logprob-build/scheduler-fix-build.json)
SHA256 `8d65e737347b5ed23c47d0d4282b80e5ea3a91e43c39178d7083a141c1b77c62`;
binary `d80dd044eb06c96e917945afa517470cb991082d259542ffe7d23fa8dc029d58`.
Combined48-attempt replay `job_hkm4ks1nw9vqf2z` submitted once with explicit patch
and binary identities. [Spec](../../target/scheduler-fixes/combined-logprob-retest.spec.json)
SHA256 `88dd8b1e4790585416bcc389752c9fef06f6a713bc0d08ace2114af86b3e6cd6`.
Same44expected completions/4baseline errors/24fixed, no tolerance or setting
changes. Native combined parity remains pending. Completed occupancy6.24467h/$0.

Broader [workspace lint](../../target/scheduler-fixes/combined-workspace-lint.log)
passed both Rust workspaces. `just test` [stopped before C++ tests](../../target/scheduler-fixes/combined-workspace-test.log)
because GoogleTest v1.18.0 is not staged; HTTPS was blocked with
`GIT_ALLOW_PROTOCOL=file`. No replacement dependencies were downloaded.
A local cache search found no usable staged copy. The corresponding Rust workspace
commands are running serially through a [Just wrapper](../../target/scheduler-fixes/combined-checks.just),
with offline mode and isolated test state; those results are separate from the
unrun C++ facade tests. This limitation does not erase the1688passed changed-crate
tests or claim complete repository validation.

13:13UTC: both Rust workspace test commands completed successfully, including
integration and documentation tests: 4864passed/314ignored across114test groups;
[full log](../../target/scheduler-fixes/combined-rust-workspace-test.log). The separate
C++ facade test dependency blocker remains. Combined native replay started
13:10:28UTC (`run_zg7p4x1wnzexhrr`), only this job active; coarse host telemetry
is nominal/unthrottled with normal memory pressure. Final native audit pending.
Report links and `git diff --check` pass; heartbeat retained because work continues.

13:20UTC status: combined replay is in process2/8 (combined4096 normal-first),
after its first baseline process. Only this registered job is active; retrieved
logs contain no job error. Coarse telemetry remains nominal/unthrottled with
normal memory pressure. Dated job/log/host snapshots and hashes, elapsed occupancy
and unchanged spec/patch identities are recorded. Final counts and output/native
audit remain pending; no retry, duplicate submission or new correctness claim.

13:30UTC status: combined replay reached process4/8 (baseline4096 logprobs-first).
Both combined4096 blocks returned to the runner; output parity is not yet
independently audited. Only this job is active, no reported failure, nominal
coarse thermals/no throttling/normal memory pressure. Status/log/host hashes and
active occupancy updated. Spec and patch hashes, report links and diff checks
remain valid. No duplicate job, retry, new runtime edit or qualification claim.

13:40UTC status: combined replay reached process6/8 (combined512 normal-first).
All batch4096 processes and baseline512 normal-first returned to the runner.
No reported failure; only this job is active. Coarse telemetry is nominal and
unthrottled with normal memory pressure. Dated snapshots/hashes and elapsed
occupancy updated; immutable spec/patch, links and diff checks pass. Final counts
and independent parity/native/thermal audit remain pending. No duplicate job.

## 2026-11-25 13:50 UTC: combined logprob alignment qualified

Combined replay `job_hkm4ks1nw9vqf2z` succeeded13:49:54UTC. The independent
[audit](../../target/scheduler-fixes/artifacts/combined-logprob-retest/audit.json)
passed44completed responses,24fixed completions and4expected baseline HTTP400
errors. Fixed text equals archived ordinary output on both prompts across mode,
starting order, repetition and batch512/4096; fixed logprobs repeat exactly.
Feature trace reports4096tokens/eightchunks and384tokens/onechunk. Exact binary,
model and native settings, full41/41Metal,F16KV,16lanes8192ctx,micro512,cachesOFF,
nominal boundaries and guarded requests passed. No positive drop/export counters.

A separate [integration comparison](../../target/scheduler-fixes/artifacts/combined-logprob-retest/integration-comparison.json)
finds24/24fixed texts and12/12logprob responses exact against the isolated fix.
Thus the registered serial mode issue is fixed in the integrated root candidate;
this does not establish concurrent, cache-enabled, other-model or cross-engine
parity. Original baseline failures, historical discrepancies and separate C++
GoogleTest dependency blocker remain. Both Rust workspaces passed4864tests with
314ignored; ignored fixture tests are not hardware qualification.

[Report](../../target/scheduler-fixes/artifacts/combined-logprob-retest/prefill-retest.json)
SHA256 `c38f613b5944f97db7ecd3574ade6099d4854723db87bb258e5c858cf3402aa5`;
[archive](../../target/scheduler-fixes/artifacts/combined-logprob-retest/prefill-retest-evidence.tar.gz)
SHA256 `103f79c623ed88a6ba7127c84e9d098cdf034c7bf1c7fb3356b365db15145518`.
Completed fix-campaign occupancy6.90193hours,$0. No duplicate jobs/retries.

## 2026-11-25 13:55 UTC: remaining Q35 discrepancy advanced to step tracing

Registered the unchanged-baseline68-response/eight-wave scheduler trace described
in the [plan](SCHEDULER_FIX_PLAN.md). Previous272-response history re-audit passed.
Offline synthetic trace audit passed68responses and rejected telemetry loss,
missing steps and changed requests; those are not native results. Exact payload
hashes, pinned baseline/CI model, target,68requests,zero warmup,$0/1800s lease and
no-retry settings verified. No runtime edits, precision substitutions or downloads.

Job `job_2a8nk71a2a1ca9r` started13:54:55UTC, run`run_6fjrtf85szhaqwk`.
[Spec](../../target/scheduler-fixes/qwen35-batch-trace.spec.json) SHA256
`ecd17cd047d8227e21ea2f64ee76e0cae7b94b45f8319740b87a0c04ffc8c617`.
Fetch/audit instructions and job/spec hashes are in the authoritative ledger.
Q35 concurrent, Flash and cross-engine discrepancies remain unresolved; Coder's
explicit queue remedy completes requests but still fails latency SLOs.

Completed logprob heartbeat`e0f62ddf` deleted and removed from active ledger state.
New remaining-investigation heartbeat`b9acbd08` runs through17:55UTC. Root source
and original failures preserved; report links and `git diff --check` pass.

14:00UTC status: unchanged-baseline Q35 trace has reached its second/final
fresh process (c16-first), after completing the c1-first block. Only this job
is active; no reported error, nominal coarse thermals/no throttling/normal
memory pressure. Dated job/log/host snapshots, hashes and elapsed occupancy
recorded. Exact output calibration and scheduler token accounting await final
artifacts; no native mechanism or correctness claim yet. No duplicate submission.

## 2026-11-25 14:10 UTC: Q35 trace audited; decode grouping isolated next

`job_2a8nk71a2a1ca9r` succeeded14:02:12UTC. Its independent
[audit](../../target/scheduler-fixes/artifacts/qwen35-batch-trace/audit.json)
verified68responses/eightwaves, exact correspondence with archived history text,
requests, native settings/full33Metal/F16KV/cachesOFF, thermal boundaries and
scheduler token accounting. All serial outputs remain stable; all64concurrent
outputs differ from serial, including the fresh concurrent-first wave.
No positive telemetry drop/export counter, no tolerance change.

Each serial wave records one512token prefill plus31single-token decode steps.
Each c16 wave records16separate512token prefill steps and45decode steps: grouped
rows ramp2..16, remain16, then drain. No mixed prefill/decode step appears.
These serving step observations narrow the next control but do not identify a
native operator fault or clear c128. Trace instrumentation preserved every output.

[Report](../../target/scheduler-fixes/artifacts/qwen35-batch-trace/batch-history.json)
SHA256 `d31f2562aa8da803936ea7feadfefe724363d16d49a3d27df9eb2be334c3b9e3`;
[archive](../../target/scheduler-fixes/artifacts/qwen35-batch-trace/batch-history-evidence.tar.gz)
SHA256 `5e5c193755450ef2a01af027c8826c89aaa3d351f7116b3743da0e5a15e2660c`.
Completed fix-campaign occupancy7.02310hours,$0.

The next [registered control](SCHEDULER_FIX_PLAN.md) calls the unchanged native API
in solo, resident-serial, resident-batched and staggered modes, twice/reversed.
Eight fresh processes,98lane completions,3136tokens;256full subject-logit rows stay
private, with hashes/top20 exported. Exact original remote native/header/CI model
identities and all prior settings remain required. Solo and staggered text must
calibrate server evidence before applying findings to the server. Completed lanes
remain resident until process end; this cleanup difference is explicit.

Diagnostic caller compiled via Just with-Wall/-Wextra/-Werror and passed ABI
0.1.87/config240 smoke against the supplied local same-source bundle. This is
[compile evidence](../../target/scheduler-fixes/qwen35-batch-probe-local-build.log),
not remote generation. Sealed source/header/CIpin payload hashes, syntax,8process
count,$0/3600slease and zero retries verified. No Rust/native runtime edit.

Submitted once as `job_kx206dpzq8sy6vb`.
[Spec](../../target/scheduler-fixes/qwen35-native-batch-probe.spec.json) SHA256
`bc6a2a047175de67b08fa5bbb7392fcde8f60145c16d636dfa0a1c7a8dc19d02`.
The ledger has current fetch/audit instructions; existing investigation heartbeat
remains active. Q35/Flash/cross-engine failures, Coder latency SLO failures and
C++ dependency blocker remain unresolved. All historical failures retained.

14:22UTC status: native batch probe is running, with the second of eight fresh
processes reached (`resident-serial`, first block). Only this campaign job is
active on the host. Coarse thermals are nominal, unthrottled, with normal memory
pressure; these observations do not replace the final guarded-window audit.
Dated job/log/host snapshots and hashes plus elapsed occupancy are recorded.
No duplicate submission, runtime edit or new correctness claim.

14:30UTC status: seventh of eight native probe processes reached
(`resident-serial`, reversed block). No reported job error; final process,
artifact export and independent calibration/logit audit remain pending.
Dated status/log/host hashes and elapsed occupancy updated; no duplicate job.

## 2026-11-25 14:40 UTC: native grouping reproduces Q35 server difference

The unchanged-native probe succeeded at14:32:24UTC. Independent
[audit](../../target/scheduler-fixes/artifacts/qwen35-native-batch-probe/audit.json)
passed eight fresh processes,98lane completions,3136tokens and256subject-row
hashes; exact CI/native/header/source identities, call plans, token IDs, Metal/KV
settings and thermal gates passed. Both repeats have identical tokens and subject
logit hashes. Solo matches archived serial text; every staggered lane matches
archived concurrent text. No runtime or tolerance change.

| Control | Subject text vs solo | Subject logits vs solo | Repeat |
|---|---|---|---|
|16resident sessions, individual decode|Exact|All32rows exact|Exact|
|16resident sessions, grouped decode|Different|First differs at output index1|Exact|
|Staggered grouped decode|Different|First differs at output index1|Exact|

Both grouped modes first change the fourth generated token (zero-based index3):
solo token220, grouped token321. The solo top1/top2 logit gap is1.056606 and its
preference over token321 is1.311962. Grouped top1/top2 gaps are0.179018
(resident) and0.412056(staggered). These observed margins do not establish a
quality tolerance or faulty operator. Comparisons stop at that first token flip
because subsequent histories differ. Full [findings](../../target/scheduler-fixes/artifacts/qwen35-native-batch-probe/findings.json)
retain those limits; raw float arrays remain private on the host.

The experiment isolates decode grouping from residency for this prompt and
reproduces the server behavior through the native API. It does not identify
which operator is responsible or clear c128. Exact-source inspection finds
shape-dependent Metal matmul dispatch and multi-sequence recurrent operations;
these remain hypotheses, not attribution. The next step is calibrated native
activation/operator localization with identical prefixes before any further fix.
The public Skippy context exposes no eval-callback setter; llama context
construction supports cb_eval, requiring a separately registered diagnostic
caller and fresh calibration if used. No speculative production edit.

[Report](../../target/scheduler-fixes/artifacts/qwen35-native-batch-probe/batch-probe.json)
SHA256 `4573623d6abda450caa0a52f49a9d667afaeee6acd2b5a481f0bb142519d2964`;
[archive](../../target/scheduler-fixes/artifacts/qwen35-native-batch-probe/batch-probe-evidence.tar.gz)
SHA256 `10f650a91a1f5b319bac26cf54ad5b4084e78a95f5956e4c9b9a61904b84ad9c`.

Completed occupancy 7.25392h,$0. No active job remains; investigation heartbeat
is retained for the next control. All original failures, Flash/cross-engine
discrepancies, Coder SLO failures and C++ dependency blocker remain.

## 2026-11-25 14:55 UTC: activation localization control queued

Registered the public-context tracing on/off control in the [plan](SCHEDULER_FIX_PLAN.md).
Eight fresh processes,68lane completions,2176tokens. It links the exact original
native bundle; weights, library, Rust and production configuration are unchanged.
Direct greedy/no-EOG caller behavior and callback-induced graph changes require
exact archived token/text/full-logit calibration before activation interpretation.
Raw arrays remain private; selected2D first-column captures are limited evidence.

Just C++ build with-Wall/-Wextra/-Werror and ABI0.1.87/config240 smoke passed
against the supplied local same-source bundle; this is compile evidence only.
Python syntax, sealed source hashes, exact CIpin, shellsyntax and call-plan
mutation rejection checks passed. An initial local payload inspection regex
failed on embedded parentheses; AST parsing replaced it and validation passed.
No runtime job or result was discarded.

Submitted once as `job_4e1pvd3wgjp8vqv`; [spec](../../target/scheduler-fixes/qwen35-activation-probe.spec.json) SHA256
`862f9010f7e2c0d5de8412308d15b4ca6ad74ee021c4c7408b39459b7b0234db`. $0/3600seconds,no retries. Unrelated Metal CI
job`job_7zjgtqty0m5757r` occupies the host and remains untouched; probe queued.
The ledger records fetch/audit instructions and all hashes. No new runtime fix,
operator attribution or performance claim. Existing investigation heartbeat kept.

15:00UTC status: activation probe started14:57:49UTC and reached its second
process (`batch-plain`, first block). Only this job is active on the host;
coarse thermals nominal, unthrottled, memory pressure normal. No reported
job error. Final calibration and activation audit remain pending; dated
status/log/host hashes and elapsed occupancy updated. No duplicate submission.

## 2026-11-25 15:13 UTC: activation calibration passed; first projection localized

Job`job_4e1pvd3wgjp8vqv` completed15:11:19UTC. The [audit](../../target/scheduler-fixes/artifacts/qwen35-activation-probe/audit.json)
verified8processes,68lanes,2176tokens,exactmodel/library/header/native settings,
fullMetal/F16KV/cachesOFF and thermal gates. Every plain/traced mode matches
archived native lane tokens/text and all32subject full-logit hashes. Fresh
repeats, including every captured activation hash, are exact. The callback
preserved the registered behavior; no failure or tolerance was hidden.

The first captured difference is `node_15`, layer0 QKV `MUL_MAT` with weight
`blk.0.attn_qkv.weight` shape[2560,8192] and **identical** `attn_norm-0`
input columns. Embedding and normalization observations are exact. Across all
three captured decode steps, QKV max-absolute difference is2.861022949e-6;
RMS differences are3.1126e-7,2.9692e-7,3.0804e-7. Both repeats agree.
This is an observed first projection difference, not proof of incorrect
arithmetic or that this projection alone causes the eventual token flip.

|Decode output index|QKV max abs|Final logit max abs|
|---|---|---|
|1|2.861e-6|0.142678|
|2|2.861e-6|0.071377|
|3|2.861e-6|3.392808|

Each traced repeat captured2451solo and1947batch rows;504solo rows are
unmatched, principally shape-dependent views/reshapes.3D/4D operators remain
unobserved. Raw arrays stay private. [Findings](../../target/scheduler-fixes/artifacts/qwen35-activation-probe/findings.json)
retain these limits. The next diagnostic should compare this projection with
an independent FP64 accumulation of the same dequantized weights/input; public
`ggml_get_type_traits(...)->to_float` is available in the pinned headers.
This would be a diagnostic arithmetic reference, not CPU offload of generation
or a substituted model. It needs preregistration and exact output calibration.
No native/Rust edit or kernel-fault claim follows from the current evidence.

[Report](../../target/scheduler-fixes/artifacts/qwen35-activation-probe/activation-probe.json)
SHA256 `e203340c606fe415f3760bb6c099e4abeee6319e5ba79389cb3bc754698e2775`;
[archive](../../target/scheduler-fixes/artifacts/qwen35-activation-probe/activation-probe-evidence.tar.gz)
SHA256 `1afe7fa6826962d4bc22d2e9d23b30156c2ca56773018d33136adbdd81c23bc5`.
Completed occupancy 7.47887h,$0. Investigation heartbeat retained; all original
failures, other-model discrepancies, Coder SLO and C++ dependency limits remain.

## 2026-11-25 15:25 UTC: captured QKV arithmetic reference submitted

Registered a read-only CPUFP64 reference in the [plan](SCHEDULER_FIX_PLAN.md):
12saved cases,98304dotproducts,original GGUF tensor and private captured inputs/
outputs. No generation, serving offload, weight substitution or runtime edit.
Prior fullMetal/calibration evidence remains authoritative. Full arrays stay
private; hashes/error summaries and generic arithmetic bounds are diagnostic,
not a quality tolerance or kernel-fault verdict.

Just build with-Wall/-Wextra/-Werror/-ffp-contract=off passed; ABI smoke passed.
Offline synthetic8192-row GGUF produced exact known results and detected an
injected0.5error atrow5. Python/shell syntax, sealed payload/input hashes, exact
CIpin and budget checks passed. These are harness checks, not model qualification.

Submitted once as`job_5dsn85en18krfn6`; [spec](../../target/scheduler-fixes/qwen35-projection-reference.spec.json) SHA256
`5ee5e0ace76da2ce4acf3f9a6281e437e2956cb2cac590dc5f22b628f9f373b4`. $0/900seconds,no retries. Host was idle and nominal.
Ledger contains artifact/audit instructions and evidence hashes; heartbeat retained.

## 2026-11-25 15:27 UTC: first QKV arithmetic reference audited

Job`job_5dsn85en18krfn6` completed15:25:58UTC. The [audit](../../target/scheduler-fixes/artifacts/qwen35-projection-reference/audit.json)
verified12cases/98304dotproducts, original q5_K tensor/model/native/header/source
identities and exact archived private inputs/outputs. FP64 and compensated
reference hashes agree across modes/repeats for each input; error summaries
repeat exactly. No new generation or runtime change.

|Decode index|Solo max abs vs FP64|Grouped max abs vs FP64|
|---|---|---|
|1|1.361e-6|2.359e-6|
|2|1.928e-6|1.908e-6|
|3|1.904e-6|3.152e-6|

RelativeL2 errors range9.328e-8..2.474e-7. No row exceeds the preregistered
generic FP32 dot-product bound; maximum error/bound ratio is0.000158889.
Plain/compensated FP64 disagreement is at most1.421e-14, below the conservative
FP64 summation bound. These observations support small arithmetic differences
at this first projection; they do **not** establish a faulty kernel or clear
the full-model token mismatch. The bound is not a model-quality tolerance.
Shared native dequantization and unmeasured downstream operators remain limits.

Next inspect the first larger layer0 downstream departure and the unobserved
3D/4D recurrent path before choosing another control. Preserve exact-output
failures and all historical discrepancies; no speculative production patch.
[Findings](../../target/scheduler-fixes/artifacts/qwen35-projection-reference/findings.json) retain limits.

JY reported its final console-log artifact missing and used retained history.
That warning is preserved; both named evidence artifacts fetched with verified
SHA256, and the archive includes the reference/build/telemetry records.
[Report](../../target/scheduler-fixes/artifacts/qwen35-projection-reference/projection-reference.json)
SHA256 `3ae094a72c39d5086a0feb6225585aeec0b45853d96dd9555db2937d52cc9deb`;
[archive](../../target/scheduler-fixes/artifacts/qwen35-projection-reference/projection-reference-evidence.tar.gz)
SHA256 `7099dc231f22382e8aec636f40bc47d401f538e44039ea733080848e47f98168`.
Completed occupancy 7.50132h,$0. Heartbeat retained for remaining investigation.

## 2026-11-25 15:37 UTC: gate projection discrepancy localized

Read-only gate job `job_v8qnjxqeyrjr0vd` succeeded; [spec](../../target/scheduler-fixes/qwen35-gate-reference.spec.json)
SHA256 `328d564e304b090f1786db36d22659828d584e5e5f664559ca7106cefccce0ef`.
The [audit](../../target/scheduler-fixes/artifacts/qwen35-gate-reference/audit.json)
verified 12 cases / 49152 dot products, original Q4_K gate weights and private
capture hashes. All same-input reference hashes and repeated measurements agree.

|Decode index|Solo max abs vs FP64|Grouped max abs vs FP64|Grouped rows beyond generic FP32 bound|
|---|---|---|---|
|1|1.267e-6|0.0112593|87 / 4096|
|2|1.677e-6|0.0165824|285 / 4096|
|3|1.551e-6|0.0144079|228 / 4096|

Whole-input, whole-dequantized-weight and combined FP16 rounding controls do not
explain the grouped error. The generic bound is diagnostic, not a quality
threshold or complete contract for Metal's arithmetic. No production fix follows
yet. Source inspection identifies a more specific hypothesis: Q4_K dequantization
uses `xb->d / 16.h` for upper nibbles, whereas Q5_K uses `16.f`. Half-intermediate
rounding or subnormal handling is distinct from rounding the completed weights.
This needs a direct control against the saved packed weights and outputs.

[Findings](../../target/scheduler-fixes/artifacts/qwen35-gate-reference/findings.json)
retain limits. Report SHA256 `148aec92fadfc6702bce5d810be490afced7fd8117c2dd0fd99b0f4f0ebe6bf6`;
archive SHA256 `a27640050ed069214784bcd9232ad6a322e1b5d5c4a938b449a4e37f9b389d5e`.
JY's missing final console-log warning is retained; named artifacts verified.
Just warnings-denied build and synthetic exact/injected-error/FP16-rounding
checks passed before submission. No new generation, runtime edit or precision
change. Completed occupancy 7.51987 h, $0; heartbeat retained.

## 2026-11-25 15:39 UTC: intermediate-scale diagnostic submitted

Submitted `job_8y6cwz4h8jz41kp` once. [Spec](../../target/scheduler-fixes/qwen35-scale-reference.spec.json)
SHA256 `29ecdfe97601a705b29d028337c7ce6a9c3eea7899a5c7c04f464ca8299a31dd`.
The [registered control](SCHEDULER_FIX_PLAN.md) compares float, half-rounded and
half-rounded/flushed upper-nibble scale arithmetic over the same 12 saved cases.
No new model generation or serving changes. Just warnings-denied build passed;
4096-row synthetic Q4_K reference matched independent Python arithmetic across
all subblocks/nibbles and normal/subnormal scales. Sealed source/input hashes,
exact CI pin and shell syntax verified. $0 / 900-second lease, no retries.

## 2026-11-25 15:41 UTC: intermediate scale rounding explains captured gate errors

[Scale audit](../../target/scheduler-fixes/artifacts/qwen35-scale-reference/audit.json)
passed provenance/count/repeat checks: 12 cases, 49152 dot products per variant.
Grouped max errors versus the half-rounded intermediate scale reference are
2.511e-6, 1.791e-6 and 3.064e-6, versus 0.011259, 0.016582 and 0.014408 for
float scale division. Single-request outputs still match float-scale arithmetic
within 1.267e-6..1.677e-6. Flushing the half result's subnormals does not match.
All repeats agree. This strongly supports intermediate scale rounding as the
captured gate discrepancy's mechanism; direct isolated Metal calibration remains
needed before a production fix. It does not prove the only source of full-model
divergence or clear original correctness failures.

[Report](../../target/scheduler-fixes/artifacts/qwen35-scale-reference/scale-reference.json)
SHA256 `f809262e12e94a0645b9c875c9a41a904b89cc2f9ae037a66089a93d48ad5ed8`;
archive SHA256 `afbc8cbcb12642021f225a669c80cfdcfb17f79628f599f35db2a3fdb2ffbb16`.
Completed occupancy 7.53868 h, $0. No runtime edits.

## 2026-11-25 15:43 UTC: unchanged Metal operator replay submitted

Job `job_b0aey1zgbh0gthz`, [spec](../../target/scheduler-fixes/qwen35-gate-metal.spec.json)
SHA256 `4f15fd3170c3bb31a76a48498349b5384ae5b1dec415372419649b8d84bd938f`.
Twelve cases / 24 single-operator graph executions, original Metal library and
pinned gate weights. Saved input replicated across widths 1/16; archived first
column must match byte for byte before inferring model applicability. Raw output
arrays stay private. Just warnings-denied build and ABI smoke passed; sealed
payload/hash/pin/budget/shell checks passed. No production edit or generation.

## 2026-11-25 15:50 UTC: isolated Metal calibration and candidate regression

[Original Metal audit](../../target/scheduler-fixes/artifacts/qwen35-gate-metal/audit.json)
verified all 12 first-column outputs byte-exact against the full-model captures,
24 repeated graph executions and all replicated peers. This isolates the observed
gate discrepancy from scheduler/history. An initial audit expected the backend
string `Metal`; the pinned backend's actual name is `MTL0`. The auditor was
corrected against source, with the initial rejection retained; the caller already
required `ggml_backend_is_metal`. No runtime or evidence was changed.

Report SHA256 `0857335b33c80df467b04381f279b75f39234ec2d4eceb145dd88b4b829e4508`;
archive SHA256 `504a578b0bdb5896aabdb602ed3d22f22085a4df22f3ee164c2a6960e6df31be`.
Completed JY occupancy 7.55806 h, $0.

A separate exact one-term synthetic Q4_K Metal regression, using no model weights,
passes widths 1/2 and fails widths 3/8/16/17/32 with the supplied original local
bundle (exit 2, retained). Small upper-nibble scales round or underflow before
float assignment. This is a local development-device regression, not the primary
M1 Ultra model qualification. [Baseline evidence](../../target/scheduler-fixes/q4-scale-regression-baseline-local.json).

Staged an isolated ordered-queue candidate changing `16.h` to `16.f`, with a
native CTest regression covering seven dispatch widths and eight scale values.
[Patch](../../target/scheduler-fixes/q4-scale-native.patch) SHA256
`7b9ff3fd8f0f5e9dec6d2800e6f95e5de449e178d4e570fbedfa443b71d733f0`.
Prepared native source `e09c584b22b280a93903058129a76ac7b90df8d4`, patch digest
`fa2bed5b1593f41206352efdcc968669b945950b63d36261e43590fee1e2a544`.
Root native queue and the previously qualified combined scheduler changes are
preserved. `just native-full-replay metal` is running in the isolated checkout,
with supplied dependencies, network Git protocols disabled and dynamic linking.
[Validation log](../../target/scheduler-fixes/q4-scale-native-full-replay.log).
No full-model fix or performance claim until candidate native checks and exact
M1 Ultra operator/full-model retests complete. Other campaign failures remain.

## 2026-11-25 16:07 UTC: native suite and remote source prerequisites

The isolated native replay suite remains running; 179 / 337 checks
observed passed so far, not a final pass. The initial source inventory
`job_89gkz05ptv3eqtp` verified that the original upstream working files remain
on the M1 Ultra, but its Git objects are unavailable.
[Inventory](../../target/scheduler-fixes/artifacts/q4-native-source-inventory/source-inventory.json)
SHA256 `aa7d86391003f8a6087aec2b024b6650b21131f1e7843c47a91de56944c6d863`.
An expanded read-only cache inventory, `job_92mdb4wj2t01sft`, is queued behind
unrelated CI. No unrelated job was changed and no replacement source downloaded.

Prepared an offline source recovery harness that verifies each existing indexed
blob/mode and the complete pinned Git tree/commit before materializing source in
a new directory. Synthetic missing-object recovery and corrupt-byte rejection
passed; this has not yet validated the host's real source inputs. The initial
harness test exposed an index-only checkout issue; forcing checkout only after
verified tree identity fixed it, and the selftest passed.
[Selftest](../../target/scheduler-fixes/source-recovery-selftest.log).
Root native queue remains unchanged; candidate M1 Ultra qualification pending.

## 2026-11-25 16:11 UTC: offline source recovery queued

Native replay remains running, 191 / 337 checks observed passed so far. The
expanded dependency inventory remains queued behind unrelated CI. Submitted
`job_wapdshga3xvpv8q` once for offline reconstruction of the exact upstream
llama.cpp tree/commit from the existing indexed working files, into a new private
cache directory. The original cache and runtime are untouched. Any blob, mode
or tree mismatch fails; no source/model downloads or substitute dependencies.
[Spec](../../target/scheduler-fixes/q4-source-recovery-llama.spec.json) SHA256
`dd0c7178d3642628ae699c2815c6329a8b33255108532a255c585f2cc4f40019`. $0 / 900-second lease, no retries. The required pinned whisper.cpp
source must still be located before a candidate remote native build.

## 2026-11-25 16:30 UTC: local native suite passed; component build prepared

The isolated `just native-full-replay metal` exited0: **336passed,1skipped** out
of337. The new Q4 scale regression passed; the skipped long checkpoint test is
not qualification. [Full log](../../target/scheduler-fixes/q4-scale-native-full-replay.log)
SHA256 `6cdb4116cb5072b962b8d13b74498e78da140feba33bd4c704cbf370d17f9d2d`.

Offline recovery verified the exact upstream commit/tree and4174files on the
M1 Ultra; no whisper source was found. Prepared a paired Metal-only component
build retaining every non-Metal original library byte-for-byte. Local packaging
and exact regression passed all seven widths with the fixed component.
[Composition](../../target/scheduler-fixes/q4-component-local-composition.json),
[regression](../../target/scheduler-fixes/q4-component-local-regression.json).
The initial standalone ggml configure failure (missing ggml.pc.in) is retained in
[its log](../../target/scheduler-fixes/q4-component-local-build.log); the corrected
recipe configures pinned llama and builds only ggml-metal.

The [sealed remote spec](../../target/scheduler-fixes/q4-metal-components-prepare-failed.spec.json)
SHA256 `fe0b64d8c8b1ff68d803d295a6202f7c58273e6e87dc70c1a2c946d62f9c02db`
is $0/4200seconds,no retries. It requires original failure and candidate exact
pass, source/library/retained-file hashes and nominal thermal boundaries. Root
native queue remains unchanged; M1 operator/full-model candidate tests pending.

Submitted once as `job_gt4v7ds1aa316gh`; ledger updated.

## 2026-11-25 16:34 UTC: source-preparation harness correction

The first component job failed before compilation: recovered Git objects and
working files were valid, but the repository had no `origin` entry required by
`just llama-prepare`. [Failure log](../../target/scheduler-fixes/artifacts/q4-metal-components/baseline-prepare.log)
and [immutable failed spec](../../target/scheduler-fixes/q4-metal-components-prepare-failed.spec.json)
are preserved. Added a local-only origin to each new private source copy; network
Git protocols remain disabled. The complete offline reconstruction and both
Just patch queues now reproduce original6704435209b0 and fixede09c584b22b28.
[Preparation selftest](../../target/scheduler-fixes/q4-metal-prepare-selftest-v2.log).

Submitted corrected job `job_n57z4wma5953ypf`, spec SHA256
`3d849fe02a2a4c5bc2f3b59e586eb47fe36cba415a498003145804b3ea34e5a0`.
This is a corrected harness submission, not an automatic runtime-result retry.
Same paired components, exact regression, $0/4200second cap and no generation.

## 2026-11-25 16:47 UTC: M1 component arithmetic qualification passed

Corrected build `job_n57z4wma5953ypf` succeeded. The
[component audit](../../target/scheduler-fixes/artifacts/q4-metal-components-v2/audit.json)
verifies original mismatch counts0/0/9/24/48/51/96 and fixed all-zero across
widths1/2/3/8/16/17/32, exact source/patch identities, matching compiler/SDK,
nominal thermal boundaries and22non-Metal files byte-preserved. No model ran.
Report SHA256 `ad8644308d215ff214019c35d05e39eaa79fc7dece9b4f73251ae9f4f66450aa`.
Baseline manifest `5c3d2e5e50f05a60bd6320d16b0d8253227269aec7267042b06b0e8becd07698`;
fixed `b7ca3592c48d87948aa8075c106317a83447d8f92d57533639032141603b1e71`.

Both build logs retain a missing upstream-parent message from CMake's Git
revision-count probe and an OpenMP warning. Source commits/trees were verified;
only Metal was rebuilt, existing CPU/runtime libraries retained, and both builds
exited0. No history or replacement dependency was downloaded. Rebuilt baseline
operator/full-model calibration remains a hard gate for the next experiment.

Prepared the registered24operator-case/eight-process model replay. Its audit
selftest reuses archived evidence (not candidate generation), verifies2176tokens,
and rejects false offload, false baseline calibration and a substituted bundle.
[Selftest](../../target/scheduler-fixes/q4-retest-audit-selftest.log). Python and
shell payload checks passed; payload will be staged in three hashed chunks.

Paired operator/native replay submitted as `job_egkj8kp5xj55d6q`, [spec](../../target/scheduler-fixes/q4-component-retest.spec.json) SHA256 `ba669d698bec0bbed148650c0e28b175fcdd1c8250d9d0679b3e9770ff211f80`. All three staged payload receipts verified. Model qualification remains pending.

## 2026-11-25 17:05 UTC: Q4 fix passes registered native model retest

Job `job_egkj8kp5xj55d6q` succeeded and its
[independent audit](../../target/scheduler-fixes/artifacts/q4-component-retest/audit.json)
verified eight fresh processes,68lanes,2176generated tokens,24operator cases,
full Metal/native/request/hash/thermal gates and exact repeats. Rebuilt baseline
matches every archived operator first column and every subject token/logit hash.

| Captured grouped gate input | Original max error vs FP64 | Fixed max error vs FP64 |
|---|---:|---:|
| Step1 | 0.0112593 | 0.0000025581 |
| Step2 | 0.0165824 | 0.0000018177 |
| Step3 | 0.0144079 | 0.0000033453 |

Both repeats give identical results; solo projections are unchanged. The fixed
native model generates identical32token continuations across solo and all16
grouped lanes, in both fresh repeats. Original diverges at token index3 (fourth
token). **Solo/grouped logits remain non-identical.** No quality tolerance was
introduced; the exact synthetic regression, operator error reduction and observed
token equality are separate evidence. This establishes the arithmetic fix and
clears this native prompt's token mismatch, not all serving/c128/cross-engine or
other-model failures. Root native patch integration and serving retest follow.

Report SHA256 `2eb0eac64c668192ae99f33c6ed4fc21522d681f13605d5225aa38fbcc5f4242`;
archive `025fb9434bfb7ef386c2614a1cc158083b233c966af1756b8928c77558a47bb3`.
All74exported files were safely extracted; raw arrays remain private. $0.

## 2026-11-25 17:12 UTC: exact native fix integrated; serving replay prepared

Added the qualified0046core patch and native regression target to root. All21
existing Rust file hashes are unchanged; [integration record](../../target/scheduler-fixes/q4-root-integration.json).
Root `just llama-prepare` resolves the same tested e09c584b22b28 revision using
supplied source without network fetch; [preparation log](../../target/scheduler-fixes/q4-root-prepare.log).
The isolated full native suite and M1 operator/model checks remain the evidence
for these exact native bytes. No additional Rust change or ABI change.

Prepared six-process/204response streaming serving replay across original,
native-fixed and combined-fixed arms. Caches off, original settings and both
starting histories retained. [Audit selftest](../../target/scheduler-fixes/q4-serving-audit-selftest.log)
uses archived controls and rejects binary/offload/cache mutations while keeping
original output differences; it is not candidate hardware qualification.

The serving preflight caught a manifest packaging issue before model launch:
the actual CLI rejects diagnostic top-level `component_rebuild` metadata. The
[loader self-test](../../target/scheduler-fixes/q4-serving-manifest-selftest.json)
verifies rejection and acceptance of a serving view that moves identical
provenance into supported `build` metadata. All25runtime files retain exact
bytes; the source manifest is preserved. No Rust schema or native edits.
The [v2 spec](../../target/scheduler-fixes/q4-serving-retest-v2.spec.json)
preregisters both serving manifest hashes. Original stages succeeded and remain
recorded but are superseded; their model job was never submitted.

## Q4 streaming integration qualification complete

Job `job_24az19bhc1k4a8e` completed at17:39:00UTC. The
[independent audit](../../target/scheduler-fixes/artifacts/q4-serving-retest-v2/audit.json)
verified204responses in24waves across six fresh processes. The rebuilt-original
arm matches every archived serving output, including its concurrency difference.

| Configuration | Responses | Match its fresh serial text | Match fixed direct-native text |
|---|---:|---:|---:|
| Original server + original Metal | 68 | 4 | 0 |
| Original server + fixed Metal | 68 | 68 | 68 |
| Combined scheduler + fixed Metal | 68 | 68 | 68 |

Both fixed arms agree across c1/c16, repeats and reversed starting histories.
The exact model/binary/component/serving-manifest hashes, request bodies and
completion/cache counters passed. All33layers offload to Metal,16lanes each
8192context,F16KV,logical/microbatch512,cachesoff. Thermal boundaries are nominal;
all24wave traces account for512prefill and31decode tokens per request, with zero
reported telemetry drops/export errors. Serving-step traces are not independent
native kernel attribution.

This qualifies the registered one-prompt serving integration, including the
combined scheduler. Fixed text need not equal the original faulty text; it
matches the fixed direct-native replay. Native solo/grouped logits still differ.
The original heterogeneous c128 workload, other models, cross-engine quality
and performance gates are not cleared. No new throughput claim follows from
this diagnostic. Original failures and missing-GoogleTest C++ facade limitation
remain.

Report SHA256:`6467c5e025336b75a939c9d0a50e819feb79750ed88818773e7da023435c9ee7`;
archive:`5285eff51746ea06b93bc789581c598defb6f2e17dfdec56df89ed417cd175e5`;
audit:`f9f0c794f869a7a36ed3c2e5b5cb22eb7658db70867b556e5c8b0c0558a3b5d1`.

## 2026-11-26 00:44 UTC: fixed-Metal heterogeneous c128 retest audited

Job `job_txcg43nzentnsd4` succeeded at 00:44:03 UTC, $0, on
`macstudio-2l0352`. Its [registered spec](../../target/scheduler-fixes/q35-c128-fixed.spec.json)
SHA256 `bbad4abe6a30e1385378458b4ff8bcbec374de4959d9976cdbb00bd105ce9d25`
used the original Qwen3.5-4B heterogeneous c128 fixture, inferencekit 0.1.1,
four balanced fresh-process rounds and frozen 791.173594 ms reference.
The two stage jobs and content-addressed payload verified; the
[independent audit](../../target/scheduler-fixes/artifacts/q35-c128-fixed/audit.json)
reconstructed and verified their exact model pin, sealed scripts/build inputs,
original and combined binary identities, original/fixed Metal manifests and
unchanged non-Metal libraries. All 16 cells and 2,048 requests completed with
32 output tokens each; 16/16 cache counters passed. Native logs verify 33/33
Metal layers, 16 serving lanes with 8192 tokens each, F16 KV and logical/micro
batch 512. Prefix and disk caches were off. Saved thermal boundaries and sampled
host observations are nominal; the [thermal audit](../../target/scheduler-fixes/artifacts/q35-c128-fixed/thermal-observations-audit.json)
finds no alert overlapping a client window. All requests and outputs have
verified evidence digests; no export errors or dropped-event messages were found.

[Full per-arm statistics](../../target/scheduler-fixes/artifacts/q35-c128-fixed/statistics.md)
and [paired ratios](../../target/scheduler-fixes/artifacts/q35-c128-fixed/statistics.json)
retain every round. The compact medians of four per-round values are:

| Arm | Complete | TTFT p95 ms | E2E p95 ms | TPOT p95 ms | Output tok/s | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|
| Skippy original server + fixed Metal | 512 | 62,297.7 | 66,416.5 | 252.0 | 60.84 | 0/4 |
| Skippy combined server + fixed Metal | 512 | 62,241.2 | 66,364.0 | 251.7 | 60.88 | 0/4 |
| Vanilla separate slots | 512 | 73,537.9 | 77,387.0 | 283.8 | 52.29 | 0/4 |
| Vanilla unified | 512 | 72,571.6 | 76,397.3 | 281.3 | 52.97 | 0/4 |

The combined Skippy server differs from the original Skippy server by only
0.08% in paired output throughput (ratio 1.0008, empirical paired-bootstrap
95% interval 0.9985–1.0023); this does not meet the registered 2% effect gate.
Its throughput is descriptively 16.4% above vanilla separate slots and 14.9%
above vanilla unified on this workload. These cross-engine timings do not
establish an equivalent-output speedup: 12 exact comparisons still differ.
`load-0100` differs from both vanilla arms in all four rounds; `load-0190`
differs from separate slots in all four rounds. All fixed-run outputs are stable
across rounds. Relative to the archived failed c128 run, both Skippy arms change
`load-0100` and `load-0190` in every round, vanilla separate slots changes one
`load-0000` response, and vanilla unified is unchanged. The original nine
cross-engine mismatches remain preserved in the closed campaign; the new run
has twelve, with a different prompt/arm pattern. The fixed Q4 Metal component
clears the registered one-prompt c1/c16 integration, but does not clear c128
cross-engine parity.

Every arm fails the registered TTFT p95 ≤30 s SLO in all four rounds (TPOT p95
is below 1 s). Thus the work has a qualified arithmetic fix and complete c128
measurements, but **no overall scheduler/engine winner or c128 quality/SLO
clearance**. Qwen3.6 cross-engine/concurrent parity, Flash serial `load-0001`,
Coder latency SLO, historical Thoughtworks indices 16/227 and old hybrid-MoE
differences also remain unresolved. No tolerance, queue or model pin was silently
changed; no further runtime edit is justified by this retest alone.

Fetched artifact SHA256: suite `f255c597242075f52deb9c3f81d08ca9d0fd4ce95a8c84271267377ac20a1b8b`,
fixtures `422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e`,
doctor `b0b99af5600e8c194ef316c86dc38fad4892b5ae31fc451c557c5059590fc1cb`,
archive `d5f0ce4fee11310eb4aed57db101faabbfd3f7aa99e227e8c0b5694d48646594`.
Audit SHA256 `8ebbd8e5b99fb357f2fc290774b754639fb50926a020c9c554325b3bb444824e`;
statistics JSON `24b519ae34c5c9c5c718d87f157d0c6aa799e98c91051db922de91852b2cc08f`.

## 2026-11-26: owner selected a separate prospective task-quality gate

The owner agreed to assess a bounded performance claim through a separately
registered task-quality rule. The [version 1 exact-output gate](SCHEDULER_NONREGRESSION_PROTOCOL.md)
and all its recorded failures remain unchanged. The prospective
[version 2 protocol](SCHEDULER_TASK_QUALITY_PROTOCOL.md) states a 2% adverse
task-outcome bound against each vanilla layout at every claimed load, with
fresh vanilla A/A controls and an independent capacity-SLO verdict. It does
not clear the confident Qwen3.6 distribution disagreement by task score alone.

The offline 256-item source fixture and deterministic scorer are sealed with
SHA256 `ccf46ac48e9257d29b8bcf0ba188bbc01e1fe380badfdace807fb524b2bca302`
and `0b8b783c800fa15021327dc406c930728bc96bd455bcfbe0644f808fbc00807c`.
The scorer selftest, independent answer checks, Python compile and synthetic
256-item materializer contract check pass. The materializer SHA256 is
`4a5aa78ee90e5f52e28b28c213d9e994a876dc00b42d244622f5b71286450670`.
Model-specific 512-effective-token materialization, quality runs, balanced version 2 timing, and a four-model
non-regression claim have **not** occurred. Three Coder screen staging jobs
remain queued while the host runs unrelated CI; no model job has been
submitted for that screen.

## 2026-11-26 20:40 UTC: current-main port and Coder screen

The three Coder transport stages later succeeded; their fetched receipts
match all three sealed chunk hashes. The registered $0 balanced screen was
submitted once as `job_trqshv7hbbqdmzv`, spec SHA256
`5ed0241dd4743447ccaad72c12df3f2a07237391c599a7c437ef588c8d15a204`.
It is running on `macstudio-2l0352`; no result is yet promoted.

Current `main` upgraded llama.cpp to `5bacb7c868a2bc98682de15f447fc1ce17c9c49c`.
The Q4 Metal patch is carried unchanged in ordered slot `0060`, with the
native recipe regenerated at patch-queue digest
`4995a13499973a2363764a678316cbdce3f825125b1253ecee13801e33497c51`.
Offline `GIT_ALLOW_PROTOCOL=file just llama-prepare` passed without a download.
Offline `just lint` passed on the merged source. Serial offline Rust workspace
tests passed 4,869 tests in 114 groups with 314 ignored; the native Metal full
replay result and its harness repair are recorded below. The previously quoted M1 Ultra model
results were measured on the earlier source and cannot qualify this port.
The [source attestation](pr-evidence/source-attestation.json) distinguishes
identical patch bytes from pending port hardware qualification. Draft PR #233
contains the four historical tables with an explicit UNPROVEN verdict.

## 2026-11-26 21:31 UTC: current-main native replay harness repair

The first offline `just native-full-replay metal` on the upgraded source exited
8 after CTest selected `test-skippy-chat` but could not find its executable.
The test had been registered by the native CMake queue; the full-replay
`BUILD_TARGETS` list omitted it. The original log is retained at
`target/scheduler-fixes/main-port-native-metal.log`. This is a harness build
failure, not a passing test or model result. The remaining selected tests
reported pass or the registered resident-checkpoint skip.

`scripts/build-llama.sh` now includes `test-skippy-chat` for the platforms where
CMake registers it. `bash -n` and `git diff --check` passed. An offline Just
full-replay retry built the missing executable and is running CTest; its log is
`target/scheduler-fixes/main-port-native-metal-retry.log`. Do not promote a
native replay pass before that command exits zero. The current-head CI
inference qualification jobs and quality canary have passed, but they do not
establish matched performance or the prospective task-quality result.

## 2026-11-26 22:03 UTC: current-main native Metal replay passed

The corrected offline `GIT_ALLOW_PROTOCOL=file CARGO_NET_OFFLINE=true just
native-full-replay metal` exited zero on the local M5 Max. CTest selected
341 tests: 340 passed, one (`skippy_resident_checkpoint_gpu_long`) was
registered as skipped, and none failed. In particular, `test-skippy-chat`
compiled and passed, as did `test-skippy-q4-scale-metal` and
`skippy_backend_ops_kv_rows`. The retained retry log SHA256 is
`20aaaa993df6df855f13825647683f05690e90192ae547c79e0a9416beda1e72`.
This clears the local current-source native replay gate. It does not establish
M1 Ultra full-model output, task quality, or performance on the ported source.

## 2026-11-27: live task prompt materialization

The first live tokenizer harness failed before fixture qualification; its
verbose/native evidence was incomplete and Coder could not reach exactly 512
tokens for `extraction-029`. The corrected four-model job
`job_1dsm6yt99fm11wm` (spec SHA256
`652c851fa2bce0699f5311d484385e230df36c06e7758a2a967b35ebfe07cb1f`)
also failed overall because Coder's nearest prompt length was 511. Its
independent artifact audit passed the other three exact CI models: 256/256
prompts each at 512 effective tokens, with pinned shards, full Metal, F16 KV,
16×8192 context, batch/microbatch 512, caches off, and nominal thermals. The
four-model audit SHA256 is
`b706402f66dde39efda33545fe75358f5425c319a34118134472342e3644b4d9`;
the failing Coder result remains in it.

The separate Coder-only neutral-padding job `job_17r5xdjt2cz8ys4` (spec
SHA256 `b84b5e68e8a12dcf95ec32db6d169d261b4b0d2063f442139b5e15cc7e6cd2a7`)
passed its independent audit for 256/256 exact-512 prompts under the same
native, pin, cache and thermal gates. Fourteen prompts required one neutral
space before the unchanged task; the other 242 needed no suffix. Its audit
SHA256 is `eb6d74c3d81bf4e8ecfb5c49af29d1beaaaf0e900eabd74c6346acf3101ca528`.
The durable materializer now matches the Coder-tested source at SHA256
`57cab8a1d1b0fc5a000ad5022ae771e984f60c8f576fc9e79b70eced5fb0d6ae`.
This is fixture construction only. No model outputs have been scored, no
current-source timing cells have run, and four-model non-regression remains
**UNPROVEN**.

## 2026-11-27: first prospective quality control fails baseline gate

The current PR head `360b1bce` was built through `just build metal` in the
source-pinned $0 M1 Ultra workspace. Its sealed 32-file product manifest and
native source identity passed independent hash verification. The first version
2 quality control, unchanged vanilla separate slots on exact CI Qwen3.5-4B,
ran as `job_v7akh7s4ba6k0k5` (spec SHA256
`74880f5dc900fdb0190d6de6db0172c39a909cd2658cac10fcad172defb20601`).
The independent audit verified 256/256 complete requests, exact 512 input and
32 output tokens each, 33/33 Metal layers, F16 KV, 16×8192 context,
batch/microbatch 512, cache off, exact model/server/fixture/request hashes,
and nominal thermal boundaries. The raw response archive SHA256 is
`fc7e46392265037de06a669c3124199940a572f57d9d9531c399a896514db1d4`.

The preregistered final-line scorer marked **0/256 correct**, below its
required 200 baseline-correct items. Only three final lines were parseable
integers, and all 256 responses ended at the forced 32-token length. Raw
completions often continue or repeat the prompt. This is a failed baseline
quality gate, not evidence of a Skippy regression or of model task quality in
normal chat use. The [compact result](pr-evidence/task-quality-q35-v2-control.json)
preserves the identifiers and hashes; the full raw responses and independent
audit are retained locally under
`target/scheduler-fixes/artifacts/task-quality-q35-vanilla-a/`. No A/A,
candidate quality, or version 2 timing results are promoted after this failed
gate. The four-model non-regression claim remains **UNPROVEN**.

## 2026-11-27: unscored chat-boundary diagnostics

Two separate $0 Qwen3.5 vanilla chat probes used four selected tasks from
each of the four source strata, the exact model and server pins, full Metal,
F16 KV, 16×8192 context, batch/microbatch 512, cache off, and nominal
thermal guards. These 16-item probes are exploratory and are **not** version
2 scores. The first (`job_nh0gwp3ey5y957t`, spec SHA256
`57ed8a4ebb82d989972013145a1f508407f7c2a6769d8feddf265ba0a2fe9a66`)
used ordinary chat formatting: all 16 requests completed, but the chat
template made each prompt 521 tokens and all 32 output tokens went to
`reasoning_content`, leaving final `content` empty. The second
(`job_zjyzva1a6azgft1`, spec SHA256
`237cbe6e8674a846c0d7699a76ac3320d26356610756249186321d78f3e6aeab`)
set the pinned server's documented `chat_template_kwargs.enable_thinking=false`.
All 16 requests completed at 523 input tokens; eight final answers matched
their registered integers, four arithmetic and four extraction, while none
of the four ordering or four assignment tasks matched. The
[compact diagnostic record](pr-evidence/task-quality-chat-pilots.json) binds
both raw archives and response hashes. All raw outputs remain in the ignored
local artifacts. Neither changed request shape can retroactively repair the
registered 512-token v2 baseline failure, and these small results cannot
justify a quality or speed claim. No version 3 scored run has been registered.

## 2026-11-27: full unscored chat diagnostic and held-out repair

The complete original 256-task fixture was then run as an **unscored**
nonthinking-chat diagnostic on exact CI Qwen3.5 vanilla separate slots:
`job_7xb63ccte3ekrhv`, spec SHA256
`aef77c61b15d60e2602a698b5e24097f969af99d9482805b770af852102b33e7`.
Its first JY spec was rejected before job creation because the harness
version label exceeded 64 characters; the rejected spec SHA256
`d78523aa169956dea54faef05ea37e1f637abd346ad77c77b4a1fa36a67cc886`
is retained. The submitted job completed 256/256 at 523 input tokens,
with the exact pinned model and server, 33/33 Metal layers, F16 KV,
16×8192 context, batch/microbatch 512, cache off and nominal thermal
boundaries. The local independent audit at
`target/scheduler-fixes/artifacts/task-quality-chat-no-thinking-full-explore/audit.json`
has SHA256 `7d8d18dd2cd015ef70c01b8bda209efbb0890a21698254ff99acdc51e619a981`;
the raw response archive SHA256 is
`b6380071038a90bd3d0c02739e90517d16beb7a74e7be478819999d7c89da66f`.
The original scorer's final-line rule matched 139/256 answers: arithmetic
64/64, extraction 64/64, ordering 9/64, assignment 2/64. This confirms the
original 256-item fixture misses its 200-correct baseline prerequisite even
with final chat answers. The exploratory score is not a version 2 or 3
quality result. The [compact evidence record](pr-evidence/task-quality-chat-pilots.json)
now includes it.

The separately [registered version 3 protocol](SCHEDULER_TASK_QUALITY_V3_PROTOCOL.md)
retains all four task families and difficulty rules but uses a new held-out
seed and 512 unique tasks, with nonthinking chat and exact 512 **rendered**
input tokens. Its source manifest, generator/scorer, and materializer are
sealed before any version 3 model-specific prompt or scored output. The
synthetic 512-task materializer contract and scorer selftests pass; these are
not model qualification. No version 3 scored result or current-source
performance result exists yet. Version 2's failure and the historical
cross-engine discrepancies remain intact.

Four source-pinned, $0, model-specific version 3 **prompt materialization only**
jobs were submitted: Qwen3.5 `job_4mnrckk6j1ppe45`, Coder
`job_z8sxnjxrah9jcq3`, Qwen3.6 `job_3c0gwq4g7g9gdw4`, and Flash
`job_r8d6dqd53bqw8pp`. The sealed spec SHA256 values are recorded in the
ignored fix ledger. These jobs calibrate native chat tokenization and attempt
to form exact-512 rendered prompts; they do not produce scored task answers.
Qwen3.5 completed its independent model, source, prompt, native setting,
cache, and thermal audit: 512 distinct exact-512 rendered prompts, 33/33 Metal
layers, and nominal thermal boundaries. Its prompt archive SHA256 is
`242d7fb4ba12d657e8dc135a7a3b15c4bc0688ed34fa0de747680ed7cdcef3bc`;
the local audit SHA256 is
`e05a8dd0bc013429827abdc24a69292241b8b5c20f1f4aa799740d6f5371b9ed`.
Coder and Qwen3.6 subsequently passed the same prompt-only audit: each has
512 exact-512 prompts, full Metal, the exact pinned shards, and nominal
thermals. Flash's first attempt `job_r8d6dqd53bqw8pp` failed **before prompt
generation**. Its 93 GB split-shard hashes passed, but the shared launcher
resolved the first staged shard name to an opaque blob name; llama.cpp rejected
that name as an invalid split file. The failed job, spec, and server log are
preserved. A new sealed harness-only attempt `job_98bffc3r9w9wgr5` passes the
verified snapshot filename to the same pinned server. No model, weight,
precision, native code, or runtime changed. A completed prompt archive is not
a scored quality result.

The first prospective scored-cell spec was rejected **before job creation**:
embedding the complete prompt corpus made `runtime.command` 96 KB, above JY's
16 KB schema limit. Its spec hash and rejection remain in the fix ledger. The
independently audited Qwen3.5, Coder and Qwen3.6 prompt archives were
committed as compressed, hash-checked synthetic fixtures under
`pr-evidence/`; the scored runner reads the exact archive from its source-
pinned checkout. Qwen3.5 and Qwen3.6 materializations yielded identical
prompt bytes under their respective pinned native tokenizers. The corrected
Flash job then passed an independent 512-prompt, 49/49 Metal audit (local audit
SHA256 `592a08749bbff0a19659ebb9d717ad9277c3814e4397b0fbcfa44d66c95cfab6`).
All four model-specific compressed fixtures are bound by the
[archive manifest](pr-evidence/task-quality-v3-prompt-archives.json); this is
still prompt-only evidence. The first prospective Qwen3.5 vanilla separate-
slot c1 baseline, `job_spna4q4ssr7f5r0` (spec SHA256
`f3a3ca2ba1115bc8053f66a26b250c8a015404cab00c0d4d4fc7f8c6ab4e3eae`),
completed 512/512 scored requests on the pinned native Metal server. The
independent audit verified 512 input tokens per request, zero cache use,
33/33 Metal layers, F16 KV, 16×8192 context, batch/microbatch 512 and
nominal thermals. The registered scorer found **281/512** correct: 127
arithmetic, 128 extraction, 20 ordering and 6 assignment. This clears the
200-correct comparator prerequisite for this one c1 arm. Its raw response
archive SHA256 is
`d5b8db13e8ad4dd7dfe35e38a0041bc4199145d508fc22d98099d67e267fff1e`;
the independent audit SHA256 is
`1a02a71de13a8d67f9363b4f43a2c895b8070dc0899950c867b3bd810efe1528`.
The current Skippy Qwen3.5 c1, vanilla unified Qwen3.5 c1, and separate-slot
Coder/Qwen3.6/Flash c1 jobs have been submitted as distinct sealed $0 jobs.
No candidate comparison, A/A repeat, loaded quality or current-source timing
gate has passed yet. This packaging change does not alter task values,
scoring, requests or any native runtime.

## 2026-11-27: first current-Skippy v3 c1 attempt thermally invalid

The source-pinned Qwen3.5 current-Skippy job `job_1w80qfakyw6spb3` (spec
SHA256 `a17bdeded66d4b9691dc83f7ca63d26b56810dfd1af6ed249e3a74d20f3db339`)
produced all 512 responses but failed its end-of-run thermal gate: native
pressure rose from 0 to 2. The job is **failed**, and none of its outputs is
promoted as a valid quality cell. Its failure archive SHA256 is
`cd11fe31029e0e4b4f26ab550bdce3f1f3e159bb6a59eea94bc5a8cac9566586`.
An exploratory score of the complete, token-shaped responses found 279/512
correct and two adverse tasks among the 281 correct vanilla separate-slot
items. The registered exact one-sided 95% upper bound is 2.2234%, so that
diagnostic is **UNPROVEN** even apart from thermal invalidation. The two
adverse IDs are `assignment-096` and `assignment-097`; the diagnostic score
SHA256 is
`7c5702d2ee259650eaf92e10ce256909425aef50bb5afe51b852eb8929dd4ee7`.

That exploratory comparison exposed a scorer implementation bug: its old
lower bound printed 7.053% for 2/281, above the correct 2.2234% upper bound.
The original binomial CDF underflowed near probability one. The
[version 3 protocol](SCHEDULER_TASK_QUALITY_V3_PROTOCOL.md) records the
source-hash amendment and direct-sum verification. The registered tasks,
parser, exact confidence rule and 2% margin are unchanged; no invalid cell is
rescued by this correction. The vanilla unified Qwen3.5 job
`job_kh1ens6tgx5j7yk` then failed its nominal thermal gate after 256/512
responses (pressure 0→1; archive SHA256
`45204e5861fcac1072f27fab3806a600b94e214f00127dca5b6ff03b275b038b`).
The Coder vanilla separate-slot job `job_hs6v7f0w2s6tj9q` independently
failed at the same 256-response boundary and pressure level (archive SHA256
`c9f16cf8b7e9a3c20c632dd8c7f3f25af7db365f9184eda45f436f65417d1fbb`).
The still-queued Flash job `job_y8m7vfvj2wv05xt` was cancelled before any
run; its sealed spec remains in the ledger. Qwen3.6 was already running.
These are capacity/thermal invalidations, not scored model failures. A
prospective matched c1 cohort with 120-second idle intervals every 64
responses is now registered in the version 3 protocol. All three arms will
use that same cadence and be compared only within that cohort; a further
thermal failure remains a capacity blocker.

The first matched cooled Qwen3.5 vanilla separate-slot cell
`job_445yb8c70gs6d1a` passed its independent audit: 512/512 complete,
seven recorded pauses of at least 120 seconds, nominal pressure at every
block and process boundary, exact model/server/request identities, full
33/33 Metal layers, F16 KV and caches off. It again scored 281/512; its
parsed and raw outputs agree with the original valid control on all 512
tasks. Archive SHA256
`951ed23ade3a4da53e43de6b0f4dcbdfd61035921c29bcb9a4c5b4f99d0f474b`,
audit SHA256
`73d13ad5ed105403185559f4527debea4033a132540d7a45f178debec6910b04`.

The matched cooled current-Skippy Qwen3.5 cell `job_2sqtdmj7xp8akkx`
also passed all 512 native, cache and thermal checks with the same seven
pauses. It scored 279/512. Against the cooled vanilla separate-slot
comparator, `assignment-096` and `assignment-097` are adverse among 281
baseline-correct tasks. The registered exact one-sided 95% interval is
0.1266%–2.2234%, crossing the strict 2% margin: **UNPROVEN**. Its archive
SHA256 is
`da96af0ad3a5357e30e03ea67ef410fcf3afe555a6454f345225c9b9e42a5a92`
and audit SHA256 is
`8ce26d9d69e6e9f92260af7211eb7656f35395197fcaa2fb314bdbbee77c3600`.
Raw and parsed outputs agree on all 512 tasks with the earlier thermal-invalid
Skippy diagnostic, establishing repeatability but not changing that attempt's
invalid status. The matched vanilla unified cell `job_fynjrbshsntma0f` also
passed: 512/512 responses, seven ≥120-second pauses, nominal pressure at all
boundaries, 33/33 Metal layers, F16 KV and caches off. It scored 281/512;
its parsed and raw answers exactly match the matched separate-slot control
on every task. Its archive SHA256 is
`698cf8661aba96d27142c3e3df7de367f71857ed169247c2c53499ba9d5288e6`
and audit SHA256 is
`5a2c30615b4b03073a6d51858b342f72e2a5b163108ac0421e32f8f156dd9962`.
The same two adverse IDs therefore give **UNPROVEN** versus both vanilla
layouts at the registered 95%/2% boundary. No current-source fixed-output
performance cell is promoted while this quality gate remains unproven; a
measured cause for the two answer differences is still needed before any
targeted runtime edit.

The [version 3 protocol](SCHEDULER_TASK_QUALITY_V3_PROTOCOL.md) now registers
one independent 512-task confirmatory extension before any extension model
response. Its manifest SHA256 is
`b7b92d1aac38b7391b88b2b42acaa538be7e6917630db9ec4da52478ab61b23d`.
It preserves the first-half **UNPROVEN** verdict and uses a stricter one-sided
97.5% bound for a single pooled 1024-task final look, with no further
extension. The original scorer manifest and baseline score hashes remain
unchanged under the amended generator. No extension prompt, score, A/A, or
timing result exists yet. The exact Qwen3.5 extension prompt-only job
`job_6qq8wfgp3ysax0c` ran at PR source
`431b570c91d876d7826cb808217bb1cb364fff65`, spec SHA256
`e06aa9fc7587689620f02d6047366c6241265e3a69cf45fe218f73a57501c539`,
and local runner SHA256
`a4acd3877f4a3b2dcc169c39a0d9c118ba0e6e1239f6e81d2d9cd071372700b2`.
It completed and passed independent native model, source, chat-calibration,
512-token, full-Metal, cache and thermal audits. The raw prompt SHA256 is
`4472d7e0d91ca34894742c93ac3019febf9994c3b8b4bc5cb1e951fff3a474b2`,
compressed source fixture SHA256
`d33f10f14e93f27214fe761e95329a82afd7235013fa74519e0cdd056c6ea8d6`,
evidence archive SHA256
`08cc87462e385d567af5e84ea8053feae530c380bd8988aa1ae0cd2beaa862ac`.
No extension task answer has been generated or scored.

## 2026-11-27: Qwen3.5 c1 confirmatory quality gate passed

The independently registered 512-task extension and all six fresh, reversed-order
vanilla A/A and current-Skippy repeats have now completed. Each of the 12
Qwen3.5 c1 cells returned 512/512 responses with seven recorded cooldowns,
nominal thermal boundaries, the exact CI model and source-pinned binaries,
full 33/33 Metal offload, F16 KV, 16×8192 context, batch/microbatch 512 and
both caches off. Every repeat reproduced its first cell's **raw text** on all
512 tasks. The extension vanilla separate, vanilla unified and current-Skippy
arms each scored 273/512; the two vanilla layouts matched raw text exactly.

The original 512-task comparison remains **UNPROVEN**: two adverse items among
281 baseline-correct tasks have a one-sided 95% upper bound of 2.2234%, above
the registered strict 2% limit. The extension has zero adverse items among 273
baseline-correct tasks. The pre-registered single pooled look has two adverse
items among 554 baseline-correct tasks, `assignment-096` and
`assignment-097`, with a one-sided exact **97.5% upper bound of 1.29796%**.
The pooled **c1 task-quality gate passes** against each vanilla layout. Raw
cross-engine text differs on 15 original and seven extension tasks per layout;
these exact-output discrepancies remain visible and are not exact-text passes.

The [compact evidence archive](pr-evidence/task-quality-v3-qwen35-c1-evidence.tar.xz)
contains all 12 response/score/audit triplets, both comparison records and a
manifest (39 regular files, SHA256
`95780a6148d5633d59929159c30febd757640c48891ae1fe238e6827eeac448d`).
The [summary](pr-evidence/task-quality-v3-qwen35-c1-summary.json) binds every
job, spec, source, runner, archive and audit hash (SHA256
`db23971c8a167a5a4fdcb36983bcdc9765b85eedf22583ff3cc24698a4b128a1`).
The ignored local builder was run twice and produced the same archive hash;
the tracked archive lets reviewers rerun the registered scorer against every
raw output. The original v2 failure and thermal-invalid attempts remain
separate failures. This result covers **Qwen3.5 single-user task quality only**:
loaded quality, current-source fixed-output timing, absolute SLOs and the
other three CI models remain unqualified by this gate. No four-model
non-regression claim follows.
