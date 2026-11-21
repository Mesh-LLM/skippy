"""Drive one load point with the pinned AIPerf and read back what it measured."""

from pathlib import Path

from .ci_artifacts import read_json
from .ci_process import command
from .inference_inputs import downloaded_path, safe_relative

AIPERF = ["uv", "tool", "run", "--python", "3.12", "--constraints", "ci/aiperf-requirements.lock",
          "--from", "aiperf==0.13.0", "aiperf", "profile"]
# Output name -> (AIPerf export metric, statistic). Missing metrics stay null; the raw
# export is retained beside them, so a renamed field is visible rather than zero.
METRICS = {
    "output_tok_s": ("output_token_throughput", "avg"),
    "request_per_s": ("request_throughput", "avg"),
    "ttft_ms_p50": ("time_to_first_token", "p50"),
    "ttft_ms_p95": ("time_to_first_token", "p95"),
    "itl_ms_p50": ("inter_token_latency", "p50"),
    "latency_ms_p50": ("request_latency", "p50"),
    "output_tokens_mean": ("output_sequence_length", "avg"),
}


def tokenizer(fixture):
    files = fixture["tokenizer_files"]
    path = downloaded_path(command([
        "hf", "download", fixture["tokenizer_repo"], "--revision", fixture["tokenizer_revision"],
        "--quiet", *files,
    ], timeout=600, capture_stdout=True))
    if any((path / safe_relative(name)).stat().st_size == 0 for name in files):
        raise ValueError("empty pinned tokenizer asset")
    return path


def profile_args(endpoint, point, coordinate, tokenizer_path, warmup, directory):
    args = [
        *AIPERF, "--ui", "simple", "--url", endpoint, "--endpoint-type", "chat", "--streaming",
        "--model", coordinate, "--tokenizer", str(tokenizer_path), "--random-seed", "42",
        "--use-server-token-count", "--concurrency", str(point["concurrency"]),
        "--request-count", str(point["requests"]), "--warmup-request-count", str(warmup),
        "--synthetic-input-tokens-mean", str(point["input_tokens"]),
        "--synthetic-input-tokens-stddev", "0", "--output-tokens-mean", str(point["output_tokens"]),
        "--output-tokens-stddev", "0", "--extra-inputs", "ignore_eos:true",
        "--artifact-dir", str(directory),
    ]
    if point["depth"]:
        # One shared prefix per concurrent request: each conversation re-reads its own
        # prefix, so the KV pool has to hold concurrency x depth to keep them resident.
        args += ["--prefix-prompt-length", str(point["depth"]),
                 "--prefix-prompt-pool-size", str(point["concurrency"])]
    return args


def number(value):
    return float(value) if type(value) in {int, float} else None


def measurements(export):
    count, errors = export["request_count"]["avg"], export["request_error_rate"]["avg"]
    if (type(count) not in {int, float} or not count > 0 or errors != 0
            or export.get("error_summary") != [] or export.get("is_complete") is not True):
        raise ValueError("AIPerf produced empty, incomplete or failed request evidence")
    return {name: number((export.get(metric) or {}).get(statistic))
            for name, (metric, statistic) in METRICS.items()}


def run_point(endpoint, point, coordinate, tokenizer_path, warmup, directory: Path):
    command(profile_args(endpoint, point, coordinate, tokenizer_path, warmup, directory), timeout=5400)
    exports = list(directory.rglob("profile_export_aiperf.json"))
    if len(exports) != 1:
        raise ValueError(f'{point["id"]}: expected one AIPerf export, found {len(exports)}')
    return measurements(read_json(exports[0]))
