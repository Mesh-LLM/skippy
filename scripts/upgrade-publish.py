#!/usr/bin/env python3
"""Publish the immutable candidate selected by the successful full CI job."""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from lib.ci_artifacts import digest, read_json, source
from lib.ci_process import cancel_on_signal, command
from lib.upgrade_resolution import REPO, open_upgrade
from lib.upgrade_source import PIN, WORKFLOW, check_changes, git, validate_sha


def require_publisher():
    if (os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_REF"),
            os.environ.get("GITHUB_WORKFLOW_REF")) != (REPO, "refs/heads/main", WORKFLOW):
        raise ValueError("upgrade publication requires the protected-main Skippy upgrade workflow")


def import_candidate(root, bundle):
    candidate = read_json(bundle.with_name("candidate.json"))
    if (set(candidate) != {"schema_version", "base_sha", "candidate_sha", "upstream_sha", "bundle_sha256"}
            or type(candidate["schema_version"]) is not int or candidate["schema_version"] != 1
            or digest(bundle) != candidate["bundle_sha256"] or source() != candidate["base_sha"]):
        raise ValueError("candidate identity does not match frozen protected source")
    base = validate_sha(candidate["base_sha"])
    sha = validate_sha(candidate["candidate_sha"])
    upstream = validate_sha(candidate["upstream_sha"])
    git(root, "bundle", "verify", str(bundle), timeout=120)
    if git(root, "bundle", "list-heads", str(bundle)) != f"{sha} HEAD":
        raise ValueError("unexpected candidate bundle refs")
    git(root, "fetch", "--no-tags", str(bundle), "HEAD", timeout=120)
    if git(root, "rev-list", "--parents", "-n1", sha) != f"{sha} {base}":
        raise ValueError("upgrade candidate must be a direct non-merge child of its frozen base")
    check_changes(root, base, sha)
    if git(root, "show", f"{sha}:{PIN}") != upstream:
        raise ValueError("candidate pin differs from qualified upstream")
    return candidate


def authenticated(*args):
    token = os.environ["GH_TOKEN"]
    if not token:
        raise ValueError("upgrade publisher requires a scoped App token")
    credential = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    environment = {"GH_TOKEN": token, "GIT_CONFIG_COUNT": "1",
                   "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                   "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + credential}
    try:
        return command(list(args), overrides=environment, capture_stdout=True, limit=1024 * 1024,
                       stderr=subprocess.DEVNULL).strip()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise RuntimeError(f"authenticated {args[0]} failed") from None


def description(candidate):
    sha = validate_sha(candidate["candidate_sha"])
    upstream = validate_sha(candidate["upstream_sha"])
    run = os.environ["GITHUB_RUN_ID"]
    if not run.isdecimal():
        raise ValueError("invalid workflow run ID")
    return (
        "## Original problem\n\n"
        f"Integrate llama.cpp `{upstream}` with Skippy's native stage implementation. Tracks #12.\n\n"
        "## Diagnostics\n\n"
        f"[Upstream revision](https://github.com/ggml-org/llama.cpp/commit/{upstream}). "
        f"[Upgrade checks and repair evidence](https://github.com/{REPO}/actions/runs/{run}).\n\n"
        "## Fix\n\n"
        "Update the pinned native source, ordered patch queues, generated bindings and source-recipe identity. "
        f"The exact independently verified candidate is `{sha}`.\n\n"
        "## Compatibility\n\n"
        "Native ABI and graph contracts are checked by the existing CI gates. The native source-recipe "
        "identity changes with the pin and patch queue; runtime artifacts are built from this candidate.\n\n"
        "## Validation\n\n"
        "The controller's CPU `just lint`, `just test` and `just llama-check` gates passed, followed by "
        "independent full CI on this exact candidate. The linked run records the selected platform "
        "builds and smoke results. No additional live-model inference battery was run.\n\n"
        "## Acceptance criteria\n\n"
        "- [x] I ran the relevant local checks, or explained why they do not apply.\n"
        "- [x] I agree to license my contributions under the Apache License, Version 2.0.\n"
    )


def publish(candidate):
    sha, upstream = candidate["candidate_sha"], candidate["upstream_sha"]
    current = open_upgrade(upstream)
    if current:
        print(current)
        return
    body_text = description(candidate)
    branch = "automation/llama-" + sha[:12]
    authenticated("git", "push", f"https://github.com/{REPO}.git", f"{sha}:refs/heads/{branch}")
    existing = json.loads(authenticated("gh", "pr", "list", "--repo", REPO, "--head", branch,
                                        "--state", "open", "--json", "headRefOid,baseRefName,url"))
    if existing:
        if len(existing) != 1 or (existing[0]["headRefOid"], existing[0]["baseRefName"]) != (sha, "main"):
            raise ValueError("existing upgrade PR identity mismatch")
        print(existing[0]["url"])
        return
    with tempfile.TemporaryDirectory() as tmp:
        body = Path(tmp) / "body.md"
        body.write_text(body_text, encoding="utf-8")
        print(authenticated("gh", "pr", "create", "--repo", REPO, "--base", "main", "--head", branch,
                            "--title", f"Upgrade llama.cpp to {upstream[:12]}", "--body-file", str(body)))


def validated_candidate(root, bundle, expected):
    validate_sha(expected)
    candidate = import_candidate(root, bundle.resolve(strict=True))
    if candidate["candidate_sha"] != expected:
        raise ValueError("candidate differs from the source selected by successful full CI")
    return candidate


def main(bundle, expected):
    require_publisher()
    publish(validated_candidate(Path.cwd(), bundle, expected))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--validated-sha", required=True)
    args = parser.parse_args()
    try:
        with cancel_on_signal():
            main(args.candidate, args.validated_sha)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
