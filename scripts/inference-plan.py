#!/usr/bin/env python3
"""Validate the inference ladder and admit source revisions to qualification."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from lib.ci_artifacts import read_json, write_json
from lib.ci_process import command

from lib.inference_ladder import SELECTIONS, matrix, plan

REPO = "general-inference/skippy"


def github(path):
    return json.loads(command(["gh", "api", path], overrides={"GH_TOKEN": os.environ["GH_TOKEN"]},
                              capture_stdout=True))


def admit_pr(sha):
    pulls = github(f"repos/{REPO}/commits/{sha}/pulls")
    current = next((pr for pr in pulls if pr["head"]["sha"] == sha and pr["state"] == "open"), None)
    if current is None:
        raise ValueError("no current open PR for tested SHA")
    if current["head"]["repo"]["full_name"] != REPO:
        raise ValueError("fork PR needs isolated execution")
    number, author = current["number"], current["user"]["login"]
    if type(number) is not int or number <= 0 or not re.fullmatch(r"[A-Za-z0-9-]+", author):
        raise ValueError("invalid PR identity")
    permission = github(f"repos/{REPO}/collaborators/{author}/permission")["permission"]
    if permission not in {"admin", "write", "maintain"}:
        raise ValueError("PR author is not a trusted collaborator")
    if github(f"repos/{REPO}/pulls/{number}")["head"]["sha"] != sha:
        raise ValueError("PR head changed during inference admission")


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def admit_release_candidate(sha):
    if (os.environ["GITHUB_EVENT_NAME"] != "workflow_dispatch"
            or os.environ["GITHUB_REF"] != "refs/heads/main"
            or os.environ["GITHUB_WORKFLOW_REF"] != f"{REPO}/.github/workflows/release.yml@refs/heads/main"):
        raise ValueError("release candidates require a protected Release dispatch")
    run = os.environ["GITHUB_RUN_ID"]
    if not re.fullmatch(r"[1-9][0-9]*", run):
        raise ValueError("invalid release run ID")
    ref = github(f"repos/{REPO}/git/ref/heads/release-candidates/{run}")
    commit = github(f"repos/{REPO}/git/commits/{sha}")
    if (ref["object"]["sha"] != sha
            or [parent["sha"] for parent in commit["parents"]] != [os.environ["GITHUB_SHA"]]
            or not commit["message"].rstrip().endswith(f"\n\nSkippy-Release-Run: {run}")):
        raise ValueError("inference candidate does not belong to this release run")


def platforms(event, name):
    """Which lab platforms a run qualifies on: labels pick them for a PR, the input for a dispatch."""
    if name == "pull_request_target":
        labels = {label["name"] for label in event["pull_request"].get("labels", [])}
        spark, metal = "qualify" in labels, "qualify-metal" in labels
        return "all" if spark and metal else "metal" if metal else "spark"
    if name == "workflow_dispatch":
        selection = os.environ.get("QUALIFICATION_PLATFORM") or "spark"
        if selection not in SELECTIONS:
            raise ValueError(f"unknown platform selection: {selection}")
        return selection
    return "spark"


def admit():
    event = read_json(Path(os.environ["GITHUB_EVENT_PATH"]))
    name = os.environ["GITHUB_EVENT_NAME"]
    is_pr = name in {"workflow_run", "pull_request_target"}
    candidate = os.environ.get("RELEASE_CANDIDATE")
    if candidate:
        plan("release", candidate)
        admit_release_candidate(candidate)
        output(source=candidate, profile="release", platform="spark")
        return
    if name == "workflow_run":
        run = event["workflow_run"]
        if run["conclusion"] != "success":
            raise ValueError("upstream CI did not pass")
        sha = run["head_sha"]
    elif name == "pull_request_target":
        sha = event["pull_request"]["head"]["sha"]
    else:
        sha = os.environ["GITHUB_SHA"]
    profile = ("pr" if is_pr else "nightly" if name == "schedule"
               else os.environ.get("QUALIFICATION_PROFILE", "release"))
    selection = platforms(event, name)
    matrix(profile, sha, platform=selection)
    if is_pr:
        admit_pr(sha)
    output(source=sha, profile=profile, platform=selection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("admit")
    for name in ("matrix", "plan"):
        action = actions.add_parser(name)
        action.add_argument("profile")
        action.add_argument("sha")
        if name == "plan":
            action.add_argument("output", type=Path)
        else:
            action.add_argument("--include-smoke", action="store_true")
        action.add_argument("--platform", default="spark", choices=sorted(SELECTIONS))
    args = parser.parse_args()
    if args.action == "admit":
        admit()
        return
    if args.action == "matrix":
        rows = matrix(args.profile, args.sha, args.include_smoke, args.platform)
        output(matrix=json.dumps({"include": rows}, separators=(",", ":")))
    else:
        plans = [plan(args.profile, args.sha, host) for host in SELECTIONS[args.platform]]
        result = plans[0] if len(plans) == 1 else {
            **plans[0], "platform": args.platform, "hardware": [each["hardware"] for each in plans],
            "rows": [row for each in plans for row in each["rows"]],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, result)
        print(f'Wrote {len(result["rows"])} planned configurations; no inference was executed')


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
