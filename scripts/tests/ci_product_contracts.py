"""Offline contracts for passing verified runtime directories into product archives."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import ci_artifacts, ci_smoke


SCRIPTS = Path(__file__).resolve().parents[1]
SOURCE = "a" * 40
HOST = {"id": "linux-x86_64", "target": "x86_64-unknown-linux-gnu",
        "os": "linux", "arch": "x86_64"}
RUNTIME = {"id": "linux-x86_64-cpu", "backend": "cpu", "gpu_architectures": ""}


class ProductContract(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("ci_product", SCRIPTS / "ci-product.py")
        self.product = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.product)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        self.enterContext(patch.object(ci_artifacts, "source", return_value=SOURCE))
        self.enterContext(patch.object(self.product, "require_host"))
        self.command = self.enterContext(patch.object(self.product, "command", side_effect=self.native_build))
        host = self.root / "host"
        host.mkdir()
        (host / "skippy").write_bytes(b"CLI")
        ci_artifacts.receipt(host, "host", HOST["target"], "")
        self.host_input = self.root / "producer-host"
        ci_artifacts.archive(host, self.host_input / "host.tar.gz")
        self.runtime_input = Path("dist/runtimes") / RUNTIME["id"]

    def native_build(self, args, **kwargs):
        if args[:2] != ["just", "release-runtime-build"]:
            return
        native = Path("dist/native-runtimes/runtime")
        native.mkdir(parents=True)
        (native / "libskippy.so").write_bytes(b"native runtime")
        ci_artifacts.write_json(native / "manifest.json", {
            "runtime": {"platform": {"os": "linux", "arch": "x86_64"},
                        "backend": {"kind": "cpu"}},
        })
        if kwargs.get("overrides", {}).get("SKIPPY_NATIVE_RUNTIME_ARCHIVE", "1") != "0":
            ci_artifacts.archive(native, native.with_suffix(".tar.gz"))

    def test_runtime_passes_directly_into_a_verifiable_final_archive(self):
        self.product.build_runtime(HOST, RUNTIME)
        ci_artifacts.verify(self.runtime_input, "runtime", HOST["target"], RUNTIME["id"])
        self.assertFalse(list(Path("dist").rglob("*.tar.gz")))
        self.product.package(HOST, RUNTIME, self.host_input, self.runtime_input)
        # Packaging must not execute an unarchived CLI. Startup belongs to smoke.
        self.assertFalse(any(call.args[0][-1:] == ["--version"]
                             for call in self.command.call_args_list))
        product_input = Path("dist/products") / RUNTIME["id"]
        unpacked = self.root / "unpacked"
        ci_artifacts.unpack(product_input, unpacked)
        ci_artifacts.verify(unpacked, "product", HOST["target"], RUNTIME["id"])
        self.assertEqual((unpacked / "skippy").read_bytes(), b"CLI")
        self.assertEqual((unpacked / "native-runtimes" / RUNTIME["id"] / "libskippy.so").read_bytes(),
                         b"native runtime")
        with patch.object(ci_smoke, "require_host"), patch.object(ci_smoke, "command") as smoke:
            ci_smoke.artifact_smoke(HOST, RUNTIME, product_input)
        self.assertEqual(smoke.call_args_list[0].args[0][-1], "--version")
        report = ci_artifacts.read_json(product_input / "evidence/artifact-smoke.json")
        archive = product_input / f"skippy-{RUNTIME['id']}.tar.gz"
        self.assertEqual(report["archive_sha256"], ci_artifacts.digest(archive))

    def test_modified_runtime_directory_cannot_be_packaged(self):
        self.product.build_runtime(HOST, RUNTIME)
        (self.runtime_input / "libskippy.so").write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.product.package(HOST, RUNTIME, self.host_input, self.runtime_input)
        self.assertFalse(Path("dist/products").exists())

    def test_runtime_receipt_from_another_source_cannot_be_packaged(self):
        self.product.build_runtime(HOST, RUNTIME)
        receipt = self.runtime_input / "provenance.json"
        value = ci_artifacts.read_json(receipt)
        value["source"] = "b" * 40
        ci_artifacts.write_json(receipt, value)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.product.package(HOST, RUNTIME, self.host_input, self.runtime_input)


class RocmLicenseContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "SDK with spaces"
        self.stage = Path(temporary.name) / "stage"
        self.licenses = {}
        for component, name in zip(("rocblas", "hipblaslt", "hipblas-common", "hipblas"),
                                   ("LICENSE.md", "LICENSE.txt", "LICENSE", "LICENSE.md")):
            path = self.root / "share/doc" / component / name
            path.parent.mkdir(parents=True)
            path.write_bytes(component.encode())
            self.licenses[component] = path

    def collect(self, override=""):
        return subprocess.run(["bash", str(SCRIPTS / "rocm-distribution-licenses.sh"),
                               str(self.stage), str(self.root), str(override)],
                              capture_output=True, text=True)

    def test_complete_sdk_copies_every_component_license(self):
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = result.stdout.splitlines()
        self.assertEqual(len(paths), len(self.licenses))
        self.assertEqual({(self.stage / path).read_bytes() for path in paths},
                         {component.encode() for component in self.licenses})

    def test_each_missing_component_fails_before_copying_any_licenses(self):
        for component, path in self.licenses.items():
            with self.subTest(component=component):
                path.unlink()
                result = self.collect()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"license for {component}", result.stderr)
                self.assertFalse(self.stage.exists())
                path.write_bytes(component.encode())

    def test_explicit_combined_license_does_not_require_sdk_discovery(self):
        for path in self.licenses.values():
            path.unlink()
        combined = self.root / "combined-license.txt"
        combined.write_bytes(b"Combined AMD redistribution terms")
        result = self.collect(combined)
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = result.stdout.splitlines()
        self.assertEqual(len(paths), 1)
        self.assertEqual((self.stage / paths[0]).read_bytes(), combined.read_bytes())

    def test_missing_explicit_license_does_not_fall_back_to_sdk(self):
        result = self.collect(self.root / "missing-license.txt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configured ROCm license file does not exist", result.stderr)
        self.assertFalse(self.stage.exists())


if __name__ == "__main__":
    unittest.main()
