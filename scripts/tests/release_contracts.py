"""Offline checks for release versioning and Git ref promotion."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from lib.release_version import bump, parse_tag


SCRIPTS = Path(__file__).resolve().parents[1]
TAG = "v0.81.0-rc.1"


def release_script():
    spec = importlib.util.spec_from_file_location("ci_release", SCRIPTS / "ci-release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeCatalogContract(unittest.TestCase):
    """The catalog a host downloads names each release archive exactly once."""

    def setUp(self):
        self.release = release_script()
        self.archives = {f"skippy-{TAG}-{variant}.tar.gz": digit * 64
                         for variant, digit in (("linux-x86_64-cpu", "a"), ("darwin-aarch64-metal", "b"))}
        self.value = self.release.catalog(TAG, [
            self.release.catalog_entry(
                {"id": f"skippy-native-runtime-{name}", "release_version": TAG[1:],
                 "skippy_abi": "0.1.86", "url": None, "sha256": None},
                TAG, name, sha256)
            for name, sha256 in self.archives.items()
        ])

    def test_catalog_points_each_runtime_at_its_release_archive(self):
        self.release.verify_catalog(self.value, TAG, self.archives)
        self.assertEqual(self.value["release_version"], TAG[1:])
        first = self.value["artifacts"][0]
        self.assertEqual(first["url"], "https://github.com/general-inference/skippy/releases/"
                         f"download/{TAG}/skippy-{TAG}-linux-x86_64-cpu.tar.gz")
        self.assertEqual(first["sha256"], "a" * 64)

    def test_installer_and_release_agree_on_the_catalog_location(self):
        installer = (SCRIPTS.parent / "crates/skippy-runtime-install/src/types.rs").read_text()
        self.assertIn(f'releases_url: "{self.release.RELEASES_URL}"', installer)
        self.assertIn(f"/download/{{tag}}/{self.release.CATALOG}", installer)

    def test_catalog_rejects_a_runtime_from_another_release_or_abi(self):
        stale = {"id": "old", "release_version": "0.76.1", "skippy_abi": "0.1.86"}
        with self.assertRaisesRegex(ValueError, "not built for release"):
            self.release.catalog_entry(stale, TAG, "skippy.tar.gz", "a" * 64)
        mixed = [dict(entry) for entry in self.value["artifacts"]]
        mixed[1]["skippy_abi"] = "0.1.87"
        with self.assertRaisesRegex(ValueError, "disagree on the Skippy ABI"):
            self.release.catalog(TAG, mixed)

    def test_verification_rejects_a_catalog_that_differs_from_the_archives(self):
        def changed(edit):
            value = {**self.value, "artifacts": [dict(entry) for entry in self.value["artifacts"]]}
            edit(value)
            return value

        cases = (
            lambda value: value["artifacts"][0].update(sha256="c" * 64),
            lambda value: value["artifacts"].pop(),
            lambda value: value["artifacts"][1].update(url=value["artifacts"][0]["url"]),
            lambda value: value["artifacts"][0].update(url="https://example.invalid/runtime.tar.gz"),
            lambda value: value.update(release_version="0.80.0"),
        )
        for edit in cases:
            with self.assertRaises(ValueError):
                self.release.verify_catalog(changed(edit), TAG, self.archives)


class ReleaseContract(unittest.TestCase):
    def run_command(self, *args, check=True):
        return subprocess.run(args, cwd=self.root, env=self.env, check=check,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def git(self, *args):
        return self.run_command("git", *args).stdout.strip()

    def release(self, *args, check=True):
        return self.run_command(sys.executable, str(SCRIPTS / "ci-release-candidate.py"),
                                *args, check=check)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name)
        self.root = parent / "work"
        self.root.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.pop("GH_TOKEN", None)
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.env["GITHUB_OUTPUT"] = str(parent / "outputs")
        self.env["GITHUB_RUN_ID"] = "123"
        self.git("init", "--initial-branch=main")
        self.git("init", "--bare", str(parent / "remote"))
        self.git("remote", "add", "origin", str(parent / "remote"))
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@skippy.local")
        (self.root / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/*"]\n\n[workspace.package]\nversion = "0.80.0"\n')
        for package in ("skippy-a", "skippy-b"):
            directory = self.root / "crates" / package
            directory.mkdir(parents=True)
            content = f'[package]\nname = "{package}"\nversion.workspace = true\n'
            if package == "skippy-a":
                content += '\n[dependencies]\npeer = { package = "skippy-b", path = "../skippy-b", version = "0.80.0" }\n'
            (directory / "Cargo.toml").write_text(content)
        (self.root / "Cargo.lock").write_text(
            'version = 4\n\n[[package]]\nname = "skippy-a"\nversion = "0.80.0"\n'
            'dependencies = ["skippy-b 0.80.0"]\n\n[[package]]\nname = "skippy-b"\n'
            'version = "0.80.0"\n\n[[package]]\nname = "registry"\nversion = "0.80.0"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n')
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.env["GITHUB_SHA"] = self.base
        self.git("push", "origin", "main")

    def stage(self):
        self.release("stage", TAG)
        return self.git("rev-parse", "HEAD")

    def refs(self, *refs):
        rows = self.git("ls-remote", "--refs", "origin", *refs).splitlines()
        return {ref: sha for sha, ref in (row.split("\t") for row in rows)}

    def test_version_update_preserves_external_lockfile_packages(self):
        bump(TAG, self.root)
        self.assertTrue(parse_tag(TAG))
        with self.assertRaisesRegex(ValueError, "canonical SemVer"):
            parse_tag("v0.81.0-rc.01")
        manifest = tomllib.loads((self.root / "crates/skippy-a/Cargo.toml").read_text())
        self.assertEqual(manifest["dependencies"]["peer"]["version"], TAG[1:])
        packages = tomllib.loads((self.root / "Cargo.lock").read_text())["package"]
        self.assertEqual([entry["version"] for entry in packages], [TAG[1:], TAG[1:], "0.80.0"])
        self.assertEqual(packages[0]["dependencies"], [f"skippy-b {TAG[1:]}"])

    def test_stage_retry_and_atomic_promotion(self):
        candidate = self.stage()
        tag_ref = f"refs/tags/{TAG}"
        candidate_ref = "refs/heads/release-candidates/123"
        self.assertEqual(self.refs("refs/heads/main", tag_ref, candidate_ref),
                         {"refs/heads/main": self.base, candidate_ref: candidate})
        self.git("checkout", "--detach", self.base)
        self.assertEqual(self.stage(), candidate)
        self.release("promote", TAG, self.base, candidate)
        self.release("promote", TAG, self.base, candidate)
        self.release("cleanup", candidate)
        self.assertEqual(self.refs("refs/heads/main", tag_ref, candidate_ref),
                         {"refs/heads/main": candidate, tag_ref: candidate})

    def test_promotion_rejects_moved_main_and_occupied_tag(self):
        candidate = self.stage()
        tag_ref = f"refs/tags/{TAG}"
        self.git("push", "origin", f"{self.base}:{tag_ref}")
        result = self.release("promote", TAG, self.base, candidate, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.refs("refs/heads/main", tag_ref),
                         {"refs/heads/main": self.base, tag_ref: self.base})
        self.git("push", "origin", f":{tag_ref}")
        self.git("checkout", "--detach", self.base)
        self.git("commit", "--allow-empty", "-m", "concurrent change")
        advanced = self.git("rev-parse", "HEAD")
        self.git("push", "origin", "HEAD:refs/heads/main")
        self.git("checkout", "--detach", candidate)
        result = self.release("promote", TAG, self.base, candidate, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.refs("refs/heads/main", tag_ref), {"refs/heads/main": advanced})

    def test_remote_tag_rejection_also_rejects_the_main_update(self):
        candidate = self.stage()
        hook = self.root.parent / "remote/hooks/update"
        hook.write_text('#!/bin/sh\ncase "$1" in refs/tags/*) exit 1 ;; esac\n')
        hook.chmod(0o755)
        result = self.release("promote", TAG, self.base, candidate, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("atomic push failure", result.stderr)
        self.assertEqual(self.refs("refs/heads/main", f"refs/tags/{TAG}"),
                         {"refs/heads/main": self.base})


if __name__ == "__main__":
    unittest.main()
