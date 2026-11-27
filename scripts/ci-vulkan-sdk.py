#!/usr/bin/env python3
"""Install the checksum-pinned LunarG SDK in a job-local directory."""

import os
import platform
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

from lib.ci_artifacts import digest
from lib.ci_process import command

VERSION = "1.4.328.1"
# Published by LunarG's /sdk/sha/<version>/<platform>/<file>.json endpoint.
PACKAGES = {
    "Linux": ("linux", f"vulkansdk-linux-x86_64-{VERSION}.tar.xz",
              "241e75b56c91c0d210ed07a7c638ec05a3e5b0e4c66ba9f0ba0f102d823ad6bf"),
    "Windows": ("windows", f"vulkansdk-windows-X64-{VERSION}.exe",
                "a8675df6d538079c2a719a9373994948091db785b48f142e024254e76348d16c"),
}


def install(version):
    system = platform.system()
    if version != VERSION or system not in PACKAGES or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("Vulkan CI requires the pinned SDK on Linux or Windows x64")
    operating_system, filename, expected = PACKAGES[system]
    with tempfile.TemporaryDirectory(prefix="skippy-vulkan-download-", dir=os.environ["RUNNER_TEMP"]) as temporary:
        archive = Path(temporary) / filename
        url = f"https://sdk.lunarg.com/sdk/download/{VERSION}/{operating_system}/{filename}"
        print(f"Downloading {url}", flush=True)
        command(["curl", "--fail", "--location", "--retry", "3", "--output", str(archive), url], timeout=600)
        if digest(archive) != expected:
            raise ValueError("Vulkan SDK download SHA-256 mismatch")
        root = Path(os.environ["RUNNER_TEMP"]) / "skippy-vulkan-sdk"
        if root.exists():
            raise ValueError("Vulkan SDK installation directory must be fresh")
        root.mkdir()
        if system == "Linux":
            with tarfile.open(archive) as package:
                package.extractall(root, filter="data")
            sdk = root / VERSION / "x86_64"
            binary = sdk / "bin"
        else:
            sdk = root / VERSION
            command([str(archive), "--root", str(sdk), "--accept-licenses", "--default-answer",
                     "--confirm-command", "install"], timeout=1800, terminate_descendants=True)
            binary = sdk / "Bin"
    compiler = binary / ("glslc.exe" if system == "Windows" else "glslc")
    if not compiler.is_file() or not (sdk / "include/vulkan/vulkan_core.h").is_file():
        raise ValueError("Vulkan SDK installation is missing the shader compiler or headers")
    command([str(compiler), "--version"], timeout=60)
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as output:
        output.write(f"VULKAN_SDK={sdk}\nSKIPPY_VULKAN_SDK_VERSION={VERSION}\n")
        if system == "Linux":
            output.write(f'LD_LIBRARY_PATH={sdk / "lib"}:{os.environ.get("LD_LIBRARY_PATH", "")}\n')
    with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as output:
        output.write(f"{binary}\n")


if __name__ == "__main__":
    install(sys.argv[1])
