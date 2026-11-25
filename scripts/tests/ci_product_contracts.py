"""Offline contracts for passing verified runtime directories into product archives."""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib import ci_artifacts, ci_smoke, ci_targets


SCRIPTS = Path(__file__).resolve().parents[1]
SOURCE = "a" * 40
HOST = {"id": "linux-x86_64", "target": "x86_64-unknown-linux-gnu",
        "os": "linux", "arch": "x86_64"}
RUNTIME = {"id": "linux-x86_64-cpu", "backend": "cpu", "gpu_architectures": ""}


class DeviceSmokeContract(unittest.TestCase):
    def assert_device(self, backend, device, *, offload=True, passes=True):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "server.log"
            selected = {"type": "backend_device_selected", "data": {"detail": device}}
            log.write_text(json.dumps(selected) + "\n" +
                           ("offloaded 29/29 layers to GPU\n" if offload else ""))
            server = SimpleNamespace(poll=lambda: None)
            with (patch.object(ci_smoke.time, "sleep"),
                  patch.object(ci_smoke.time, "monotonic", side_effect=[0.0, 2.0])):
                if passes:
                    ci_smoke.require_device([log], server, 1.0, backend)
                else:
                    with self.assertRaisesRegex(RuntimeError, "GPU device and positive layer offload"):
                        ci_smoke.require_device([log], server, 1.0, backend)

    def test_each_gpu_backend_requires_its_own_device_and_positive_offload(self):
        for backend, device in (("rocm", "ROCm0: AMD Radeon 8060S"), ("rocm", "HIP0"),
                                ("cuda", "CUDA0"), ("metal", "MTL0")):
            with self.subTest(backend=backend, device=device):
                self.assert_device(backend, device)
                self.assert_device(backend, device, offload=False, passes=False)

    def test_rocm_rejects_cpu_fallback_and_other_gpu_backends(self):
        for device in ("CPU", "CUDA0", "MTL0", "Vulkan0"):
            with self.subTest(device=device):
                self.assert_device("rocm", device, passes=False)


class TargetPlannerContract(unittest.TestCase):
    def test_windows_launches_bash_by_its_resolved_path(self):
        bash = r"C:\Program Files\Git\usr\bin\bash.exe"
        plan = '{"hosts":{"include":[]},"runtimes":{"include":[]}}'
        with (patch.object(ci_targets.platform, "system", return_value="Windows"),
              patch.object(ci_targets.shutil, "which", return_value=bash),
              patch.object(ci_targets, "capture", return_value=plan) as capture):
            self.assertEqual(ci_targets.targets(), ({}, {}))
        capture.assert_called_once_with([bash, "scripts/ci-plan.sh"])


class WindowsCudaCacheContract(unittest.TestCase):
    def test_release_bump_preserves_cache_and_native_changes_invalidate_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            for name in ("artifact-file.sh", "ci-plan.sh", "ci-windows-cuda-cache.sh"):
                shutil.copyfile(SCRIPTS / name, root / "scripts" / name)
            (root / "ci").mkdir()
            matrix = root / "ci/targets.json"
            shutil.copyfile(SCRIPTS.parent / "ci/targets.json", matrix)
            (root / "llama_cpp").mkdir()
            pin = root / "llama_cpp/upstream.txt"
            pin.write_text("first llama pin")
            lockfile = root / "Cargo.lock"
            lockfile.write_text("first release version")
            nvcc = root / "nvcc.exe"
            nvcc.write_bytes(b"CUDA compiler")
            cl = root / "cl.exe"
            cl.write_bytes(b"MSVC compiler")
            cl.chmod(0o755)

            def git(*args):
                subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com",
                                *args], cwd=root, check=True, stdout=subprocess.DEVNULL)

            def commit():
                git("add", ".")
                git("commit", "-qm", "Fixture inputs")

            def namespace(**overrides):
                environment = {**os.environ, "SKIPPY_CI_PROFILE": "full",
                               "WindowsSDKVersion": "10.0.26100.0", "VCToolsVersion": "14.44.35207",
                               "CUDACXX": str(nvcc),
                               "CC": str(cl), "CXX": str(cl), **overrides}
                return subprocess.check_output(
                    ["bash", "scripts/ci-windows-cuda-cache.sh", "windows-x86_64-cuda13", "13.1.2"],
                    cwd=root, env=environment, text=True,
                ).strip()

            git("init", "-q")
            commit()
            initial = namespace(SKIPPY_CUDA_ARCHITECTURE_MODE="native")
            self.assertRegex(initial, r"^skippy-native-windows-cuda-v1-[0-9a-f]{64}$")
            self.assertEqual(initial, namespace(SKIPPY_CUDA_ARCHITECTURE_MODE="release"))

            lockfile.write_text("next release version")
            commit()
            self.assertEqual(initial, namespace())

            plan = json.loads(matrix.read_text())
            row = next(row for row in plan["runtimes"] if row["id"] == "windows-x86_64-cuda13")
            row["gpu_architectures"] += ";121"
            matrix.write_text(json.dumps(plan))
            commit()
            changed_arches = namespace()
            self.assertNotEqual(initial, changed_arches)
            self.assertNotEqual(changed_arches, namespace(WindowsSDKVersion="10.0.26200.0"))
            self.assertNotEqual(changed_arches, namespace(VCToolsVersion="14.45.00000"))

            nvcc.write_bytes(b"Updated CUDA compiler")
            self.assertNotEqual(changed_arches, namespace())
            nvcc.write_bytes(b"CUDA compiler")
            pin.write_text("next llama pin")
            commit()
            self.assertNotEqual(changed_arches, namespace())


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
