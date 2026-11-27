"""Offline regression tests for native recipe transitions in JY build caches."""

import os
import unittest
from contextlib import chdir
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import patch


BUILD = run_path(str(Path(__file__).resolve().parents[1] / "inference-build.py"))


class NativeCacheTests(unittest.TestCase):
    def test_backend_inputs_preserve_metal_sdk_without_probing_cuda(self):
        source_inputs = BUILD["source_inputs"]
        calls = []
        def capture(args):
            calls.append(args)
            return "26.1\n" if args[0] == "xcrun" else "tool-version\n"
        with patch.dict(os.environ, {"SCCACHE_EXTRAFILES": ""}), patch.dict(
            source_inputs.__globals__, capture=capture,
            native_inputs=lambda: "1" * 64, digest=lambda _: "2" * 64,
        ):
            metal = source_inputs("a" * 40, "metal")
        self.assertEqual(metal["backend"], "metal")
        self.assertEqual(metal["macos_sdk"], "26.1")
        self.assertNotIn("cuda", metal)
        self.assertFalse(any(args[0] == "nvcc" for args in calls))

    def test_backend_inputs_preserve_cuda_architecture_and_separate_cache_keys(self):
        source_inputs = BUILD["source_inputs"]
        with patch.dict(os.environ, {"SCCACHE_EXTRAFILES": ""}), patch.dict(
            source_inputs.__globals__, capture=lambda _: "tool-version\n",
            native_inputs=lambda: "1" * 64, digest=lambda _: "2" * 64,
        ):
            cuda = source_inputs("a" * 40, "cuda")
            metal = source_inputs("a" * 40, "metal")
        self.assertEqual(cuda["architectures"], "121")
        self.assertIn("cuda", cuda)
        self.assertNotIn("macos_sdk", cuda)
        self.assertNotEqual(BUILD["dependency_key"](cuda), BUILD["dependency_key"](metal))

    def test_native_transition_isolates_dependencies_but_rust_edits_reuse_them(self):
        old = {"schema": 2, "source": "a" * 40, "native_inputs_sha256": "1" * 64}
        new = dict(old, source="b" * 40, native_inputs_sha256="2" * 64)
        rust_edit = dict(new, source="c" * 40)
        self.assertNotEqual(BUILD["dependency_key"](old), BUILD["dependency_key"](new))
        self.assertEqual(BUILD["dependency_key"](new), BUILD["dependency_key"](rust_edit))
        self.assertEqual(BUILD["dependency_key"](old), BUILD["dependency_key"](dict(old)))
        self.assertNotEqual(BUILD["cache_key"](new), BUILD["cache_key"](rust_edit))

    def test_patch_removal_and_native_build_changes_invalidate_identity(self):
        native_inputs = BUILD["native_inputs"]
        with patch.dict(native_inputs.__globals__, capture=lambda args: "100644 blob old patch\n"):
            old = native_inputs()
        with patch.dict(native_inputs.__globals__, capture=lambda args: "100644 blob new patch\n"):
            self.assertNotEqual(old, native_inputs())
        with patch.dict(native_inputs.__globals__, capture=lambda args: ""):
            self.assertNotEqual(old, native_inputs())

    def test_build_hashes_native_identity_in_compiler_cache_and_retains_extra_files(self):
        build = BUILD["build"]
        calls = []
        inputs = {"schema": 2, "source": "a" * 40, "native_inputs_sha256": "1" * 64,
                  "backend": "cuda"}
        def command(args, overrides, timeout):
            calls.append((args, overrides))
            # The command helper merges overrides into the complete inherited environment.
            self.assertEqual(os.environ["PROVIDER_CONFIGURATION"], "retained")
        with TemporaryDirectory() as tmp, chdir(tmp), patch.dict(os.environ, {
            "SCCACHE_EXTRAFILES": "/existing/input", "PROVIDER_CONFIGURATION": "retained",
        }), patch.dict(build.__globals__, command=command, install_binaries=lambda _: None,
                       identity=lambda _sha, _backend: {}), \
                patch("shutil.which", return_value="/bin/sccache"), \
                patch("shutil.copy"), patch("pathlib.Path.rename"):
            root = Path(tmp) / "inference-build-v2"
            root.mkdir()
            build(inputs["source"], root, inputs, "key", root / "key")
        compiler = next(env for args, env in calls if args == ["just", "inference-build-uncached", "cuda"])
        self.assertIn("cargo-v2", compiler["CARGO_TARGET_DIR"])
        self.assertEqual(compiler["SCCACHE_EXTRAFILES"].split(os.pathsep)[0], "/existing/input")
        self.assertTrue(compiler["SCCACHE_EXTRAFILES"].endswith("target/inference-native-inputs.json"))
        self.assertNotIn("SCCACHE_RECACHE", compiler)


if __name__ == "__main__":
    unittest.main()
