# Coder explicit queue256 retest

Pinned Q4_K_M Coder on M1 Ultra, full Metal, 16 active lanes, context 8192, F16 KV, caches off. Four balanced fresh-process rounds, 256 requests each, frozen reference 749.4787085ms. Queue 256 is explicit in both Skippy arms; the runtime default is unchanged.

Completed 4096/4096; cross-arm text mismatches 0; cross-round mismatches 0. Request bodies and identities match the original failed c256 experiment.

| Arm | Output tok/s | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | SLO rounds |
|---|---:|---:|---:|---:|---:|
| skippy-base | 59.22 | 67940.6/129852.9 | 75874.4/137129.5 | 235.5/261.0 | 0/4 |
| skippy-patched | 59.20 | 67958.2/129914.2 | 75897.3/137180.2 | 235.5/261.1 | 0/4 |
| llama-slots | 50.76 | 79445.9/152145.5 | 88835.2/160159.3 | 290.7/304.5 | 0/4 |
| llama-unified | 58.77 | 68533.4/131276.9 | 76575.5/138099.6 | 252.3/259.2 | 0/4 |

Values are medians of round metrics, not pooled percentiles. TPS is aggregate output tokens over client span. SLO requires p95 TTFT ≤30 s and p95 TPOT ≤1 s. This bounded burst is not a production-capacity claim.

Saved JY thermal alerts do not overlap timed client windows (including 1 s boundary uncertainty). Native settings/full offload were verified in separate diagnostic preflights, with matching timed launch arguments; timed Skippy logs omit native settings. Native boundaries were nominal and the 0.5 s thermal/contention guard completed. Sampling cannot exclude every transient.

Patched/base paired ratios (95% empirical intervals):

- ttft_p50_ms: 1.0002 (0.9993–1.0005); excludes parity and ≥2% effect: False.
- ttft_p95_ms: 1.0005 (1.0000–1.0007); excludes parity and ≥2% effect: False.
- e2e_p50_ms: 1.0000 (0.9975–1.0006); excludes parity and ≥2% effect: False.
- e2e_p95_ms: 1.0004 (1.0002–1.0009); excludes parity and ≥2% effect: False.
- tpot_p50_ms: 1.0003 (0.9998–1.0004); excludes parity and ≥2% effect: False.
- tpot_p95_ms: 1.0003 (0.9999–1.0056); excludes parity and ≥2% effect: False.
- output_tok_s_over_client_span: 0.9997 (0.9991–1.0000); excludes parity and ≥2% effect: False.

Exact text agreement is a correctness screen, not full quality qualification. Original queue 128 failures remain preserved. No engine fix or widened default is claimed.
