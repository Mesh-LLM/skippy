---
pretty_name: "Skippy inference health"
tags:
- benchmark
- tabular
---

# Skippy inference health

**Preview.** Values, dates and commit IDs on this card are illustrative.

**2 regressions · 0 improvements · 1 within tolerance · 0 first measurements**

24 Nov 2026 14:00 UTC · Engine `cccccccccccc` · nightly · Qualification: Failed · **Tolerance: ±1%** · NVIDIA GB10 DGX Spark

2 measured configurations. Counts refer to performance metrics. First measurements establish new series and have no trend verdict.

## What regressed

| Configuration | Metric | Latest | Reference | Change |
| --- | --- | --- | --- | --- |
| Qwen3.5-4B (1 Spark) | Output throughput | 26.71 tok/s | 30.42 tok/s | -12.20% |
| Qwen3.5-4B (1 Spark) | Time to first token | 14.53 seconds | 14.34 seconds | +1.37% |

## Performance over time

Each panel shows one measured model and Spark configuration. The dashed line is the current comparison reference; the shaded band is ±1%. Blue circles are within tolerance, green triangles indicate improvements, red triangles indicate regressions, and grey squares mark first measurements. Missing measurements leave gaps. Dates are UTC.

### Output throughput

Higher is better.

![Output throughput over time for every measured configuration](charts/qualification-1.png)

### Time to first token

Lower is better.

![Time to first token over time for every measured configuration](charts/qualification-2.png)

### Decode throughput

Higher is better.

![Decode throughput over time for every measured configuration](charts/qualification-3.png)

## Latest numbers

Current values and signed changes against the saved reference, or the previous comparable measurement when no saved reference exists. Higher throughput and lower latency are better.

### One Spark

| Model | Output throughput (tok/s) | Time to first token (seconds) | Decode throughput (tok/s) |
| --- | --- | --- | --- |
| Qwen3.5-4B (1 Spark) | 26.71 (-12.20% · Regressed) | 14.53 (+1.37% · Regressed) | — |
| Qwen3.5-4B (1 Spark) | — | — | 62.95 (-0.58% · Within tolerance) |


## Measurement notes

Two trials per configuration. Throughput and latency values are means of trial summaries. Latency is the mean of trial p95 values, not a pooled p95.
The ±1% rule expresses tolerance, not statistical significance. Comparisons preserve workload identity; a changed benchmark starts a new series.

[Full results and history](https://huggingface.co/datasets/meshllm/skippy-inference-qualification/tree/main/runs)
