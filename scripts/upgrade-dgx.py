#!/usr/bin/env python3
"""Dispatch the fixed seven Spark rows from the protected upgrade controller."""

import argparse
import os
from pathlib import Path

from lib.ci_process import command
from lib.inference_ladder import plan
from lib.upgrade_source import WORKFLOW, validate_sha


def main(sha, output, configuration):
    validate_sha(sha)
    if (os.environ.get("GITHUB_WORKFLOW_REF") != WORKFLOW or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("upgrade Spark dispatch requires the protected-main controller")
    run = os.environ["GITHUB_RUN_ID"]
    if not run.isdecimal():
        raise ValueError("invalid workflow run identity")
    ref = f"heads/upgrade-candidates/{run}/{sha[:12]}"
    staged = command(["gh", "api", f"repos/Mesh-LLM/skippy/git/ref/{ref}", "--jq", ".object.sha"],
                     overrides={"GH_TOKEN": os.environ["GH_TOKEN"]}, capture_stdout=True).strip()
    if staged != sha:
        raise ValueError("upgrade candidate ref identity mismatch")
    # The native execution runs candidate source. Controller and credentials stay on main.
    plan("upgrade", sha)
    command(["just", "inference-dispatch", "upgrade", sha, str(output), configuration], timeout=21000,
            overrides={"JIANYANG_INFERENCE_SSH_KEY": os.environ["JIANYANG_INFERENCE_SSH_KEY"]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha")
    parser.add_argument("output", type=Path)
    parser.add_argument("configuration")
    args = parser.parse_args()
    main(args.sha, args.output, args.configuration)
