"""Offline Vulkan device admission and exact-archive release evidence contracts."""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import ci_vulkan
from lib.ci_targets import validate_native_identity
from lib.native_policy import forbidden, host_owned

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = """GPU0:
    apiVersion = 1.3.280
    driverVersion = 24.0.9
    vendorID = 0x1002
    deviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
    deviceName = AMD Radeon Graphics
"""


class VulkanDevices(unittest.TestCase):
    def test_real_gpu_records_vendor_api_and_driver(self):
        device, = ci_vulkan.devices(SUMMARY, "amd")
        self.assertEqual(device["deviceName"], "AMD Radeon Graphics")
        self.assertEqual(device["vendorID"], "0x1002")
        self.assertEqual(device["driverVersion"], "24.0.9")
        nvidia = SUMMARY.replace("0x1002", "0x10DE").replace("INTEGRATED", "DISCRETE")
        self.assertEqual(ci_vulkan.devices(nvidia, "nvidia")[0]["vendorID"], "0x10de")

    def test_software_wrong_vendor_and_missing_driver_cannot_qualify(self):
        for summary in ("", SUMMARY.replace("INTEGRATED_GPU", "CPU"),
                        SUMMARY.replace("0x1002", "0x10de"),
                        SUMMARY.replace("driverVersion = 24.0.9", ""),
                        SUMMARY + SUMMARY.replace("GPU0", "GPU1").replace("INTEGRATED_GPU", "CPU")):
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                ci_vulkan.devices(summary, "amd")
        with self.assertRaises(ValueError):
            ci_vulkan.devices(SUMMARY, "auto")

    def test_loader_is_a_host_requirement_but_cli_must_stay_backend_neutral(self):
        self.assertTrue(host_owned("libvulkan.so.1", "elf"))
        self.assertTrue(host_owned("vulkan-1.dll", "pe"))
        self.assertFalse(host_owned("libvulkan_radeon.so", "elf"))
        self.assertTrue(forbidden("libvulkan.so.1"))

    def test_preflight_requires_hardware_icd_selection_and_keeps_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with (patch.object(ci_vulkan, "capture", return_value=SUMMARY),
                  patch.dict(os.environ, VK_DRIVER_FILES="")):
                with self.assertRaisesRegex(ValueError, "hardware ICD"):
                    ci_vulkan.preflight("amd", output)
                self.assertEqual((output / "vulkaninfo.txt").read_text(), SUMMARY)
            with (patch.object(ci_vulkan, "capture", return_value=SUMMARY),
                  patch.dict(os.environ, VK_DRIVER_FILES="/usr/share/vulkan/icd.d/radeon_icd.x86_64.json")):
                self.assertEqual(len(ci_vulkan.preflight("amd", output)), 1)

    def test_build_checks_sdk_headers_and_packaging_preserves_the_toolkit_pin(self):
        version = "1.4.328.1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            include = root / "include/vulkan"
            include.mkdir(parents=True)
            header = include / "vulkan_core.h"
            header.write_text("#define VK_HEADER_VERSION 328\n")
            with (patch.dict(os.environ, VULKAN_SDK=directory, SKIPPY_VULKAN_SDK_VERSION=version),
                  patch.object(ci_vulkan, "capture", return_value="shaderc compiler")):
                identity = ci_vulkan.toolchain(version)
                self.assertEqual(identity["sdk_version"], version)
                header.write_text("#define VK_HEADER_VERSION 329\n")
                with self.assertRaisesRegex(ValueError, "headers differ"):
                    ci_vulkan.toolchain(version)
                with self.assertRaisesRegex(ValueError, "expected Vulkan SDK"):
                    ci_vulkan.toolchain("1.4.329.0")
            host = {"os": "linux", "arch": "x86_64"}
            runtime = {"backend": "vulkan", "toolkit": version}
            (root / "manifest.json").write_text(json.dumps({"runtime": {
                "platform": host, "backend": {"kind": "vulkan"}}}))
            (root / "vulkan-toolchain.json").write_text(json.dumps(identity))
            validate_native_identity(root, host, runtime)
            with self.assertRaisesRegex(ValueError, "toolkit mismatch"):
                validate_native_identity(root, host, {**runtime, "toolkit": "1.4.329.0"})

    def test_full_only_policy_and_sdk_pin_are_enforced_even_in_pr_plans(self):
        original = json.loads((ROOT / "ci/targets.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "ci").mkdir()
            shutil.copy(ROOT / "scripts/ci-plan.sh", root / "scripts")
            (root / "Justfile").write_text("ci-plan:\n    @bash scripts/ci-plan.sh\n")
            for profile in ("pr", "full"):
                for field, value in (("full_only", False), ("toolkit", "latest"),
                                     ("validation", "artifact"), ("gpu_architectures", "121")):
                    manifest = json.loads(json.dumps(original))
                    row = next(row for row in manifest["runtimes"] if row["id"] == "linux-x86_64-vulkan")
                    row[field] = value
                    (root / "ci/targets.json").write_text(json.dumps(manifest))
                    result = subprocess.run(["just", "ci-plan"], cwd=root,
                        env={**os.environ, "SKIPPY_CI_PROFILE": profile, "GITHUB_OUTPUT": ""},
                        capture_output=True, text=True)
                    with self.subTest(profile=profile, field=field):
                        self.assertNotEqual(result.returncode, 0)


class VulkanRelease(unittest.TestCase):
    def test_each_vendor_requires_real_device_evidence_for_the_exact_archive(self):
        spec = importlib.util.spec_from_file_location("ci_release", ROOT / "scripts/ci-release.py")
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        variant, source, digest = "linux-x86_64-vulkan", "a" * 40, "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for vendor, vendor_id in (("amd", "0x1002"), ("nvidia", "0x10DE")):
                with self.assertRaises(FileNotFoundError):
                    release.verify_smoke(root, variant, source, "vulkan", digest, vendor=vendor)
                output = root / f"release-device-evidence-{variant}-{vendor}"
                output.mkdir()
                report = {"source": source, "variant": variant, "backend": "vulkan",
                          "archive_sha256": digest, "vulkan_vendor": vendor,
                          "vulkan_devices": ci_vulkan.devices(SUMMARY.replace("0x1002", vendor_id), vendor)}
                release.artifacts.write_json(output / "smoke.json", report)
                release.verify_smoke(root, variant, source, "vulkan", digest, vendor=vendor)
                with self.assertRaisesRegex(ValueError, "archive"):
                    release.verify_smoke(root, variant, source, "vulkan", "c" * 64, vendor=vendor)
                report["vulkan_devices"][0]["deviceType"] = "PHYSICAL_DEVICE_TYPE_CPU"
                release.artifacts.write_json(output / "smoke.json", report)
                with self.assertRaisesRegex(ValueError, "real .* Vulkan"):
                    release.verify_smoke(root, variant, source, "vulkan", digest, vendor=vendor)


if __name__ == "__main__":
    unittest.main()
