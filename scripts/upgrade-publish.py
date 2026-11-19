#!/usr/bin/env python3
"""Check both complete upgrade qualification passes and publish their exact commit."""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from lib.ci_artifacts import digest, extract_archive, read_json, source
from lib.ci_process import cancel_on_signal, command
from lib.upgrade_evidence import MANIFEST, RECIPE, family, inventory, recipe_digest
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


def candidate_recipe(sha):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        archive, checkout = root / "source.tar", root / "source"
        command(["git", "archive", "--format=tar", "--output", str(archive), sha, "--", *RECIPE])
        extract_archive(archive, checkout, allow_symlinks=False)
        return recipe_digest(checkout)


def expected_families(manifest):
    expected = {}
    for model in manifest["models"]:
        receipt = family(model)
        name = receipt["family"]
        if name in expected:
            raise ValueError("duplicate supported family")
        receipt["lanes"].sort()
        expected[name] = receipt
    required = {"qwen3-dense", "qwen3-moe", "qwen3-next", "qwen3-vl", "qwen3vlmoe", "qwen3tts"}
    if not required <= set(expected):
        raise ValueError("supported family roster is missing Qwen3 variants")
    return expected


def admit_receipts(root, candidate, manifest, manifest_hash, recipe):
    expected = expected_families(manifest)
    for pass_name in ("candidate", "independent"):
        for backend in ("cpu", "metal", "cuda"):
            directory = root / f"llama-{pass_name}-{backend}-evidence"
            roster = set(inventory(directory, [directory]))
            receipt = read_json(directory / "receipt.json")
            identity = {"schema_version": 1, "candidate_sha": candidate["candidate_sha"],
                        "upstream_sha": candidate["upstream_sha"], "manifest_sha256": manifest_hash,
                        "native_recipe_sha256": recipe, "backend": backend, "pass": pass_name, "result": "pass"}
            if (set(receipt) != {*identity, "families", "artifacts"} or any(
                type(receipt[key]) is not type(value) or receipt[key] != value
                for key, value in identity.items()
            )):
                raise ValueError(f"invalid source, recipe or result in {pass_name}/{backend} receipt")
            artifacts = receipt["artifacts"]
            if not artifacts or "receipt.json" in artifacts or roster != {*artifacts, "receipt.json"}:
                raise ValueError("family evidence contains missing or unaccounted files")
            actual = {}
            for item in receipt["families"]:
                name = item["family"]
                if name in actual:
                    raise ValueError("duplicate family evidence")
                actual[name] = dict(item, lanes=sorted(item["lanes"]))
            if actual != expected:
                raise ValueError(f"incomplete family/model/lane coverage in {pass_name}/{backend}")
            for path, checksum in artifacts.items():
                if digest(directory / path) != checksum:
                    raise ValueError("changed family evidence artifact")


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


def publish(candidate):
    sha, upstream = candidate["candidate_sha"], candidate["upstream_sha"]
    current = open_upgrade(upstream)
    if current:
        print(current)
        return
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
        body.write_text(
            f"Upgrade Skippy's native inference engine to llama.cpp `{upstream}`.\n\n"
            "The complete supported-family roster passed CPU, Metal and CUDA qualification and "
            "an independent clean verification of this exact candidate. Both passes used the "
            "normal platform CI and packaged Qwen3 smoke matrix.\n\n"
            f"Candidate: `{sha}`\n", encoding="utf-8",
        )
        print(authenticated("gh", "pr", "create", "--repo", REPO, "--base", "main", "--head", branch,
                            "--title", f"Upgrade llama.cpp to {upstream[:12]}", "--body-file", str(body)))


def main(bundle, evidence):
    require_publisher()
    candidate = import_candidate(Path.cwd(), bundle.resolve(strict=True))
    sha = candidate["candidate_sha"]
    manifest = command(["git", "show", f"{sha}:{MANIFEST}"], capture_stdout=True, raw=True)
    admit_receipts(evidence, candidate, json.loads(manifest), hashlib.sha256(manifest).hexdigest(),
                   candidate_recipe(sha))
    publish(candidate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    try:
        with cancel_on_signal():
            main(args.candidate, args.evidence)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
