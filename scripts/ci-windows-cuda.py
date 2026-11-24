#!/usr/bin/env python3
"""Install the pinned NVIDIA compiler and libraries on a hosted Windows build runner."""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from lib.ci_artifacts import digest


REDIST = "https://developer.download.nvidia.com/compute/cuda/redist/"
MANIFESTS = {
    "12.9.2": "236b051e6c7915a31ed9da369ea74ddb5b0fbf77eb1dffc10640e7ce6ed10257",
    "13.1.2": "33001778b29e0ad294d97eed123f2aec99a15403e8505eef43a86cdcdd6ef583",
}
REDISTRIBUTED = ("cuda_cudart", "libcublas", "libnvjitlink")


def install(version, root):
    with urllib.request.urlopen(f"{REDIST}redistrib_{version}.json", timeout=60) as response:
        raw = response.read()
    if hashlib.sha256(raw).hexdigest() != MANIFESTS[version]:
        raise ValueError(f"NVIDIA CUDA {version} manifest checksum mismatch")
    manifest = json.loads(raw)
    components = ["cuda_nvcc", "cuda_cccl", *REDISTRIBUTED]
    if version == "13.1.2":
        components.extend(("cuda_crt", "libnvvm"))

    root.mkdir(parents=True, exist_ok=False)
    licenses = []
    with tempfile.TemporaryDirectory(dir=root.parent) as work:
        for component in components:
            entry = manifest[component]["windows-x86_64"]
            relative = entry["relative_path"]
            archive = Path(work) / PurePosixPath(relative).name
            print(f"Downloading {archive.name}", flush=True)
            with urllib.request.urlopen(REDIST + relative, timeout=60) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            if archive.stat().st_size != int(entry["size"]) or digest(archive) != entry["sha256"]:
                raise ValueError(f"CUDA {component} archive checksum mismatch")
            with ZipFile(archive) as bundle:
                prefix = archive.stem + "/"
                if component in REDISTRIBUTED:
                    licenses.append(component.encode() + b"\n\n" + bundle.read(prefix + "LICENSE"))
                for member in bundle.infolist():
                    if member.is_dir() or member.filename == prefix + "LICENSE":
                        continue
                    if not member.filename.startswith(prefix):
                        raise ValueError(f"unexpected CUDA zip entry {member.filename}")
                    path = PurePosixPath(member.filename.removeprefix(prefix))
                    if path.is_absolute() or ".." in path.parts or ":" in str(path) or "\\" in str(path):
                        raise ValueError(f"unsafe CUDA zip entry {member.filename}")
                    destination = root.joinpath(*path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output)
            archive.unlink()
    (root / "EULA.txt").write_bytes(b"\n\n".join(licenses))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", choices=MANIFESTS)
    args = parser.parse_args()
    root = Path(os.environ["RUNNER_TEMP"]) / f"skippy-cuda-{args.version}"
    install(args.version, root)
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
        print(f"CUDA_PATH={root.as_posix()}", file=env)
        print(f"CUDAToolkit_ROOT={root.as_posix()}", file=env)
        print(f"CUDACXX={(root / 'bin/nvcc.exe').as_posix()}", file=env)
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as path:
        print(root / "bin", file=path)
        if args.version == "13.1.2":
            print(root / "bin/x64", file=path)


if __name__ == "__main__":
    main()
