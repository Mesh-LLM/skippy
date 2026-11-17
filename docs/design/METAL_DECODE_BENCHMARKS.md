# Native decode benchmark evidence

Status: draft, 2026-11-17. The first standalone comparison is complete;
confirmation campaigns and the matched vanilla llama.cpp baseline are pending.

## Matched comparison

The table must compare vanilla llama.cpp, Skippy main, and the candidate PR.
Vanilla uses the same llama.cpp upstream pin, not the Skippy native patch queue.
An empty hardware/model cell is unmeasured, never an inferred number.

Hardware: one 256 GB Apple M3 Ultra, macOS 26.7.1. Skippy main is
`fbc3349cd406786e43f6579590c9a12b0cd49c05`. The measured candidate snapshot is
`c2055c4a3bb20f601f0074e6fe06a7c85efef01d`; its product source matches this
PR, with subsequent evidence/documentation edits only. Both use llama.cpp
`8954f194837d2ec4d5f67f20c81b84abe63883ba`. The candidate includes the earlier
Mesh-hosted/Jared native work and subsequent engine changes, ported into the
standalone repository. These numbers do not isolate each contributor's changes.

| Serving mode | Vanilla llama.cpp | Skippy main TPS | Candidate PR TPS | Candidate vs main |
|---|---:|---:|---:|---:|
| Plain, concurrency 1 | Pending | 35.85 | 99.35 | +177.09% |
| Plain, concurrency 2, aggregate | Pending | 62.58 | 162.19 | +159.18% |
| DFlash, concurrency 1 | Not tested | 163.73 | 165.52 | +1.10% |
| DFlash, concurrency 2, aggregate | Not tested | 155.71 | 206.06 | +32.34% |

These are serving throughput measurements: fixed output tokens divided by
pooled HTTP batch elapsed time, including prefill. Each concurrency is pooled
independently. They are one fresh-process campaign with three measured cycles,
not a cross-hardware claim or a comparison with BaseRT/Splash.

The target is `unsloth/Qwen3.6-35B-A3B-GGUF`, revision
`a483e9e6cbd595906af30beda3187c2663a1118c`, file
`Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` (22,360,456,160 bytes), SHA-256
`707a55a8a4397ecde44de0c499d3e68c1ad1d240d1da65826b4949d1043f4450`.
DFlash additionally uses `ethanwtodd/Qwen3.6-35B-A3B-DFlash2-Q8_0-GGUF`,
revision `65aaab9513e9c6608ada7c7d16e6532c198a2296`, file
`Qwen3.6-35B-A3B-DFlash2-Q8_0.gguf` (570,468,544 bytes), SHA-256
`986b8f24ac3469ed122946fb5be5b1fbe186467f2573f84e48bdbf7db278b327`.
There is no model tuning or quantization change between arms.

## Method and custody

- Eight fixed completion prompts, temperature zero, top-k one, 128 output
  tokens per request, concurrency one and two, three warmup and three measured
  cycles, and within-prompt old/new/new/old measured order.
- Context 8192, batch 2048, microbatch 512, four CPU threads and four batch
  threads, f16 K/V, generation concurrency two, native MTP and prompt/disk
  caches disabled; DFlash proposal cap seven when enabled.
- Both arms use packaged release CLIs and native libraries from their pinned
  source trees. All 21 loaded ggml/llama library paths were checked against
  each arm's own bundle. Model/draft and package hashes were verified.
- Whole ABBA blocks with competing load or non-nominal thermals are discarded
  and retried; 100 ms observations cover requests. Plain discarded 43 blocks,
  DFlash 23. Each completed mode accepted 288 requests including warmup.
- All accepted response text hashes match across arms and cycles; every
  response has 128 output tokens and zero prompt-cache hits. Independent
  pooling and artifact audits passed remotely and again on downloaded raw
  evidence locally.
- No build/test work runs on the benchmark host during timed windows.

Jianyang build job: `job_dae3gdjd1gvr22d`, run `run_bjf8g9z1gvr6fzy`.
Measurement job: `job_n64z1nezwe0wp85`, run `run_06cmr5v5yx35a14`.
The measurement job was intentionally cancelled after both retained-v-main
comparisons and audits completed, to publish those artifacts separately from
its unfinished cache experiment. Cancellation does not supply a successful
whole-job or cache-performance result.

The run retains `retained-{plain,dflash}-0-{summary,independent-audit,provenance}.json`
and `retained-{plain,dflash}-0-evidence.tar.gz`, including requests, raw
responses, accepted/rejected batches, thermal/process observations, commands,
and loaded-library paths. Local copies are under the ignored
`target/m3-decode-campaign/measure-artifacts-1/` directory.

## Remaining work

Two further fresh-process campaigns per mode are running in
`job_a3z1zb6g1w0p9vx`; complete and audit them before treating the pilot as
established. A same-runtime rollback-depth ablation is running on the separately
authorized M1 Ultra (`job_r1a6e5jzn1kvhd9`). Its hardware differs, so its numbers
must not be pooled into the M3 table.

The large plain gain is consistent with restoring explicit zero-depth recurrent
rollback: the old native path forced at least two snapshots even for ordinary
decode. Attribution still requires the ablation; this explanation is not itself
causal measurement.

Two API tests remain unqualified because the bundled split-certification roster
still targets ABI 0.1.80 and its old recipe. The candidate is ABI 0.1.85. Do not
relabel the roster or claim full API/family certification from these serving
measurements. Broader rollout qualification remains outstanding.

Cached Metal dispatch prototypes, including serialized/deduplicated signatures
and in-place snapshots, remain outside this PR. Their correctness/profile
results do not contribute to any TPS number above.
