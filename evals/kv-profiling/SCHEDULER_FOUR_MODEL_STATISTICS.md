# Completed four-round scheduler statistics

Descriptive medians of four fresh-process round metrics; not pooled request percentiles. 95% intervals enumerate all 256 paired round bootstrap samples. Four rounds provide limited uncertainty coverage and do not establish generalization. All requests in these tables completed; Coder c256 is excluded because its completion gate failed. Qwen3.5 c128 retains nine exact-output mismatches against vanilla; its cross-engine timings do not establish equivalent-work speedups.

Client in-flight counts include queued HTTP requests, not active GPU sequences. Load labels are bounded Poisson bursts with frozen baseline latency, not production capacity. SLO counts use TTFT p95 ≤30,000 ms and TPOT p95 ≤1,000 ms and are separate from completion/correctness gates.

## Qwen3.5-4B single

| Arm | Completed | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | Output tok/s | Peak client in-flight range | Mean client in-flight | Lag p95/max ms | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| skippy-base | 16 | 430.6/438.3 | 791.2/798.2 | 11.6/11.6 | 40.36 | 1–1 | 1.00 | N/A | 4/4 |
| skippy-patched | 16 | 430.6/437.4 | 790.8/798.9 | 11.6/11.6 | 40.36 | 1–1 | 1.00 | N/A | 4/4 |
| llama-slots | 16 | 450.2/459.0 | 811.5/827.3 | 11.6/11.9 | 39.22 | 1–1 | 1.00 | N/A | 4/4 |
| llama-unified | 16 | 448.9/459.6 | 815.8/826.4 | 11.8/11.9 | 39.08 | 1–1 | 1.00 | N/A | 4/4 |

Ratios below are patched/base; below 1 favors patched latency, above 1 favors patched throughput. All four pairings and per-round values are retained in the JSON.

| Metric | Median paired ratio | Paired 95% interval | Excludes parity and ≥2% effect |
|---|---:|---:|---|
| ttft_p50_ms | 0.999 | 0.999–1.003 | No |
| ttft_p95_ms | 0.998 | 0.995–1.004 | No |
| e2e_p50_ms | 1.000 | 0.998–1.006 | No |
| e2e_p95_ms | 1.001 | 0.999–1.016 | No |
| tpot_p50_ms | 0.999 | 0.999–1.006 | No |
| tpot_p95_ms | 1.001 | 0.995–1.046 | No |
| output_tok_s_over_client_span | 1.000 | 0.992–1.001 | No |

Unprofiled mixed-c4 custom-client diagnostics, four-round medians. Dispatch slip is the maximum across rounds; it measures deviation from the fixed arrival offsets.

| Arm | Late short TTFT ms | Long-2 E2E ms | Long-3 E2E ms | Anchor max token gap ms | Wave tok/s | Max dispatch slip ms |
|---|---:|---:|---:|---:|---:|---:|
| skippy-base | 7551.24 | 8098.99 | 8145.23 | 498.63 | 22.83 | 150.87 |
| skippy-patched | 1466.40 | 8295.30 | 8303.40 | 491.18 | 22.91 | 150.87 |
| llama-slots | 7704.47 | 8470.15 | 8625.05 | 517.45 | 21.42 | 150.13 |
| llama-unified | 7682.60 | 8330.38 | 8402.82 | 508.33 | 22.24 | 150.80 |

Mixed diagnostic patched/base ratios (paired 95% intervals):

- late_ttft_ms: 0.195 (0.148–0.198).
- long2_e2e_ms: 1.025 (1.001–1.032).
- long3_e2e_ms: 1.017 (1.009–1.023).
- anchor_max_gap_ms: 0.984 (0.957–0.998).
- wave_tok_s: 1.004 (1.001–1.009).
- dispatch_slip_ms: 0.947 (0.497–1.960).

## Qwen3.5-4B c64

| Arm | Completed | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | Output tok/s | Peak client in-flight range | Mean client in-flight | Lag p95/max ms | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| skippy-base | 256 | 15767.7/30326.5 | 23432.1/33012.7 | 226.0/248.1 | 60.63 | 64–64 | 42.95 | 2.32/2.61 | 0/4 |
| skippy-patched | 256 | 15901.5/30296.7 | 23507.8/32980.0 | 225.6/247.7 | 60.69 | 64–64 | 42.96 | 2.38/2.68 | 0/4 |
| llama-slots | 256 | 19019.6/35946.9 | 27362.4/38119.9 | 260.6/276.2 | 52.64 | 64–64 | 43.43 | 2.26/2.63 | 0/4 |
| llama-unified | 256 | 18875.1/35492.9 | 27063.8/37679.1 | 257.1/276.7 | 53.24 | 64–64 | 43.48 | 2.27/2.55 | 0/4 |

