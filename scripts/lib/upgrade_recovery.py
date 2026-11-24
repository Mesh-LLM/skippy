"""Preserve unverified source before disposable upgrade workspaces disappear."""

import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .ci_process import DEADLINE, command, time_budget
from .upgrade_source import PIN, git

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_BYTES = 256 * 1024 * 1024


def snapshot(root, destination, base):
    destination.mkdir()
    patch = destination / "tracked.patch"
    with patch.open("wb") as stream:
        command(["git", "-C", str(root), "diff", "--binary", "--no-ext-diff", base, "--"],
                stdout=stream, timeout=120)
    if patch.stat().st_size > MAX_BYTES:
        raise ValueError("recovery patch exceeds 256 MiB")
    names = command(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
                    capture_stdout=True).split("\0")
    total = 0
    with tarfile.open(destination / "untracked.tar.gz", "w:gz", dereference=False) as archive:
        for name in filter(None, names):
            path = root / name
            if name.startswith((".deps/", "target/", ".skippy-")):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"recovery untracked input is not a regular file: {name}")
            total += path.stat().st_size
            if total > MAX_BYTES:
                raise ValueError("recovery untracked inputs exceed 256 MiB")
            archive.add(path, arcname=name, recursive=False)
    return {"base": base, "head": git(root, "rev-parse", "HEAD"), "unverified": True}


def checkpoint(checkout, recovery, label, *, base):
    recovery.mkdir(parents=True, exist_ok=True)
    # An interrupted checkpoint leaves previous completed snapshots intact.
    with tempfile.TemporaryDirectory(dir=recovery, prefix=".capturing-") as scratch:
        stage = Path(scratch) / "source"
        stage.mkdir()
        manifest = {"outer": snapshot(checkout, stage / "outer", base), "unverified": True}
        native = checkout / ".deps/llama.cpp"
        if (native / ".git").exists():
            pin = (checkout / PIN).read_text().strip()
            try:
                git(native, "cat-file", "-e", pin + "^{commit}")
            except subprocess.CalledProcessError:
                pin = EMPTY_TREE
            manifest["native"] = snapshot(native, stage / "native", pin)
            manifest["native"]["full_tracked_tree"] = pin == EMPTY_TREE
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        destination = recovery / f"{label}-{os.urandom(6).hex()}"
        stage.replace(destination)
        return destination


def retain(checkout, recovery, label, *, base):
    token = DEADLINE.set(None)
    try:
        # Recovery must work after the coding or verification deadline expires.
        with time_budget(180):
            return checkpoint(checkout, recovery, label, base=base)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Unverified source checkpoint failed: {error}", flush=True)
    finally:
        DEADLINE.reset(token)
