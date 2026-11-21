"""The host platforms qualification runs on: what each must be and what it builds with."""

import os
import platform
import re
from pathlib import Path

from .ci_process import capture

PLATFORMS = {
    "spark": {"hardware": "nvidia-gb10-dgx-spark", "backend": "cuda", "modes": ("standalone", "split")},
    "metal": {"hardware": "apple-silicon-metal", "backend": "metal", "modes": ("standalone",)},
}

RECLAIMABLE = ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")


def known(name):
    if name not in PLATFORMS:
        raise ValueError(f"unknown inference platform: {name}")
    return PLATFORMS[name]


def device(name):
    """The accelerator's name, once this host is proven to be the platform's hardware."""
    if known(name)["backend"] == "cuda":
        if platform.system() != "Linux" or platform.machine() != "aarch64":
            raise ValueError("DGX Spark Linux aarch64 required")
        gpu = capture(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        if "GB10" not in gpu:
            raise ValueError("NVIDIA GB10 required")
        return gpu
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Apple silicon macOS required")
    chip = capture(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    if not chip.startswith("Apple M"):
        raise ValueError("Apple M-series chip required for Metal")
    return chip


def available_memory():
    if os.geteuid() == 0:
        raise ValueError("native inference must not execute as root")
    if platform.system() == "Darwin":
        return reclaimable_bytes(capture(["vm_stat"]))
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable missing")


def reclaimable_bytes(vm_stat):
    """macOS has no MemAvailable: count the pages a new allocation can take without swapping."""
    page = re.search(r"page size of ([0-9]+) bytes", vm_stat)
    counts = {}
    for line in vm_stat.splitlines():
        name, _, value = line.partition(":")
        counts[name.strip()] = value.strip().rstrip(".")
    if page is None or any(not counts.get(name, "").isdigit() for name in RECLAIMABLE):
        raise ValueError("vm_stat lacks the page counts admission needs")
    return int(page[1]) * sum(int(counts[name]) for name in RECLAIMABLE)


def build_toolchain(name):
    """The native toolchain facts that key the executable cache."""
    if known(name)["backend"] == "cuda":
        return {"cuda": capture(["nvcc", "--version"]), "backend": "cuda", "architectures": "121"}
    return {"macos": capture(["sw_vers", "-productVersion"]).strip(),
            "sdk": capture(["xcrun", "--show-sdk-version"]).strip(), "backend": "metal"}


def native_compiler(name, cmake_cache):
    """The compiler that built the native runtime, read from its CMake cache."""
    cuda = known(name)["backend"] == "cuda"
    variable = "CMAKE_CUDA_COMPILER" if cuda else "CMAKE_CXX_COMPILER"
    compiler = next((line.partition("=")[2] for line in cmake_cache.splitlines()
                     if line.startswith(variable + ":")), None)
    if not compiler:
        raise ValueError(f"native build cache lacks {variable}")
    return {("cuda_compiler" if cuda else "cxx_compiler"): capture([compiler, "--version"])}
