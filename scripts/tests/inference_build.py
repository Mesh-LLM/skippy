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
    def test_policy_records_actual_release_configuration_and_rejects_unoptimized_override(self):
        policy = BUILD["build_policy"]
        with TemporaryDirectory() as temporary, chdir(temporary):
            Path("Cargo.toml").write_text('[profile.release]\nlto = "thin"\ncodegen-units = 1\n')
            with patch.dict(policy.__globals__, capture=lambda _: "a" * 40), \
                    patch.dict(os.environ, {"CARGO_PROFILE_RELEASE_OPT_LEVEL": "3"}):
                observed = policy()
                self.assertEqual(observed["cargo_profile"], "release")
                self.assertEqual(observed["rust_release"], {"lto": "thin", "codegen-units": 1})
                self.assertEqual(observed["release_overrides"]["CARGO_PROFILE_RELEASE_OPT_LEVEL"], "3")
                os.environ["CARGO_PROFILE_RELEASE_OPT_LEVEL"] = "0"
                with self.assertRaisesRegex(ValueError, "opt-level 3"):
                    policy()

    def test_native_transition_isolates_dependencies_but_rust_edits_reuse_them(self):
        old = {"schema": 2, "source": "a" * 40, "native_inputs_sha256": "1" * 64}
        new = dict(old, source="b" * 40, native_inputs_sha256="2" * 64)
        rust_edit = dict(new, source="c" * 40)
        self.assertNotEqual(BUILD["dependency_key"](old), BUILD["dependency_key"](new))
        self.assertEqual(BUILD["dependency_key"](new), BUILD["dependency_key"](rust_edit))
        self.assertEqual(BUILD["dependency_key"](old), BUILD["dependency_key"](dict(old)))
        self.assertNotEqual(BUILD["cache_key"](new), BUILD["cache_key"](rust_edit))

    def test_release_policy_changes_invalidate_cache_but_commit_only_does_not_discard_dependencies(self):
        inputs = {"source": "a" * 40, "policy": {"cargo_profile": "release", "harness_commit": "a" * 40,
                                                "harness_sha256": "c" * 64}}
        next_commit = {**inputs, "source": "b" * 40,
                       "policy": {**inputs["policy"], "harness_commit": "b" * 40}}
        self.assertEqual(BUILD["dependency_key"](inputs), BUILD["dependency_key"](next_commit))
        self.assertNotEqual(BUILD["cache_key"](inputs), BUILD["cache_key"](next_commit))
        dev = {**inputs, "policy": {**inputs["policy"], "cargo_profile": "dev"}}
        self.assertNotEqual(BUILD["dependency_key"](inputs), BUILD["dependency_key"](dev))

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
        with TemporaryDirectory() as tmp, chdir(tmp), patch.dict(os.environ, {
            "SCCACHE_EXTRAFILES": "/existing/input", "PROVIDER_CONFIGURATION": "retained",
        }), patch.dict(build.__globals__, command=command, install_binaries=lambda _: None,
                       identity=lambda _: {}), \
                patch("shutil.which", return_value="/bin/sccache"), \
                patch("shutil.copy"), patch("pathlib.Path.rename"):
            root = Path(tmp) / "inference-build-v3"
            root.mkdir()
            build(inputs["source"], root, inputs, "key", root / "key")
        args, compiler = next((args, env) for args, env in calls if args[-1] == "inference-build-uncached")
        self.assertEqual(args[1:3], ["--justfile", str(BUILD["HARNESS_ROOT"] / "Justfile")])
        self.assertIn("cargo-v3", compiler["CARGO_TARGET_DIR"])
        self.assertNotIn("CARGO_PROFILE_DEV_DEBUG", compiler)
        self.assertEqual(compiler["SCCACHE_EXTRAFILES"].split(os.pathsep)[0], "/existing/input")
        self.assertTrue(compiler["SCCACHE_EXTRAFILES"].endswith("target/inference-native-inputs.json"))
        self.assertNotIn("SCCACHE_RECACHE", compiler)


if __name__ == "__main__":
    unittest.main()
