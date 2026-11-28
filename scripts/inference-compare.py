#!/usr/bin/env python3
"""Compare verified qualification artifacts with the saved public baseline."""

import argparse
import base64
from functools import cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen

from lib.inference_baselines import DATASET, DIRECTION, SHA, apply_baselines, baseline_index, prior_state
from lib.inference_markdown import HF_DATASET, run_summary, utc
from lib.inference_nightly_metrics import benchmark_definition
from lib.inference_comparability import benchmark_functions
from lib.inference_nightly_results import qualification_run


def public_json(url):
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def published_baselines(read=public_json):
    info = read(f"https://huggingface.co/api/datasets/{DATASET}")
    revision = info["sha"]
    if (info["id"] != DATASET or info["private"] is not False
            or info["gated"] is not False or not SHA.fullmatch(revision)):
        raise ValueError("expected the public, ungated qualification dataset")
    files = {entry["rfilename"] for entry in info["siblings"]}
    state = prior_state(files, lambda path: read(f"{HF_DATASET}/resolve/{revision}/{path}"))
    baseline_index(state["baselines"])
    return (revision if state["history"] else None), state["baselines"]


@cache
def source_text(source, path, repository):
    if not SHA.fullmatch(source):
        raise ValueError("invalid benchmark source SHA")
    local = subprocess.run(["git", "show", f"{source}:{path}"], capture_output=True, text=True)
    if local.returncode == 0:
        return local.stdout
    value = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/contents/{path}?ref={source}"], timeout=30,
    ))
    if value["encoding"] != "base64" or value["size"] > 4 * 1024 * 1024:
        raise ValueError("unsupported benchmark source content")
    return base64.b64decode(value["content"]).decode("utf-8")


def same_harness(profile, source, repository, configuration=None):
    # Candidate text is parsed as data; it is never imported or executed by
    # the credentialed controller. Compare each configuration's effective
    # benchmark, so split admission edits cannot reset standalone history.
    controller = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if source == controller:
        return True
    configuration = configuration or "standalone"
    mode = "split" if configuration.endswith("-split") else "standalone"
    backend = "metal" if configuration.startswith("metal-") else "cuda"
    functions, files = benchmark_definition(profile, mode, backend)
    read = lambda sha, path: source_text(sha, path, repository)
    for path, names in functions.items():
        if benchmark_functions(path, names, source=read(source, path)) != benchmark_functions(
            path, names, source=read(controller, path),
        ):
            return False
    for path in files:
        if read(source, path) != read(controller, path):
            return False
    candidate = json.loads(read(source, "ci/inference-artifacts.json"))
    protected = json.loads(read(controller, "ci/inference-artifacts.json"))
    model = configuration.removeprefix("metal-").removesuffix("-" + mode)
    return candidate.get(model) == protected.get(model) and model in candidate


def compare(run, repository, *, load=published_baselines, compatible=same_harness):
    run = dict(run)
    if run["profile"] == "release":
        run["comparison_notice"] = "Release measurements establish separate series; nightly baselines do not apply."
        return run
    revision, saved = load()
    if revision:
        run["baseline_revision"] = revision
    if not revision:
        run["comparison_notice"] = "No saved baseline exists yet; measured values establish new series."
    else:
        run["configurations"] = [dict(config) for config in run["configurations"]]
        for config in run["configurations"]:
            config["benchmark_compatible"] = compatible(
                run["profile"], run["source_sha"], repository, config["id"],
            )
        changed = sum(config["benchmark_compatible"] is False for config in run["configurations"])
        run, _ = apply_baselines(run, saved, read_only=True)
        rows = [row for config in run["configurations"] for row in config["metrics"]
                if row["metric"] in DIRECTION and row["measured"] is not None]
        matched = sum("reference" in row for row in rows)
        run["comparison_notice"] = (
            f"{matched} of {len(rows)} measured performance metrics have a matching published baseline. "
            "An unfavorable change beyond 1% fails qualification."
        )
        if changed:
            run["comparison_notice"] += f" {changed} configurations have changed benchmark inputs and start new series."
    run["note"] = run["comparison_notice"]
    if run["profile"] == "nightly":
        run["note"] += " AgentX values are means of two trial summaries. Request counts are combined."
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    environment = os.environ
    source, profile = environment["SOURCE_SHA"], environment["PROFILE"]
    run_id, attempt = environment["GITHUB_RUN_ID"], environment["GITHUB_RUN_ATTEMPT"]
    if not SHA.fullmatch(source) or any(not re.fullmatch(r"[1-9][0-9]*", v) for v in (run_id, attempt)):
        raise ValueError("invalid qualification source or run identity")
    started_at = environment["RUN_STARTED_AT"]
    utc(started_at)
    result = environment["MATRIX_RESULT"]
    if result not in {"success", "failure", "cancelled", "skipped"}:
        raise ValueError("invalid qualification matrix result")
    run = qualification_run(args.evidence, profile, source, run_id, attempt, started_at, result == "success")
    run["url"] = f'{environment["GITHUB_SERVER_URL"]}/{environment["GITHUB_REPOSITORY"]}/actions/runs/{run_id}/attempts/{attempt}'
    try:
        run = compare(run, environment["GITHUB_REPOSITORY"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        print(f"Cannot read or verify the published baseline: {error}", file=sys.stderr)
        run["outcome"] = "failed"
        run["note"] = "Baseline comparison failed. See the comparison step log for details."
        run["comparison_error"] = run["note"]
    with Path(environment["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        stream.write(run_summary({"schema_version": 1, "runs": [run]}, run["id"], chart_format="mermaid"))
    return 0 if run["outcome"] == "passed" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Inference comparison failed: {error}")
