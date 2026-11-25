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
import re
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


# A discrete GPU runs no operating system beside the model, so its reserve covers only the
# KV pools and recurrent state of the deepest cells and both arms' compute buffers, not
# the unified 16 GiB. Sized in the cuda-x86 platform's step 3; see INFERENCE_PERFORMANCE.md.
DISCRETE_RESERVE_GIB = 6


def cuda_x86_architecture():
    """The host GPU's compute capability, read at build time: 8.9 means architecture 89."""
    capability = capture(["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader",
                          "-i", "0"]).strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]", capability):
        raise ValueError(f"GPU 0 reported no compute capability: {capability!r}")
    return capability.replace(".", "")


def cuda_x86_skippy_env():
    return {"LLAMA_STAGE_BACKEND": "cuda", "LLAMA_STAGE_LINK_MODE": "static",
            "LLAMA_STAGE_CUDA_ARCHITECTURES": cuda_x86_architecture()}


def cuda_x86_reference_cmake():
    return ["-DGGML_CUDA=ON", f"-DCMAKE_CUDA_ARCHITECTURES={cuda_x86_architecture()}"]


def cuda_x86_facts():
    """A discrete-NVIDIA Linux x86_64 host, described by GPU 0: the one the job pins."""
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("the cuda-x86 backend requires Linux x86_64")
    row = capture(["nvidia-smi", "--query-gpu=name,driver_version,compute_cap,memory.total,"
                   "memory.free,count", "--format=csv,noheader,nounits", "-i", "0"]).strip()
    fields = [item.strip() for item in row.split(",")]
    if len(fields) != 6:
        raise ValueError(f"nvidia-smi did not describe GPU 0: {row!r}")
    name, driver, capability, total_mib, free_mib, count = fields
    cuda = ""
    for line in capture(["nvidia-smi", "--version"]).splitlines():
        if "CUDA Version" in line:
            cuda = line.split(":", 1)[1].strip()
            break
    processes = capture(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                         "--format=csv", "-i", "0"]).strip()
    return {"gpu": name, "gpu_count": int(count), "gpu_used": 0,
            "gpu_compute_capability": capability,
            "gpu_memory_total_bytes": int(total_mib) * 1024**2,
            "gpu_memory_free_bytes": int(free_mib) * 1024**2,
            "driver_version": driver, "toolkit_version": cuda or None,
            "os": "linux", "os_version": linux_release(), "kernel": capture(["uname", "-r"]).strip(),
            "gpu_processes": processes or None}


def cuda_x86_free(host):
    """The weights must fit GPU 0's own free VRAM, and Skippy may also hold KV in host RAM."""
    return min(host["gpu_memory_free_bytes"], cuda_free(host))


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
    # A discrete NVIDIA GPU on Linux x86_64. x86 hosts carry different cards, so a plan
    # must name one node, both arms build for that card's own architecture, and the job
    # pins GPU 0 with CUDA_VISIBLE_DEVICES. Admission is against GPU 0's free VRAM.
    "cuda-x86": {
        "description": "Same-host CUDA comparison",
        "selector": {"os": {"family": "linux"}, "arch": {"in": ["x86_64"]},
                     "accelerator": {"backend": "cuda"}, "memory": {"unified": False}},
        "memory_key": "dedicated_vram_gib",
        "reserve_gib": DISCRETE_RESERVE_GIB,
        "pin_required": True,
        "skippy_env": cuda_x86_skippy_env,
        "reference_cmake": cuda_x86_reference_cmake,
        "tool_check": ["nvidia-smi"],
        "toolkit_script": "scripts/lib/cuda-toolkit.sh",
        "device_prefixes": ("CUDA",),
        "facts": cuda_x86_facts,
        "free_memory": cuda_x86_free,
        "env": {"CUDA_VISIBLE_DEVICES": "0"},
    },
}
