"""One closed table of the backends a performance comparison can run on.

An entry holds everything backend-specific: the selector fragment of the JY target, the
Skippy build overrides, the reference build's accelerator CMake flags, the tool check that
runs before any build, the script that resolves a native toolkit when the reference build
needs one, the device prefixes that count as GPU offload evidence, and the functions that
prove the host and measure its free memory. `admit` refuses a `BACKEND` with no entry and
records the one it accepts in the plan; build and execute read the backend from the plan
and never infer it from the host, so a job always measures the machine it was planned for.
A backend without a complete entry therefore fails at plan time, not partway through a job.
"""

import platform
from pathlib import Path

from . import inference_metal
from .ci_process import capture


def linux_release():
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return ""


def metal_facts():
    chip = inference_metal.chip()
    return {"chip": chip, "macos": capture(["sw_vers", "-productVersion"]).strip(),
            "metal_working_set_bytes": inference_metal.working_set(chip)}


def metal_free(host):
    return inference_metal.available_memory(capture(["vm_stat"]), host["metal_working_set_bytes"])


def cuda_facts():
    """A Linux aarch64 host with an NVIDIA GB10, proved the way qualification's authority proves it."""
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise ValueError("the cuda backend requires Linux aarch64 (a DGX Spark)")
    gpu = capture(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]).strip()
    if "GB10" not in gpu:
        raise ValueError(f"the cuda backend requires an NVIDIA GB10, not {gpu!r}")
    driver = capture(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]).strip()
    cuda = ""
    for line in capture(["nvidia-smi", "--version"]).splitlines():
        if "CUDA Version" in line:
            cuda = line.split(":", 1)[1].strip()
            break
    return {"gpu": gpu, "driver_version": driver, "toolkit_version": cuda or None,
            "os": "linux", "os_version": linux_release(), "kernel": capture(["uname", "-r"]).strip()}


def cuda_free(host):
    """Unified memory: everything the kernel will hand out is what a model competes for."""
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable missing")


# The unified reserve, shared system memory for everything beside the weights.
RESERVE_GIB = 16

BACKENDS = {
    "metal": {
        "description": "Same-host Metal comparison",
        "selector": {"os": {"family": "macos"}, "accelerator": {"backend": "metal"}},
        "memory_key": "system_gib",
        "reserve_gib": RESERVE_GIB,
        "pin_required": False,
        "skippy_env": {"LLAMA_STAGE_BACKEND": "metal", "LLAMA_STAGE_LINK_MODE": "static"},
        "reference_cmake": ["-DGGML_METAL=ON"],
        "tool_check": ["xcrun", "--find", "swift"],
        "toolkit_script": None,
        "device_prefixes": ("MTL",),
        "facts": metal_facts,
        "free_memory": metal_free,
        "env": {},
    },
    "cuda": {
        "description": "Same-host CUDA comparison",
        "selector": {"os": {"family": "linux"}, "arch": {"in": ["aarch64"]},
                     "accelerator": {"backend": "cuda", "model": {"in": ["NVIDIA GB10"]}},
                     "memory": {"unified": True}},
        "memory_key": "system_gib",
        "reserve_gib": RESERVE_GIB,
        "pin_required": False,
        "skippy_env": {"LLAMA_STAGE_BACKEND": "cuda", "LLAMA_STAGE_LINK_MODE": "static",
                       "LLAMA_STAGE_CUDA_ARCHITECTURES": "121"},
        "reference_cmake": ["-DGGML_CUDA=ON", "-DCMAKE_CUDA_ARCHITECTURES=121"],
        "tool_check": ["nvidia-smi"],
        "toolkit_script": "scripts/lib/cuda-toolkit.sh",
        "device_prefixes": ("CUDA",),
        "facts": cuda_facts,
        "free_memory": cuda_free,
        "env": {},
    },
}
