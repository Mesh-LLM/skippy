#!/usr/bin/env python3
"""Compare verified qualification artifacts with the saved public baseline."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen

from lib.inference_baselines import DATASET, DIRECTION, SHA, apply_baselines, baseline_index, prior_state
from lib.inference_markdown import HF_DATASET, run_summary, utc
from lib.inference_nightly_metrics import HARNESS
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


def same_harness(profile, source, repository):
    # Receipt fingerprints come from the protected controller. Check that the
    # native candidate ran the same benchmark inputs before using those hashes.
    controller = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if source == controller:
        return True
    paths = (*HARNESS, "ci/inference-artifacts.json")
    paths += (("scripts/lib/inference_throughput.py",) if profile == "pr"
              else ("ci/inference-corpus.json", "ci/aiperf-requirements.lock"))
    listing = subprocess.check_output(["git", "ls-tree", "HEAD", "--", *paths], text=True)
    expected = {}
    for line in listing.splitlines():
        entry, path = line.split("\t", 1)
        expected[path] = entry.split()[2]
    tree = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/git/trees/{source}?recursive=1"], timeout=30,
    ))
    if tree["truncated"] or set(expected) != set(paths):
        raise ValueError("benchmark source inputs could not be verified")
    candidate = {entry["path"]: entry["sha"] for entry in tree["tree"] if entry["type"] == "blob"}
    return all(candidate.get(path) == sha for path, sha in expected.items())


def compare(run, repository, *, load=published_baselines, compatible=same_harness):
    run = dict(run)
    revision, saved = load()
    if revision:
        run["baseline_revision"] = revision
    if not revision:
        run["note"] = "Baseline comparison unavailable. The first nightly has yet to publish a baseline."
    elif saved and not compatible(run["profile"], run["source_sha"], repository):
        run["note"] = "Baseline comparison unavailable for this run because its benchmark inputs changed."
    else:
        run, _ = apply_baselines(run, saved, read_only=True)
        rows = [row for config in run["configurations"] for row in config["metrics"]
                if row["metric"] in DIRECTION and row["measured"] is not None]
        matched = sum("reference" in row for row in rows)
        run["note"] = (
            f"{matched} of {len(rows)} measured performance metrics had a matching published baseline. "
            "A change beyond 5% in the unfavorable direction fails qualification."
        )
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
    with Path(environment["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
        stream.write(run_summary({"schema_version": 1, "runs": [run]}, run["id"]))
    return 0 if run["outcome"] == "passed" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Inference comparison failed: {error}")
