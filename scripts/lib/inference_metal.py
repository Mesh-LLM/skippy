"""Prove native Apple silicon Metal hardware and enforce its memory budget.

Qualification requires the Apple M3 Ultra; performance comparisons take any
Apple M-series Mac, since they compare arms on one host rather than across
hosts.
"""

import platform
import re
import time

from .ci_process import capture
from .ci_smoke import require_device


DEVICE = "Apple M3 Ultra"


def chip():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Metal requires macOS ARM64")
    name = capture(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    if not name.startswith("Apple M"):
        raise ValueError(f"Metal requires an Apple M-series chip, not {name!r}")
    return name


def working_set(name):
    """Metal's working set limit, once Metal reports this chip's own GPU."""
    device, budget = capture(["xcrun", "swift", "-e", """
import Metal
let device = MTLCreateSystemDefaultDevice()!
print(device.name)
print(device.recommendedMaxWorkingSetSize)
"""]).strip().splitlines()
    if device != name or int(budget) <= 0:
        raise ValueError("required Metal device or working set limit unavailable")
    return int(budget)


def device_and_budget():
    name = chip()
    if name != DEVICE:
        raise ValueError(f"Metal qualification requires {DEVICE}")
    return name, working_set(name)


def available_memory(stats, budget):
    page_size = re.search(r"page size of ([0-9]+) bytes", stats)
    counts = [re.search(rf"^Pages {kind}:\s+([0-9]+)\.$", stats, re.M)
              for kind in ("free", "inactive")]
    if not page_size or not all(counts):
        raise ValueError("macOS available memory could not be measured")
    return min(budget, int(page_size[1]) * sum(int(count[1]) for count in counts))


def offload(log, server):
    require_device([log], server, time.monotonic() + 5, prefixes=("MTL",))
