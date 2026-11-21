"""Qualify native Apple M3 Ultra hardware and enforce its memory budget."""

import platform
import re
import time

from .ci_process import capture
from .ci_smoke import require_device


DEVICE = "Apple M3 Ultra"


def device_and_budget():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Metal qualification requires macOS ARM64")
    if capture(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() != DEVICE:
        raise ValueError(f"Metal qualification requires {DEVICE}")
    device, budget = capture(["xcrun", "swift", "-e", """
import Metal
let device = MTLCreateSystemDefaultDevice()!
print(device.name)
print(device.recommendedMaxWorkingSetSize)
"""]).strip().splitlines()
    if device != DEVICE or int(budget) <= 0:
        raise ValueError("required Metal device or working set limit unavailable")
    return device, int(budget)


def available_memory(stats, budget):
    page_size = re.search(r"page size of ([0-9]+) bytes", stats)
    counts = [re.search(rf"^Pages {kind}:\s+([0-9]+)\.$", stats, re.M)
              for kind in ("free", "inactive")]
    if not page_size or not all(counts):
        raise ValueError("macOS available memory could not be measured")
    return min(budget, int(page_size[1]) * sum(int(count[1]) for count in counts))


def offload(log, server):
    require_device([log], server, time.monotonic() + 5, prefixes=("MTL",))