Ratios below are patched/base; below 1 favors patched latency, above 1 favors patched throughput. All four pairings and per-round values are retained in the JSON.

| Metric | Median paired ratio | Paired 95% interval | Excludes parity and ≥2% effect |
|---|---:|---:|---|
| ttft_p50_ms | 1.008 | 1.003–1.010 | No |
| ttft_p95_ms | 0.999 | 0.999–1.001 | No |
| e2e_p50_ms | 1.007 | 0.993–1.014 | No |
| e2e_p95_ms | 0.999 | 0.999–1.001 | No |
| tpot_p50_ms | 0.999 | 0.998–1.001 | No |
| tpot_p95_ms | 0.999 | 0.998–1.000 | No |
| output_tok_s_over_client_span | 1.001 | 1.000–1.001 | No |

## Qwen3.5-4B c128

| Arm | Completed | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | Output tok/s | Peak client in-flight range | Mean client in-flight | Lag p95/max ms | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| skippy-base | 512 | 32875.2/62341.6 | 40433.1/66457.1 | 225.9/252.0 | 60.79 | 128–128 | 75.65 | 2.26/2.67 | 0/4 |
| skippy-patched | 512 | 32797.2/62254.5 | 40422.2/66374.1 | 225.8/251.8 | 60.87 | 128–128 | 75.74 | 2.27/2.60 | 0/4 |
| llama-slots | 512 | 38666.6/73530.8 | 47279.6/77382.8 | 262.9/284.0 | 52.30 | 128–128 | 76.30 | 2.26/2.60 | 0/4 |
| llama-unified | 512 | 38239.5/72585.8 | 46713.2/76420.0 | 259.6/281.3 | 52.95 | 128–128 | 76.35 | 2.28/2.65 | 0/4 |

Ratios below are patched/base; below 1 favors patched latency, above 1 favors patched throughput. All four pairings and per-round values are retained in the JSON.

| Metric | Median paired ratio | Paired 95% interval | Excludes parity and ≥2% effect |
|---|---:|---:|---|
| ttft_p50_ms | 0.997 | 0.996–0.999 | No |
| ttft_p95_ms | 0.998 | 0.997–1.001 | No |
| e2e_p50_ms | 0.999 | 0.997–1.002 | No |
| e2e_p95_ms | 0.998 | 0.997–1.001 | No |
| tpot_p50_ms | 1.000 | 0.999–1.001 | No |
| tpot_p95_ms | 0.999 | 0.997–1.002 | No |
| output_tok_s_over_client_span | 1.002 | 0.999–1.003 | No |

## Coder-30B single

| Arm | Completed | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | Output tok/s | Peak client in-flight range | Mean client in-flight | Lag p95/max ms | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| skippy-base | 16 | 403.8/409.6 | 749.5/768.5 | 11.1/11.6 | 42.45 | 1–1 | 1.00 | N/A | 4/4 |
| skippy-patched | 16 | 401.2/408.1 | 747.1/756.2 | 11.1/11.2 | 42.72 | 1–1 | 1.00 | N/A | 4/4 |
| llama-slots | 16 | 394.2/401.9 | 738.8/747.0 | 11.1/11.2 | 43.20 | 1–1 | 1.00 | N/A | 4/4 |
| llama-unified | 16 | 396.6/403.8 | 794.3/804.7 | 12.8/13.1 | 40.21 | 1–1 | 1.00 | N/A | 4/4 |

Ratios below are patched/base; below 1 favors patched latency, above 1 favors patched throughput. All four pairings and per-round values are retained in the JSON.

| Metric | Median paired ratio | Paired 95% interval | Excludes parity and ≥2% effect |
|---|---:|---:|---|
| ttft_p50_ms | 0.996 | 0.988–0.999 | No |
| ttft_p95_ms | 0.996 | 0.995–1.000 | No |
| e2e_p50_ms | 1.001 | 0.962–1.004 | No |
| e2e_p95_ms | 0.983 | 0.935–1.009 | No |
| tpot_p50_ms | 1.007 | 0.932–1.009 | No |
| tpot_p95_ms | 0.974 | 0.857–1.008 | No |
| output_tok_s_over_client_span | 1.004 | 0.995–1.049 | No |

Unprofiled mixed-c4 custom-client diagnostics, four-round medians. Dispatch slip is the maximum across rounds; it measures deviation from the fixed arrival offsets.

