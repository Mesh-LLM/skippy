# Metal receipt rounding: draft qualification

This is a **kernel calibration**, not end-to-end receipt qualification.
Metal admission remains closed in `BackendFamily::of`. Neither candidate is
selected for CPU or CUDA. Do not enable Metal based on this fixture alone.

## Result

On Apple M5 Max / macOS 27.2.1, Q8_0 matrix products from the prepared
llama.cpp pin `8954f194837d2ec4d5f67f20c81b84abe63883ba`, with the patch queue
from Skippy #180 (`5b554478292a319549ce1a5822eee598d8fcfa84`), fit:

- Decode: ordinary dequantized weights and f32 activations (`Raw`).
- Legacy prefill: both weights and activations rounded to f16 (`F16Weights`).
- Tensor-API prefill: weights rounded to f16, activations with their low 13
  fraction bits discarded, preserving the f32 exponent (`MetalTensor`).
  This is an observed arithmetic model, not an Apple API guarantee or a
  claim about every device or input range. In particular it is **not** the
  usual round-to-nearest f16 conversion.

Captured 2026-11-25, K=256, M=16, N=17. Relative error uses the production
matrix check and its activation-derived floor; no near-tie repair was used:

| Prefill path | Maximum error against Raw | Maximum error against candidate |
|---|---:|---:|
| Tensor API | 4.23438e-4 | 7.56634e-8 |
| Legacy | 4.23677e-4 | 9.27801e-8 |

The offline regression requires error below 2e-6 (well below the unchanged
production tolerance of 1e-3), checks decode separately, and rejects outputs
scaled by 5%. This negative control is **not** a wrong-model serving audit.
The legacy mode was tested on the same M5; this does not qualify older Macs.
No performance or receipt-recording overhead claim is made.

## Reproduce

All native inputs come from the repository's prepared source; this tool
adds no dependencies and downloads no models. Metal absence is a failure,
not a passing skip. An older device without the tensor API may fail the
tensor-profile comparison; that is not qualification for that device.

```sh
just receipts-metal-build
captures=$(mktemp -d)
env -u GGML_METAL_TENSOR_DISABLE target/receipts-metal/receipts-metal-capture \
  > "$captures/tensor.f32le" 2> "$captures/tensor.log"
GGML_METAL_TENSOR_DISABLE=1 target/receipts-metal/receipts-metal-capture \
  > "$captures/legacy.f32le" 2> "$captures/legacy.log"
SKIPPY_METAL_CAPTURE_DIR="$captures" just with-lld cargo test --locked -p skippy-receipts -- --nocapture
```

Check every command's exit status before treating the next as evidence.
Keep the logs: they identify the device and compiled kernels. Default
`cargo test -p skippy-receipts` uses the checked-in captures, requires no
Metal device, and must not be described as a new hardware run. Setting
`SKIPPY_METAL_CAPTURE_DIR` requires both fresh files; missing or truncated
captures fail instead of falling back to saved fixtures.

### Fixture format and provenance

The files under `crates/skippy-receipts/tests/fixtures/metal/` are generated
synthetic data from `capture.cpp` under the repository's Apache-2.0 license,
not model weights or user prompts. Each is 34,944 bytes, little-endian f32:

1. 16 rows of 256 Q8_0 weights, dequantized by ggml to f32 (4,096 floats).
2. 17 input columns of 256 values (4,352 floats).
3. Metal output for the first input as decode (16 floats).
4. Metal output for all inputs as prefill (272 floats).

SHA-256:

- `tensor.f32le`: `243884fd23df294a296f763ed6483b8459044e364cfce84fc5a9cafaa639249b`
- `legacy.f32le`: `86cfd49af7e76a0ad555a8c9ccdd752e1fb6704b897ac1fe146a98760a75c9e1`

The test reads these outputs; it never generates its expected output with
the candidate Rust rounding implementation.

## Gates before enabling Metal

1. Apply weight rounding consistently to output-head guide rows, full-row
   checks, and precomputed head-key projections. Rounding `rᵀW` afterwards
   is not equivalent to computing `rᵀround(W)`; retained key compatibility
   needs an explicit decision and regression tests.
2. Run real serving -> signed receipt -> opening -> audit -> offline
   recheck with Q8_0: short and long prefill, decode, sampling, cached
   prefixes, multiple lanes and stage splits. Cover both Metal kernel
   paths. The earlier design measurements explicitly found a harness/live
   discrepancy, so a synthetic kernel pass is insufficient.
3. Reject a served lower quantization claiming Q8_0 in both live audits
   and offline rechecks. Keep existing tolerances and fault attribution.
4. Measure receipts on/off on the same release build and model, including
   recording overhead, opening latency and memory. Do not silently disable
   the tensor API to pass: that has a large prefill cost.
5. Restrict admission to the qualified weight/kernel/device combinations;
   keep partial offload, mixed backends and unsupported quantizations
   fail-closed. Q4_K has a separate dequantization mismatch and is not
   covered by these profiles. Larger shapes, extreme magnitudes, MoE and
   other Apple generations remain unqualified.

No ABI, wire, hash-domain, runtime precision policy, dependency, or backend
admission changes are made by this draft.
