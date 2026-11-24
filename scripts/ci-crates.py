#!/usr/bin/env python3
"""Plan, verify, and publish Skippy's crates.io release packages."""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import time
from email.utils import parsedate_to_datetime
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from lib import ci_artifacts as artifacts
from lib.ci_process import capture, command
from lib.release_version import parse_tag, workspace_version


CPU_VARIANT = "linux-x86_64-cpu"
CPU_TARGET = "x86_64-unknown-linux-gnu"
USER_AGENT = "Skippy Release (https://github.com/Mesh-LLM/skippy)"


def publish_order(metadata, version):
    members = set(metadata["workspace_members"])
    packages = {
        Path(package["manifest_path"]).parent: package
        for package in metadata["packages"]
        if package["id"] in members
    }
    if any(package["publish"] not in ([], ["crates-io"]) for package in packages.values()):
        raise ValueError("workspace packages must explicitly select crates.io or disable publishing")
    enabled = {path: package for path, package in packages.items() if package["publish"]}
    if not enabled:
        raise ValueError("no workspace packages opt into crates.io")
    graph = {}
    for package in sorted(enabled.values(), key=lambda item: item["name"]):
        name = package["name"]
        if package["version"] != version:
            raise ValueError(f"{name} is outside release version {version}")
        deps = set()
        for dependency in package["dependencies"]:
            if not dependency.get("path") or dependency["kind"] == "dev":
                continue
            path = Path(dependency["path"])
            if path not in enabled or dependency["req"] not in {f"^{version}", f"={version}"}:
                raise ValueError(f"{name} needs a publishable, release-versioned {dependency['name']}")
            deps.add(enabled[path]["name"])
        graph[name] = sorted(deps)
    return list(TopologicalSorter(graph).static_order())


def plan():
    version = workspace_version()
    metadata = json.loads(capture(["cargo", "metadata", "--no-deps", "--locked", "--format-version", "1"]))
    return {"version": version, "packages": publish_order(metadata, version)}


def package_args(names):
    return [item for name in names for item in ("-p", name)]


def package(product):
    names = plan()["packages"]
    with TemporaryDirectory(prefix="skippy-crate-runtime-") as temp:
        root = Path(temp)
        artifacts.unpack(product, root)
        artifacts.verify(root, "product", CPU_TARGET, CPU_VARIANT)
        lib = root / "native-runtimes" / CPU_VARIANT / "lib"
        if not lib.is_dir():
            raise ValueError("verified Linux x64 CPU package is missing its native libraries")
        command(
            ["cargo", "package", "--locked", "--registry", "crates-io", *package_args(names)],
            overrides={
                "LLAMA_STAGE_LINK_MODE": "dynamic",
                "LLAMA_STAGE_BACKEND": "cpu",
                "LLAMA_STAGE_BUILD_DIR": str(lib.parent),
                "LLAMA_STAGE_LIB_DIR": str(lib),
                "LD_LIBRARY_PATH": str(lib),
            },
            timeout=3600,
        )


def registry_request(url):
    return urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=30)


def published(name, version, source):
    path = f"{name[:2]}/{name[2:4]}/{name}"
    try:
        with registry_request(f"https://index.crates.io/{path}") as response:
            entries = (json.loads(line) for line in response)
            entry = next((row for row in entries if row["vers"] == version), None)
    except HTTPError as error:
        if error.code == 404:
            return False
        raise
    if entry is None:
        return False
    if entry["yanked"]:
        raise ValueError(f"{name}@{version} is already published and yanked")
    with registry_request(f"https://static.crates.io/crates/{name}/{name}-{version}.crate") as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != entry["cksum"]:
        raise ValueError(f"{name}@{version} archive does not match the crates.io index")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as crate:
        member = f"{name}-{version}/.cargo_vcs_info.json"
        with crate.extractfile(member) as info:
            vcs = json.load(info)["git"]
    if vcs.get("sha1") != source or vcs.get("dirty", False):
        raise ValueError(f"{name}@{version} is already published from a different source")
    return True


def unpublished(names, version, source):
    remaining = []
    for name in names:
        if published(name, version, source):
            print(f"{name}@{version} already published from {source}", flush=True)
        else:
            remaining.append(name)
    return remaining


def retry_delay(log):
    match = re.search(r"Please try again after (.+?) and see https://crates\.io/docs/rate-limits", log)
    if match and "429" in log:
        return max(1, parsedate_to_datetime(match[1]).timestamp() - time.time() + 1)
    return None


def publish(tag, source):
    if parse_tag(tag) or workspace_version() != tag[1:]:
        raise ValueError("crate publishing requires the stable workspace release tag")
    tagged_source = capture(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"]).strip()
    if artifacts.source() != source or tagged_source != source:
        raise ValueError("crate publisher checkout does not match the verified release SHA")
    if not os.environ.get("CARGO_REGISTRY_TOKEN"):
        raise ValueError("crate publishing requires a crates.io credential")
    selected = plan()
    bootstrap = os.environ.get("SKIPPY_CRATES_BOOTSTRAP") == "true"
    deadline = time.monotonic() + (180 * 60 if bootstrap else 25 * 60)
    remaining = unpublished(selected["packages"], selected["version"], source)
    while remaining:
        # Build-free publisher: the release preflight already built these packages.
        # Cargo publishes interdependent packages in order and waits for the index.
        args = ["cargo", "publish", "--locked", "--registry", "crates-io", "--no-verify",
                *package_args(remaining)]
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=max(1, deadline - time.monotonic()))
        print(result.stdout, end="", flush=True)
        if result.returncode == 0:
            return
        delay = retry_delay(result.stdout) if bootstrap else None
        if delay is None or time.monotonic() + delay >= deadline:
            raise subprocess.CalledProcessError(result.returncode, args)
        print(f"Crates.io rate limit. Retrying in {delay:.0f} seconds.", flush=True)
        time.sleep(delay)
        remaining = unpublished(remaining, selected["version"], source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("plan")
    commands.add_parser("package").add_argument("product", type=Path)
    publisher = commands.add_parser("publish")
    publisher.add_argument("tag")
    publisher.add_argument("source")
    args = parser.parse_args()
    if args.action == "plan":
        print(json.dumps(plan(), indent=2))
    elif args.action == "package":
        package(args.product)
    else:
        publish(args.tag, args.source)


if __name__ == "__main__":
    try:
        main()
    except (CycleError, KeyError, OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
