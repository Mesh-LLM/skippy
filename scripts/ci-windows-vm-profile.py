#!/usr/bin/env python3
"""Validate a protected Windows rehearsal host profile before reserving its GPU."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def file_sha256(path):
    state = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            state.update(block)
    return state.hexdigest()


def firmware_arguments(profile, backend):
    if profile.get('firmware') is None:
        return []
    if profile['firmware'] != 'host':
        raise ValueError('firmware must select the installed host profile')
    value = json.loads(subprocess.run(
        [profile['lifecycle_manager'], 'describe', '--backend', backend, '--firmware'],
        check=True, capture_output=True, text=True,
    ).stdout)
    if not isinstance(value, dict) or set(value) not in (
            {'rom', 'rom_sha256'}, {'rom', 'rom_sha256', 'audio_rom', 'audio_rom_sha256'}):
        raise ValueError('host firmware must supply a pinned GPU ROM and optional paired audio ROM')
    result = []
    for name in ('rom', 'audio_rom'):
        if name not in value:
            continue
        path, digest = value[name], value[name + '_sha256']
        if (not isinstance(path, str) or not Path(path).is_absolute() or
                any(c in path for c in ('\r', '\n', ',')) or
                not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('invalid host firmware path or SHA-256')
        if file_sha256(path) != digest:
            raise ValueError('host firmware checksum mismatch')
        result.extend(['--' + name.replace('_', '-'), path,
                       '--' + name.replace('_', '-') + '-sha256', digest])
    return result


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
    if file_sha256(profile["driver"]) != profile["driver_sha256"]:
        raise ValueError("Windows GPU driver checksum mismatch")
    firmware = firmware_arguments(profile, backend)
    Path(profile["work_root"]).mkdir(parents=True, exist_ok=True)
    for name in ("base", "driver", "driver_sha256", "gpu", "work_root", "lifecycle_manager"):
        print(profile[name])
    for argument in firmware:
        print(argument)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
