#!/usr/bin/env python3
"""Collect completed failing job logs for a bounded upgrade repair attempt."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from lib.upgrade_resolution import gh, REPO


def collect(output):
    run = os.environ["GITHUB_RUN_ID"]
    if not run.isdecimal():
        raise ValueError("invalid workflow run ID")
    output.mkdir(parents=True, exist_ok=True)
    status = {"run_id": run, "downloaded_jobs": [], "errors": []}
    try:
        # --jq works with paginated output on the runner's gh 2.45; --slurp
        # does not. Newer gh also requires an explicit opt-in for ANSI logs.
        help_text = gh("api", "--help")
        log_flags = ["--allow-escape-sequences"] if "--allow-escape-sequences" in help_text else []
        identifiers = gh("api", "--paginate", "--jq",
                         '.jobs[] | select(.conclusion == "failure") | .id',
                         f"repos/{REPO}/actions/runs/{run}/jobs?per_page=100")
        for line in identifiers.splitlines():
            identifier = json.loads(line)
            if type(identifier) is not int or identifier <= 0:
                raise ValueError("invalid workflow job ID")
            try:
                log = gh("api", *log_flags, f"repos/{REPO}/actions/jobs/{identifier}/logs")
                (output / f"actions-{identifier}.log").write_text(log, encoding="utf-8")
                status["downloaded_jobs"].append(identifier)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                status["errors"].append(f"job {identifier}: {error}")
    except (OSError, subprocess.SubprocessError) as error:
        status["errors"].append(str(error))
    (output / "actions-log-collection.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    if status["errors"]:
        print("::warning::Some Actions logs could not be collected; repair will use downloaded evidence "
              "and actions-log-collection.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    collect(parser.parse_args().output)
