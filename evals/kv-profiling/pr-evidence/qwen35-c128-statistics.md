# Qwen3.5 fixed Metal c128 retest

Four fresh-process rounds, original heterogeneous 128-request arrival workload. Medians are across rounds, not pooled requests. SLO: TTFT p95 ≤30 s and TPOT p95 ≤1 s.

| Arm | Complete | TTFT p95 ms | E2E p95 ms | TPOT p95 ms | Output tok/s | SLO rounds |
|---|---:|---:|---:|---:|---:|---:|
| skippy-base | 512 | 62297.7 | 66416.5 | 252.0 | 60.84 | 0/4 |
| skippy-patched | 512 | 62241.2 | 66364.0 | 251.7 | 60.88 | 0/4 |
| llama-slots | 512 | 73537.9 | 77387.0 | 283.8 | 52.29 | 0/4 |
| llama-unified | 512 | 72571.6 | 76397.3 | 281.3 | 52.97 | 0/4 |

Paired four-round ratios use 256 empirical bootstrap samples. Latency ratios below 1 and throughput ratios above 1 favor the comparison arm.

| Comparison / baseline | TTFT p95 ratio (95% interval) | E2E p95 ratio (95% interval) | Output tok/s ratio (95% interval) |
|---|---:|---:|---:|
| skippy-patched / skippy-base | 0.999 (0.998–1.001) | 0.999 (0.998–1.002) | 1.001 (0.999–1.002) |
| llama-unified / llama-slots | 0.987 (0.985–0.988) | 0.987 (0.985–0.988) | 1.013 (1.011–1.015) |
| skippy-patched / llama-slots | 0.847 (0.846–0.847) | 0.858 (0.857–0.858) | 1.164 (1.164–1.165) |
| skippy-patched / llama-unified | 0.858 (0.857–0.860) | 0.869 (0.868–0.871) | 1.149 (1.147–1.150) |

Cross-arm exact-output mismatches: 12 comparisons across load-0100, load-0190; all four rounds.
Cross-round output drift: 0 prompts. Changes from the original failed c128 run: skippy-base 8, skippy-patched 8, llama-slots 1, llama-unified 0.
Every arm misses the registered 30-second TTFT p95 SLO, although TPOT p95 is below 1 second. The cross-engine output differences and SLO failures block an overall winner claim. Full per-round values, output hashes, and paired intervals are in audit.json and statistics.json.
