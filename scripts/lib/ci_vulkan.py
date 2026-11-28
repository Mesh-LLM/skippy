"""Bind Vulkan qualification to real devices and the declared driver vendor."""

import os
import re
from pathlib import Path

from .ci_artifacts import digest
from .ci_process import capture

VENDORS = {"nvidia": 0x10DE, "amd": 0x1002}
DEVICE_TYPES = {"PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU"}


def devices(summary, vendor):
    if vendor not in VENDORS:
        raise ValueError("Vulkan smoke requires an explicit nvidia or amd vendor")
    result = []
    for section in re.split(r"(?m)^GPU[0-9]+:\s*$", summary)[1:]:
        fields = dict(re.findall(r"(?m)^\s*(\w+)\s*=\s*(.*?)\s*$", section))
        try:
            matches = (int(fields["vendorID"], 0) == VENDORS[vendor]
                       and fields["deviceType"] in DEVICE_TYPES)
        except (KeyError, ValueError):
            raise ValueError("Vulkan device inspection is missing vendor or device type") from None
        if not matches:
            raise ValueError(f"Vulkan exposed a software or wrong-vendor device for {vendor}")
        if any(not fields.get(key) for key in ("deviceName", "apiVersion", "driverVersion")):
            raise ValueError("Vulkan device inspection is missing device or driver identity")
        fields["vendorID"] = hex(VENDORS[vendor])
        result.append({key: fields[key] for key in
                       ("vendorID", "deviceType", "deviceName", "apiVersion", "driverVersion")})
    if not result:
        raise ValueError(f"Vulkan did not discover a real {vendor} GPU with driver identity")
    return result


def preflight(vendor, output):
    output.mkdir(parents=True, exist_ok=True)
    summary = capture(["vulkaninfo", "--summary"])
    (output / "vulkaninfo.txt").write_text(summary, encoding="utf-8")
    if not os.environ.get("VK_DRIVER_FILES"):
        raise ValueError("Vulkan smoke requires explicit hardware ICD selection")
    return devices(summary, vendor)


def toolchain(version):
    if os.environ.get("SKIPPY_VULKAN_SDK_VERSION") != version:
        raise ValueError(f"expected Vulkan SDK {version}")
    header = Path(os.environ["VULKAN_SDK"]) / "include/vulkan/vulkan_core.h"
    patch = re.search(r"(?m)^#define VK_HEADER_VERSION\s+([0-9]+)\s*$", header.read_text())
    if patch is None or patch.group(1) != version.split(".")[2]:
        raise ValueError("Vulkan SDK headers differ from the pinned version")
    return {"sdk_version": version, "header_sha256": digest(header),
            "shader_compiler": capture(["glslc", "--version"])}