| Arm | Late short TTFT ms | Long-2 E2E ms | Long-3 E2E ms | Anchor max token gap ms | Wave tok/s | Max dispatch slip ms |
|---|---:|---:|---:|---:|---:|---:|
| skippy-base | 8076.12 | 8855.87 | 8933.38 | 598.16 | 21.40 | 153.17 |
| skippy-patched | 1369.35 | 9066.97 | 8966.15 | 637.99 | 21.47 | 150.25 |
| llama-slots | 6167.24 | 9046.17 | 9164.37 | 589.68 | 20.42 | 150.80 |
| llama-unified | 7989.66 | 8827.19 | 8943.60 | 600.91 | 21.07 | 150.70 |

Mixed diagnostic patched/base ratios (paired 95% intervals):

- late_ttft_ms: 0.178 (0.139–0.308).
- long2_e2e_ms: 1.024 (1.022–1.027).
- long3_e2e_ms: 1.000 (0.996–1.013).
- anchor_max_gap_ms: 1.065 (1.035–1.075).
- wave_tok_s: 1.002 (0.999–1.005).
- dispatch_slip_ms: 1.023 (0.366–1.497).

## Coder-30B c64

| Arm | Completed | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | Output tok/s | Peak client in-flight range | Mean client in-flight | Lag p95/max ms | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| skippy-base | 256 | 15993.5/30840.7 | 23926.0/34010.4 | 235.4/250.7 | 58.96 | 64–64 | 42.77 | 2.21/2.68 | 0/4 |
| skippy-patched | 256 | 16025.9/30815.5 | 23939.3/33990.7 | 235.2/250.3 | 58.99 | 64–64 | 42.75 | 2.33/2.95 | 0/4 |
| llama-slots | 256 | 18517.7/36044.4 | 27837.2/39224.1 | 287.9/298.2 | 51.24 | 64–64 | 43.17 | 2.31/2.64 | 0/4 |
| llama-unified | 256 | 16028.8/30917.4 | 23994.7/34010.0 | 249.6/256.5 | 58.91 | 64–64 | 43.03 | 2.22/2.60 | 0/4 |

Ratios below are patched/base; below 1 favors patched latency, above 1 favors patched throughput. All four pairings and per-round values are retained in the JSON.

| Metric | Median paired ratio | Paired 95% interval | Excludes parity and ≥2% effect |
|---|---:|---:|---|
| ttft_p50_ms | 1.002 | 0.992–1.007 | No |
| ttft_p95_ms | 0.999 | 0.998–0.999 | No |
| e2e_p50_ms | 1.002 | 0.999–1.008 | No |
| e2e_p95_ms | 0.999 | 0.998–1.000 | No |
| tpot_p50_ms | 1.000 | 0.999–1.001 | No |
| tpot_p95_ms | 0.998 | 0.997–1.000 | No |
| output_tok_s_over_client_span | 1.001 | 1.000–1.002 | No |

## Coder-30B c128

| Arm | Completed | TTFT p50/p95 ms | E2E p50/p95 ms | TPOT p50/p95 ms | Output tok/s | Peak client in-flight range | Mean client in-flight | Lag p95/max ms | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| skippy-base | 512 | 33485.0/63825.5 | 41420.8/68408.6 | 235.4/257.6 | 59.11 | 128–128 | 75.40 | 2.23/2.61 | 0/4 |
| skippy-patched | 512 | 33440.4/63771.3 | 41511.5/68360.8 | 235.4/257.4 | 59.16 | 128–128 | 75.51 | 2.37/3.42 | 0/4 |
| llama-slots | 512 | 38841.8/74622.8 | 48203.4/79426.7 | 289.5/302.0 | 50.99 | 128–128 | 75.94 | 2.32/2.77 | 0/4 |
| llama-unified | 512 | 33539.2/64217.0 | 41540.0/68585.8 | 251.0/257.7 | 58.92 | 128–128 | 75.83 | 2.27/2.65 | 0/4 |

Ratios below are patched/base; below 1 favors patched latency, above 1 favors patched throughput. All four pairings and per-round values are retained in the JSON.

| Metric | Median paired ratio | Paired 95% interval | Excludes parity and ≥2% effect |
|---|---:|---:|---|
| ttft_p50_ms | 0.999 | 0.999–1.000 | No |
| ttft_p95_ms | 0.999 | 0.998–1.000 | No |
| e2e_p50_ms | 1.002 | 0.999–1.006 | No |
| e2e_p95_ms | 0.999 | 0.998–1.000 | No |
| tpot_p50_ms | 1.000 | 0.999–1.001 | No |
| tpot_p95_ms | 0.999 | 0.999–1.001 | No |
| output_tok_s_over_client_span | 1.001 | 1.000–1.002 | No |

Unprofiled mixed-screen diagnostics (including every long-request latency and anchor gap) remain in each JSON cell's `diagnostic_waves`; they use a separate custom client, not inferencekit.
