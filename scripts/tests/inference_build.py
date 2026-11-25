"""Offline regression tests for native recipe transitions in JY build caches."""

import os
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import patch


BUILD = run_path(str(Path(__file__).resolve().parents[1] / "inference-build.py"))


class NativeCacheTests(unittest.TestCase):
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
        inputs = {"schema": 2, "source": "a" * 40, "native_inputs_sha256": "1" * 64}
        def command(args, overrides, timeout):
            calls.append((args, overrides))
            # The command helper merges overrides into the complete inherited environment.
            self.assertEqual(os.environ["PROVIDER_CONFIGURATION"], "retained")
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "SCCACHE_EXTRAFILES": "/existing/input", "PROVIDER_CONFIGURATION": "retained",
        }), patch.dict(build.__globals__, command=command, install_binaries=lambda _: None,
                       identity=lambda _: {}, write_json=lambda *args: None), \
                patch("shutil.which", return_value="/bin/sccache"), \
                patch("shutil.copy"), patch("pathlib.Path.rename"):
            root = Path(tmp) / "inference-build-v2"
            root.mkdir()
            build(inputs["source"], root, inputs, "key", root / "key")
        compiler = next(env for args, env in calls if args == ["just", "inference-build-uncached"])
        self.assertIn("cargo-v2", compiler["CARGO_TARGET_DIR"])
        self.assertEqual(compiler["SCCACHE_EXTRAFILES"].split(os.pathsep)[0], "/existing/input")
        self.assertTrue(compiler["SCCACHE_EXTRAFILES"].endswith("target/inference-native-inputs.json"))
        self.assertNotIn("SCCACHE_RECACHE", compiler)


if __name__ == "__main__":
    unittest.main()
