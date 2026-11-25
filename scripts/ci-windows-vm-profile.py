#!/usr/bin/env python3
"""Validate a protected Windows rehearsal host profile before reserving its GPU."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def main():
    profile = json.loads(os.environ["WINDOWS_HOST_PROFILE"])
    backend = os.environ["WINDOWS_BACKEND"]
    if backend not in ("cuda", "rocm"):
        raise ValueError("unsupported Windows backend")
    for name in ("base", "driver", "driver_sha256", "gpu", "work_root", "lifecycle_manager"):
        value = profile.get(name)
        if not isinstance(value, str) or not value or any(c in value for c in ("\r", "\n", ",")):
            raise ValueError(f"invalid or missing Windows profile field: {name}")
    if not re.fullmatch(r"[a-f0-9]{64}", profile["driver_sha256"]):
        raise ValueError("driver SHA-256 must pin the supplied Windows installer")
    if profile["gpu"] == "auto":
        profile["gpu"] = subprocess.run(
            [profile["lifecycle_manager"], "describe", "--backend", backend],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]", profile["gpu"]):
        raise ValueError("GPU must be a full PCI address")
    for name in ("base", "driver", "work_root", "lifecycle_manager"):
        if not Path(profile[name]).is_absolute():
            raise ValueError(f"{name} must be an absolute host path")
    if not os.access(profile["lifecycle_manager"], os.X_OK):
        raise ValueError("the host GPU lifecycle manager is not installed/executable")
    if not Path(profile["base"], "base.json").is_file():
        raise ValueError("Windows base image and its receipt must be prepared first")
    for name in ("base",):
        path = Path(profile[name])
        while not path.exists():
            path = path.parent
        filesystem = subprocess.run(
            ["findmnt", "--noheadings", "--output", "FSTYPE", "--target", str(path)],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        if set(filesystem) - {"autofs"} not in ({"nfs"}, {"nfs4"}):
            raise ValueError(f"{name} must be on the lab's mounted NFS cache")
    driver = Path(profile["driver"])
    state = hashlib.sha256()
    with driver.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            state.update(block)
    if state.hexdigest() != profile["driver_sha256"]:
        raise ValueError("Windows GPU driver checksum mismatch")
    Path(profile["work_root"]).mkdir(parents=True, exist_ok=True)
    for name in ("base", "driver", "driver_sha256", "gpu", "work_root", "lifecycle_manager"):
        print(profile[name])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
