#!/usr/bin/env python3
"""Build one host or runtime, or package previously verified outputs."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from lib import ci_artifacts as artifacts
from lib.ci_process import cancel_on_signal, capture, command
from lib.ci_targets import require_host, targets, validate_native_identity
from lib.ci_smoke import artifact_smoke, smoke


def build_host(host):
    require_host(host)
    command(["just", "skippy-cli-release-build"], timeout=7200)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        executable = artifacts.binary(stage, host["os"])
        shutil.copy(artifacts.binary(Path("target/release"), host["os"]), executable)
        command([
            sys.executable, "scripts/native-inspect.py", "verify-host-dependencies", str(executable),
            "--bind-sha256", "--report", str(stage / "host-imports.json"),
        ])
        artifacts.receipt(stage, "host", host["target"], "")
        artifacts.archive(stage, Path("dist/hosts") / host["id"] / "host.tar.gz")


def cuda_architectures(runtime):
    mode = os.environ.get("SKIPPY_CUDA_ARCHITECTURE_MODE", "release")
    if mode == "release":
        return runtime["gpu_architectures"]
    if mode != "native":
        raise ValueError(f"unsupported CUDA architecture mode: {mode}")
    detected = capture(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader,nounits"])
    values = set()
    for line in detected.splitlines():
        value = line.strip()
        if value:
            if not re.fullmatch(r"[0-9]+\.[0-9]+", value):
                raise ValueError(f"invalid CUDA compute capability: {value}")
            values.add(value.replace(".", ""))
    if not values:
        raise ValueError("nvidia-smi reported no CUDA compute capabilities")
    return ";".join(sorted(values))


def rocm_toolkit_version(root):
    info = root / ".info" / "version"
    if info.is_file():
        return info.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r".*[\\/](?:ROCm|HIP)[\\/](\d+\.\d+(?:\.\d+)?)", str(root))
    return match.group(1) if match else None


def rocm_versions_match(found, expected):
    if found is None:
        return False
    found = found.split("-", 1)[0]
    if found == expected:
        return True
    # The Windows HIP SDK installs into a two-segment root (7.2) for a
    # three-segment pin (7.2.0): a zero patch is implied. A pin with a
    # nonzero patch never matches the short form.
    parts = expected.split(".")
    short = found.split(".")
    return (len(short) == 2 and len(parts) == 3 and parts[2] == "0"
            and short[0] == parts[0] and short[1] == parts[1])


def build_runtime(host, runtime):
    require_host(host)
    backend = runtime["backend"]
    environment = {
        "LLAMA_STAGE_BACKEND": backend, "LLAMA_STAGE_BUILD_TESTS": "OFF",
        "LLAMA_STAGE_FULL_REPLAY": "OFF", "LLAMA_STAGE_UPSTREAM_TESTS": "OFF",
        "SKIPPY_NATIVE_RUNTIME_ARCHIVE": "0",
    }
    arches = None
    if backend == "cuda":
        arches = cuda_architectures(runtime)
        toolkit = runtime["toolkit"]
        if f'release {toolkit.rsplit(".", 1)[0]}' not in capture(["nvcc", "--version"]):
            raise ValueError(f"expected CUDA {toolkit}")
        environment.update({
            "SKIPPY_CUDA_VERSION": toolkit,
            "SKIPPY_CUDA_TOOLKIT_MAJOR": toolkit.split(".")[0],
            "LLAMA_STAGE_CUDA_ARCHITECTURES": arches,
        })
    elif backend == "rocm":
        windows = host["os"] == "windows"
        default_root = r"C:\Program Files\AMD\ROCm" if windows else "/opt/rocm"
        rocm = Path(os.environ.get("ROCM_PATH", default_root))
        toolkit = runtime["toolkit"]
        version = rocm_toolkit_version(rocm)
        if not rocm_versions_match(version, toolkit):
            found = "an unreadable version" if version is None else version
            raise ValueError(f"expected ROCm {toolkit}, found {found} at {rocm}")
        arches = runtime["gpu_architectures"]
        suffix = ".exe" if windows else ""
        environment.update({
            "ROCM_PATH": str(rocm),
            "HIPCC": os.environ.get("HIPCC") or str(rocm / f"llvm/bin/clang{suffix}"),
            "HIPCXX": os.environ.get("HIPCXX") or str(rocm / f"llvm/bin/clang++{suffix}"),
            "SKIPPY_ROCM_VERSION": toolkit,
            "LLAMA_STAGE_AMDGPU_TARGETS": arches,
        })
    # The Windows build waits for its outputs, but toolchain helpers can stay
    # in the job after it exits. Drain the job before verifying those outputs.
    # Cold Windows CUDA builds need headroom beyond three hours, inside the
    # existing four-hour Actions job budget.
    build_timeout = 12600 if host["os"] == "windows" and backend == "cuda" else 10800
    command(["just", "release-runtime-build", backend, host["target"]],
            timeout=build_timeout, overrides=environment, terminate_descendants=host["os"] == "windows")
    runtimes = [path.parent for path in Path("dist/native-runtimes").glob("*/manifest.json")]
    if len(runtimes) != 1:
        raise ValueError("native producer must emit exactly one runtime")
    directory = runtimes[0]
    validate_native_identity(directory, host, runtime, arches)
    command([sys.executable, "scripts/native-inspect.py", "verify-runtime-package", str(directory)])
    artifacts.receipt(directory, "runtime", host["target"], runtime["id"])
    output = Path("dist/runtimes") / runtime["id"]
    if output.exists():
        raise ValueError("runtime output directory must be absent")
    output.parent.mkdir(parents=True, exist_ok=True)
    directory.rename(output)


def package(host, runtime, host_input, runtime_input):
    require_host(host)
    variant = runtime["id"]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        host_dir, product = (root / name for name in ("host", "product"))
        runtime_dir = runtime_input
        artifacts.unpack(host_input, host_dir)
        artifacts.verify(host_dir, "host", host["target"], "")
        artifacts.verify(runtime_dir, "runtime", host["target"], variant)
        validate_native_identity(runtime_dir, host, runtime)
        artifacts.copy_tree(host_dir, product)
        (product / "provenance.json").unlink()
        artifacts.copy_tree(runtime_dir, product / "native-runtimes" / variant)
        for name in ("LICENSE", "NOTICE"):
            if Path(name).is_file():
                shutil.copy(name, product / name)
        artifacts.receipt(product, "product", host["target"], variant)
        artifacts.archive(product, Path("dist/products") / variant / f"skippy-{variant}.tar.gz")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("host", "runtime", "package", "smoke", "artifact-smoke"):
        action = actions.add_parser(name)
        action.add_argument("id")
        if name == "package":
            action.add_argument("host_input", type=Path)
            action.add_argument("runtime_input", type=Path)
        elif name in ("smoke", "artifact-smoke"):
            action.add_argument("product_input", type=Path)
    args = parser.parse_args()
    hosts, runtimes = targets()
    if args.action == "host":
        build_host(hosts[args.id])
    else:
        runtime = runtimes[args.id]
        host = hosts[runtime["host"]]
        if args.action == "runtime":
            build_runtime(host, runtime)
        elif args.action == "package":
            package(host, runtime, args.host_input, args.runtime_input)
        elif args.action == "artifact-smoke":
            artifact_smoke(host, runtime, args.product_input)
        else:
            smoke(host, runtime, args.product_input)


if __name__ == "__main__":
    try:
        with cancel_on_signal():
            main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
