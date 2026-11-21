# Cache correctness gate

## GPU cold reference and cross-tier replay

Run the explicit qualification case on arm64 macOS with the pinned
[Qwen3-4B Q4_K_M GGUF](https://huggingface.co/unsloth/Qwen3-4B-GGUF/blob/22c9fc8a8c7700b76a1789366280a6a5a1ad1120/Qwen3-4B-Q4_K_M.gguf).
The [fixture manifest](../crates/skippy-model-artifact/src/kv_fixtures/models.json)
pins the GGUF and embedded chat template for each KV architecture case. Set `SKIPPY_CACHE_QUAL_FAMILY`
to `dense-attention` (the default), `hybrid-dense`, `attention-moe`, or
`hybrid-moe`, and supply the matching GGUF. Use `llama-server` built
from the exact upstream commit in `llama_cpp/upstream.txt` and a Skippy native
build from the repository's pinned recipe. Build both executables for the
same backend.

```sh
LLAMA_STAGE_BUILD_DIR=/absolute/skippy-metal-build \
SKIPPY_CACHE_QUAL_MODEL=/absolute/Qwen3-4B-Q4_K_M.gguf \
SKIPPY_CACHE_QUAL_LLAMA_SERVER=/absolute/pinned-llama-server \
SKIPPY_CACHE_QUAL_REPORT_DIR=/absolute/fresh-report-directory \
just cache-correctness-metal
```

On x86_64 Linux with CUDA, point the same variables at the CUDA builds and use
`just cache-correctness-cuda`.

The test renders the same chat in both engines and compares the rendered prompt
hash, exact prompt and generated token IDs, and output text. It compares the
top five alternatives by token ID at each of eight decode checkpoints
and allows an absolute difference of 0.05 for normalized log probabilities.
The reference server and Skippy expose these scores after normalization.
The cold reference disables context checkpoint creation as well as prompt
reuse so checkpoint boundaries cannot change its prefill batches.

The cache-disabled Skippy result qualifies the cold baseline. An attention
model must hit its native resident prefix in L1. A hybrid model must restore
both KV and recurrent state at the rendered chat checkpoint and prefill the
suffix. Every replay checks the reported token accounting against the
selected prefix.

The L1 test also replays two successive conversation extensions and a changed
tail after a common prefix. Each request runs cold in llama.cpp and Skippy, then
warm through Skippy. The validator derives the expected resident prefix from
the actual tokens. A recurrent checkpoint is reusable only when the full
checkpoint remains a prefix of the rendered prompt. Each conversation case
reports its own outcome and token accounting.

The suite also exercises schema-constrained JSON, a forced named tool call,
and retrieval from the beginning of a prompt of at least 4,096 tokens. Both
engines use an 8,192-token context. The structured cases compare completed
objects or tool names and arguments against a known answer. Each case uses
its own trusted Skippy scope and reports cold, seed, and warm cache usage.

The cross-tier case forces eviction of native and serialized L1 entries. Two
L3 restores promote the checkpoint into host RAM. The next replay must hit L2
without reading a disk payload. The publisher exits cleanly, and a new process
reopens the disk cache with empty in-memory tiers and must hit L3. While that
process owns the store, a contender must be denied access, generate the cold
result using in-memory caching, and leave the committed pack intact. The test
kills the owner and requires a fresh process to discard an interrupted write
and restore the committed checkpoint. Every restore must match the cold
output and account for the remaining suffix prefill. A request from a
different trusted execution scope must miss, and truncating the disk object
must produce a detected corruption and a successful cold recompute.

Both engines must show layer offload on the selected GPU backend. The fresh
directory retains `result.json` with the outcome, fixture and executable
identities, and token evidence alongside the native logs. The report records
each output mismatch independently, including the cold comparison against
upstream llama.cpp and the cache replays against cold Skippy. Missing
prerequisites or telemetry fail qualification. Ordinary Rust tests run the
evidence validator offline.

Each GPU cell also primes L1, L2, and L3 with an explicit chat
template, then adds a Jinja comment that renders identical prompt bytes.
The revised template must cold-prefill every token, preserve the output
and leave the disk-payload read counter unchanged. The cell JSON retains
the observed state for offline verification.

Recheck a retained report without loading a model, starting a server, or
requiring a GPU. The verifier checks the fixture, pinned reference version,
qualification settings, and recorded binary hashes. It recomputes the result
from the captured outputs and cache observations.

```sh
just cache-correctness-verify metal /absolute/report-directory/result.json
```

### Run all four KV architecture cases

```sh
SKIPPY_CACHE_QUAL_FIXTURE_DIR=/absolute/read-only-model-directory \
LLAMA_STAGE_BUILD_DIR=/absolute/skippy-metal-build \
SKIPPY_CACHE_QUAL_LLAMA_SERVER=/absolute/pinned-llama-server \
just cache-correctness-matrix metal /absolute/fresh-report-directory
```

Use `cuda-arm64` for DGX Spark and `cuda` for x86-64 Linux CUDA. The matrix uses each exact
GGUF filename from the manifest and writes a `batch-result.json` with four
independent outcomes and SHA-256 digests of the retained cell reports. Missing
inputs produce explicit failures. A model failure leaves its diagnostics in
place and the other models still run.
Use `--build` to build omitted inputs with Just. The reference build extracts
the exact upstream Git tree from the prepared llama.cpp checkout.

To verify a retained matrix, pass the source SHA selected for admission:

```sh
just cache-correctness-verify-matrix metal "$source_sha" /absolute/metal-report-directory
just cache-correctness-verify-matrix cuda "$source_sha" /absolute/cuda-report-directory
```

Each verification requires a matrix recorded from a clean checkout of that
source. It verifies the fixture manifest, then checks each report digest and
recomputes its decision. Dirty worktree results can still be used for
diagnostics.

For KV-affecting PRs and pushes to `main`, CI runs one dense fixture inside
the Metal and Linux x64 CUDA 13 package jobs after device smoke. It compares
cold, warm L1, and L3 restart outputs and checks that the selected cache tier
actually restored the expected tokens. Full CI, including nightly, release and
llama upgrade validation, runs all four cases on Metal and Linux ARM64 CUDA 13
on Spark. Each backend runs its models in sequence with separate cache roots
and reports. Full runs build the pinned upstream reference only on a cache miss.

The Rust test harness loads the verified runtime from the same source-bound
package used for device smoke. It uses `hf download` at each pinned revision
with the runner's Hugging Face cache on the NAS. CI retains the runtime
manifest digest, fixture reports, and logs. A failed model, missing fixture,
absent GPU, or incomplete required job fails CI.

## Historical state handoff batch

Earlier checkouts that include the state-handoff operator prepare local observations with:

```sh
cargo xtool automation cache-family-run prepare-full --input /absolute/operator.json --output /absolute/fresh-preparation
cargo xtool automation cache-family-correctness batch --input /absolute/gate.json --output /absolute/fresh-gate
```

The operator request selects local catalog models, correctness/stage tools, a native build directory, source revisions, and explicit environment/toolkit settings. Preparation hashes actual bytes; supplied source revisions do not establish a build or loaded runtime attestation. Present batch models require their observed correctness profile. Missing models produce unqualified missing-model rows, never synthetic passes.

A gate request is:

```json
{"schema_version":1,"prepared_input":"/absolute/fresh-preparation/cache-family-input.json","cases":[],"prefix_tokens":null,"n_gpu_layers":null}
```

An empty case selection uses the intersection of the historical default roster and the current source-owned catalog: qwen3_dense, llama, glm4, gemma3, falcon_h1, olmo, qwen3next. Prepare those selected profiles before running the batch. Explicit cases are closed current catalog keys. The default topologies are one-stage, split-middle, and split-final; explicit topologies also admit split-stage0. Package-stage1 belongs to the existing package correctness frontdoor and is not one of this historical batch's topologies.

Optional fields are topologies, prefix_tokens, cache_hit_repeats (3), runtime_lane_count (4), n_gpu_layers, and execution_seconds (900, bounded to 4–86400). Without a prefix override, each catalog prefix is capped at 32. Resident hit borrowing is enabled; decoded-result hit reuse is disabled. The historical Python suffix option defaulted to 3 but never reached its child; this adapter preserves the native one-token suffix and reports actual native fields where present. Missing native sequence/storage measurements remain null.

The batch retains cache-correctness-gate.json raw observations and existing admission/process/trial evidence, cache-correctness-table.json rows, and cache-correctness-table.md. Rows preserve family/model/payload/topology, pass/missing/refusal/failure, remapping and sequence IDs, prompt/suffix counts, payload/recurrent/KV/storage bytes, repeats/matches, and pass versus disabled-or-recompute promotion. Suffix and Hits Markdown columns retain the native match booleans; counts remain in JSON. Final table/Markdown/summary writes are checked before and after each owned publication; late refusal rewrites noncomplete diagnostics through retained owned file descriptors. Failed or terminally refused batches exit nonzero and retain observations; all-missing batches may complete orchestration without qualifying any model. batch-summary.json records this scope and prepared settings.

The gate measures correctness only. It does not replace the full cache matrix or establish serving throughput, package baseline, family promotion, hardware cadence, or real-model qualification. Output directories must be fresh. Prepared paths and byte pins remain subject to the existing producer's before/after custody admission. One inherited absolute batch deadline and cancellation owner supervise every child, with each child's existing phase cap and cleanup reserve.
