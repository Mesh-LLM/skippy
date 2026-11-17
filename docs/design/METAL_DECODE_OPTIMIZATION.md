# Metal decode optimization work

Status: draft port, 2026-11-17. The standalone M3 Ultra result is confirmed across three process campaigns; the matched vanilla llama.cpp comparison is running. See [benchmark evidence](METAL_DECODE_BENCHMARKS.md).

## Baseline migration

The standalone extraction starts at `fbc3349cd406786e43f6579590c9a12b0cd49c05`
and pins llama.cpp `8954f194837d2ec4d5f67f20c81b84abe63883ba`.
It predates the shared draft/verification scheduler and native decode work
qualified in the Mesh-hosted Skippy tree. This branch ports those changes while
preserving the extraction's newer upstream changes and standalone ownership.

The port includes shared speculative forwards, concurrent sampling, resident
prefix handling, KV allocation and row attention improvements, gated activation
fusion, routed vector reuse, decode convolution changes, and tracing. The affine
conversion preserves integer codes and rounds affine coefficients to f16; that
conversion utility is not used in these unchanged-GGUF serving measurements.
Model tuning is outside scope.

Compatibility resolutions retain upstream per-layer KV strides, the standalone
context-capacity implementation, prompt shape statistics, and current sampling
state. Native API bindings are regenerated from the prepared source at ABI
0.1.85 rather than copied from the older upstream pin.

## Evidence and remaining gates

Local evidence is under `target/migration-audit/`; those artifacts are ignored
and are not a published benchmark report.

- All 141 ordered native patches apply directly, with every intermediate tree
  matching the qualified native history. Two imported patches were regenerated
  against their actual parents after a fresh M3 checkout exposed hidden
  three-way Git-object dependencies. The resulting native source is unchanged.
- Rust all-target warning-denying Clippy passed again after the final
  documentation and test-assertion edits.
- Native routed policy and numerical checks passed, including the R4/R8 paths.
- The focused native CTest run passed all 51 fixture-backed tests, with no skips.
- The full-model control/candidate probe produced identical exported state in
  four comparisons covering rollback and shared recurrent-lane repair.
- The wider native family graph-contract run passed all 198 tests with no
  skips. The serial Rust
  workspace run excluding `skippy-api` passed, including integration and
  documentation tests. The API run passed 131 tests with its two unresolved
  split-certification tests explicitly filtered out; that is not a full pass.
- The split roster remains bound to its prior native recipe. Do not relabel
  those certificates as newly qualified to make tests pass.
- Regenerated API documentation and the binding freshness/mutation self-check
  passed. The standalone release CLI builds and reads the packaged native
  runtime successfully.
- The C++ facade CTest run completed: 248 passed and one skipped because this
  standard library lacks `std::start_lifetime_as_array`. The skip is not native
  hardware qualification.
- The separate Rust facade workspace passed formatting, all-target
  warning-denying Clippy, unit/integration tests, and documentation tests. Its
  optional real embedding/reranking fixtures are not decode qualification.
- Two unchanged process-supervision projection tests failed in the parallel
  workspace run and passed both in the focused serial rerun and the serial
  workspace run. Do not report an unconditional parallel workspace pass.
- A pristine standalone baseline checkout and its runtime/release CLI have been
  built separately. Matched standalone serving benchmarks remain required before
  promotion.
- An untimed serving smoke loaded the original 35B GGUF, returned the expected
  one-word response, and completed two concurrent 64-token requests with prompt
  caching disabled. The owned server then shut down cleanly. Other validation
  was running, so request elapsed times are not benchmark evidence.

Historical Mesh-hosted engine-only results were +8.5% at concurrency one and
+45.3% aggregate throughput at concurrency two against Jared's PR baseline.
They are workload-specific historical results, not measurements of this
standalone port or a claim of parity with another engine.

The separate M1 parallel-encoding experiment failed its predeclared performance
gate: plain decode improved, but DFlash concurrency-two pooled throughput was
-0.097%, with mixed cycles. That prototype is not retained in this port.

## Subsequent experiments

After qualifying this baseline, measure independently implemented cached Metal
encoding plans, fewer host round trips across draft/verify/accept/commit, packed
4-bit TensorOps using unchanged model values, and shared-plus-routed expert
fusion. Static analysis and public research identify candidate mechanisms;
neither establishes their isolated speedup. Keep each experiment independently
switchable and compare matched model bytes, hardware, prompt/context, output
length, concurrency, and acceptance behavior.

### Cached encoding plan constraints

The current Metal encoder selects pipelines, binds buffers and inline arguments,
sets threadgroup memory, inserts barriers, and dispatches each graph on the CPU.
A replay experiment should record that command stream and replay ordinary Metal
encoder calls; changing command-buffer ordering is a separate experiment.

A tensor-shape-only key is insufficient. The key must cover operation parameters,
strides, source topology and aliasing, output flags, buffer usage and actual Metal
buffer identities/offsets, fusion configuration, and command-buffer boundaries.
Recorded buffers and pipeline objects need owned lifetimes with bounded eviction.
Debug capture and fusion statistics should use normal encoding.

Row-list attention currently examines host-visible row-list values to choose the
gather-free pipeline. Matching allocation and tensor geometry does not guarantee
that choice remains valid. Either recompute and key the classification on replay,
or invalidate the plan when the relevant values change. Never cache a dense-row
decision across a general-row layout merely because the list has the same shape.

Qualification must exercise buffer replacement, changed strides and operation
parameters, aliasing, row-layout transitions, graph reuse, and speculative
accept/reject/rollback. Keep plan creation and cache hits observable separately
from warmed pipeline compilation and GPU execution time.

### Isolated dispatch-plan prototype

The default-off prototype records ordinary Metal encoder operations and keys
replay on exact graph, tensor, allocation, fusion, partition, and row-density
state. Metal resources remain owned through command-buffer completion. Storage
is bounded; capture/debug paths use normal encoding. It is not promoted into
the retained core queue.

The final prototype native tree is
`403bb3bb5d30ddd0eafc2508ace76298b5f95cba`. Its unit checks cover 49,152 exact
values under input/metadata/allocation changes and 3,072 exact values across
row-list classifications. The packaged full-model check recorded 2,452 hits
and 266 misses, with all four exported state comparisons byte exact, including
rollback and delayed peer repair. These establish correctness coverage, not
TPS improvement.

Local timing stopped during warmup because other applications were active; no
measured batches completed. The authorized M3 Ultra campaign builds the
standalone baseline, retained port, and opt-in prototype separately. It must
verify the original model and draft hashes, perform same-device ordinary versus
replay state checks, then compare both retained versus baseline and prototype
versus retained in separate plain/DFlash campaigns at concurrency one and two.
Within-prompt ABBA, fixed output budgets, raw responses, load/thermal exclusions,
loaded-library identity, and an independent artifact audit are required before
quoting a new result. The retained port now has a standalone pilot result; the cache prototype still has no measured TPS result.

An isolated follow-up, native commit
`c464911fbf612b58edbc6c8bddb379515834934b`, deduplicates live tensor metadata
within each cache signature while preserving every graph/source identity and
row classification. The same unit/invalidation and model-state checks pass:
49,152 ordinary-op values, 3,072 row-layout values, four exact state exports,
and unchanged 2,452 hits/266 misses. On that local probe, summed final signature
storage falls from 50,279,312 to 27,285,872 bytes (45.7%). This is a data-volume
observation, not a CPU-time or TPS result; it is separate from the M3 pilot.
