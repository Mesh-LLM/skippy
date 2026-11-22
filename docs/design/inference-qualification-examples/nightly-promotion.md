# Inference qualification

**0 regressions · 1 improvements · 1 within tolerance · 0 first measurements**

19 Nov 2026 14:00 UTC · Engine `bbbbbbbbbbbb` · nightly · Qualification: Passed · **Tolerance: ±1%** · NVIDIA GB10 DGX Spark

1 measured configurations. Counts refer to performance metrics. First measurements establish new series and have no trend verdict.

## Performance over time

Each panel shows one measured model and Spark configuration. The dashed line is the current comparison reference; the shaded band is ±1%. Blue circles are within tolerance, green triangles indicate improvements, red triangles indicate regressions, and grey squares mark first measurements. Missing measurements leave gaps. Dates are UTC.

### Output throughput

Higher is better.

![Output throughput over time for every measured configuration](summaries/nightly-promotion/charts/qualification-1.png)

### Time to first token

Lower is better.

![Time to first token over time for every measured configuration](summaries/nightly-promotion/charts/qualification-2.png)

## Latest numbers

Current values and signed changes against the saved reference, or the previous comparable measurement when no saved reference exists. Higher throughput and lower latency are better.

### One Spark

| Model | Output throughput (tok/s) | Time to first token (seconds) | Time between tokens (ms) |
| --- | --- | --- | --- |
| Qwen3.5-4B (1 Spark) | 30.42 (+15.71% · Improved) | 14.23 (-0.73% · Within tolerance) | — |


## Measurement notes

Two trials per configuration. Throughput and latency values are means of trial summaries. Latency is the mean of trial p95 values, not a pooled p95.
The ±1% rule expresses tolerance, not statistical significance. Comparisons preserve workload identity; a changed benchmark starts a new series.

[Full results and history](https://huggingface.co/datasets/meshllm/skippy-inference-qualification/tree/main/runs)
