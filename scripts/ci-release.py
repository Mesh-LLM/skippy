#!/usr/bin/env python3
"""Admit release tags and inventory immutable release assets."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from lib import ci_artifacts as artifacts
from lib.ci_process import cancel_on_signal, capture, command
from lib.ci_targets import targets, validate_native_identity


def version(tag):
    match = re.fullmatch(
        r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", tag,
    )
    if not match or any(int(part) >= 2**64 for part in match.groups()[:3]):
        raise ValueError("release tag is not canonical SemVer")
    prerelease = match.group(4)
    if prerelease and any(
        len(part) > 1 and part.startswith("0") and part.isdigit()
        for part in prerelease.split(".")
    ):
        raise ValueError("release tag is not canonical SemVer")
    with open("Cargo.toml", "rb") as stream:
        committed = tomllib.load(stream)["workspace"]["package"]["version"]
    if committed != tag[1:]:
        raise ValueError("tag version differs from committed workspace version")
    return "true" if prerelease else "false"


def github_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def validate(tag):
    prerelease = version(tag)
    source = artifacts.source()
    if capture(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"]).strip() != source:
        raise ValueError("checkout is not the exact tagged commit")
    command(["git", "merge-base", "--is-ancestor", source, "refs/remotes/origin/main"], timeout=60)
    github_output("prerelease", prerelease)
    github_output("source", source)
    return source


def prepare(tag, input_dir):
    source = validate(tag)
    hosts, runtimes = targets()
    output = Path("dist/release")
    if output.exists() and any(output.iterdir()):
        raise ValueError("release output directory must be empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".release-") as tmp:
        stage = Path(tmp) / "release"
        stage.mkdir()
        assets = {}
        for variant, runtime in runtimes.items():
            directory = input_dir / f"release-product-{variant}"
            host = hosts[runtime["host"]]
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                artifacts.unpack(directory, root)
                artifacts.verify(root, "product", host["target"], variant)
                arches = runtime["gpu_architectures"] if runtime["backend"] == "cuda" else None
                validate_native_identity(root / "native-runtimes" / variant, host, runtime, arches)
            evidence = artifacts.read_json(directory / "evidence/smoke.json")
            if (evidence.get("source"), evidence.get("variant"), evidence.get("backend")) != (
                source, variant, runtime["backend"]
            ):
                raise ValueError(f"missing or mismatched smoke evidence for {variant}")
            file = stage / f"skippy-{tag}-{variant}.tar.gz"
            shutil.copyfile(directory / f"skippy-{variant}.tar.gz", file)
            artifacts.write_checksum(file)
            assets[file.name] = artifacts.digest(file)
        index = stage / "release.json"
        artifacts.write_json(index, {
            "schema_version": 1, "tag": tag, "source": source,
            "upstream_pin": Path("llama_cpp/upstream.txt").read_text(encoding="utf-8").strip(),
            "assets": dict(sorted(assets.items())), "variants": list(runtimes),
        })
        artifacts.write_checksum(index)
        verify(stage)
        stage.rename(output)


def verify(directory):
    index = directory / "release.json"
    release = artifacts.read_json(index)
    _, runtimes = targets()
    if (
        set(release) != {"schema_version", "tag", "source", "upstream_pin", "assets", "variants"}
        or type(release["schema_version"]) is not int or release["schema_version"] != 1
        or release["source"] != artifacts.source() or release["variants"] != list(runtimes)
        or not isinstance(release["upstream_pin"], str)
    ):
        raise ValueError("release roster/source mismatch")
    tag = release["tag"]
    version(tag)
    filenames = {f"skippy-{tag}-{variant}.tar.gz" for variant in runtimes}
    if type(release["assets"]) is not dict or set(release["assets"]) != filenames:
        raise ValueError("release archive roster mismatch")
    for name, expected in release["assets"].items():
        file = directory / name
        if artifacts.digest(file) != expected:
            raise ValueError(f"release archive identity mismatch: {name}")
        artifacts.check_checksum(file)
    artifacts.check_checksum(index)
    assets = filenames | {index.name}
    if {file.name for file in directory.iterdir()} != assets | {name + ".sha256" for name in assets}:
        raise ValueError("release contains unrecognized assets")
    return release


def admit_draft(path):
    release = artifacts.read_json(path)
    if release.get("isDraft") is not True:
        raise ValueError("refusing to overwrite a published release")
    source = validate(release["tagName"])
    if release.get("body") != f"Skippy source: {source}":
        raise ValueError("draft source identity does not match checkout")


def select(tag, path):
    validate(tag)
    releases = artifacts.read_json(path)
    if not isinstance(releases, list):
        raise ValueError("expected a release list")
    matches = sum(release.get("tagName") == tag for release in releases)
    if matches > 1:
        raise ValueError("duplicate release tags in API response")
    github_output("exists", "true" if matches else "false")


def upload_plan(directory, path):
    local = verify(directory)
    remote = artifacts.read_json(path)
    if (remote.get("draft") is not True or remote.get("tag_name") != local["tag"]
            or remote.get("body") != f'Skippy source: {local["source"]}'):
        raise ValueError("refusing a published or foreign draft release")
    if not isinstance(remote.get("assets"), list):
        raise ValueError("release API omitted assets")
    missing = artifacts.files(directory)
    for asset in remote["assets"]:
        name = asset["name"]
        expected = missing.pop(name, None)
        if expected is None or asset.get("digest") != "sha256:" + expected:
            raise ValueError(f"existing draft asset differs or has no verified digest: {name}")
    Path("release-upload.txt").write_text(
        "".join(f"{directory / name}\n" for name in missing), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for func, arguments in (
        (validate, (("tag", str),)),
        (prepare, (("tag", str), ("input_dir", Path))),
        (verify, (("directory", Path),)),
        (admit_draft, (("path", Path),)),
        (select, (("tag", str), ("path", Path))),
        (upload_plan, (("directory", Path), ("path", Path))),
    ):
        action = actions.add_parser(func.__name__.replace("_", "-"))
        for name, kind in arguments:
            action.add_argument(name, type=kind)
        action.set_defaults(run=func, names=[name for name, _ in arguments])
    args = parser.parse_args()
    args.run(*(getattr(args, name) for name in args.names))


if __name__ == "__main__":
    try:
        with cancel_on_signal():
            main()
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
