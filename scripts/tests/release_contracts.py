"""Offline checks for release versioning and Git ref promotion."""

import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.release_version import bump, parse_tag


SCRIPTS = Path(__file__).resolve().parents[1]
TAG = "v0.81.0-rc.1"


def release_script(name="ci-release"):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CrateReleaseContract(unittest.TestCase):
    def setUp(self):
        self.release = release_script("ci-crates")

    def test_publication_order_requires_versioned_registry_dependencies(self):
        dependency = {"name": "skippy-subprocess", "path": "/subprocess", "req": "^0.81.0", "kind": None}
        api = {"id": "api", "name": "skippy-api", "version": "0.81.0", "publish": ["crates-io"],
               "manifest_path": "/api/Cargo.toml", "dependencies": [dependency]}
        subprocess_crate = {"id": "subprocess", "name": "skippy-subprocess", "version": "0.81.0",
                            "publish": ["crates-io"], "manifest_path": "/subprocess/Cargo.toml", "dependencies": []}
        meta = {"packages": [api, subprocess_crate], "workspace_members": ["api", "subprocess"]}
        self.assertEqual(self.release.publish_order(meta, "0.81.0"), ["skippy-subprocess", "skippy-api"])
        for package, key, value in ((subprocess_crate, "publish", []), (dependency, "req", "^0.80.0")):
            with patch.dict(package, {key: value}), self.assertRaisesRegex(ValueError, "release-versioned"):
                self.release.publish_order(meta, "0.81.0")

    def test_resuming_skips_only_an_unyanked_package_from_the_release_commit(self):
        name, version, commit = "skippy-subprocess", "0.81.0", "a" * 40
        raw = io.BytesIO()
        vcs = json.dumps({"git": {"sha1": commit}}).encode()
        with tarfile.open(fileobj=raw, mode="w:gz") as crate:
            member = tarfile.TarInfo(f"{name}-{version}/.cargo_vcs_info.json")
            member.size = len(vcs)
            crate.addfile(member, io.BytesIO(vcs))
        archive = raw.getvalue()
        entry = {"vers": version, "cksum": hashlib.sha256(archive).hexdigest(), "yanked": False}

        def responses(**overrides):
            row = json.dumps({**entry, **overrides}).encode()
            return patch.object(self.release, "registry_request", side_effect=[io.BytesIO(row), io.BytesIO(archive)])

        with responses():
            self.assertTrue(self.release.published(name, version, commit))
        with responses(vers="0.80.0"):
            self.assertFalse(self.release.published(name, version, commit))
        with responses(), self.assertRaisesRegex(ValueError, "different source"):
            self.release.published(name, version, "b" * 40)
        with responses(yanked=True), self.assertRaisesRegex(ValueError, "yanked"):
            self.release.published(name, version, commit)
        with responses(cksum="bad"), self.assertRaisesRegex(ValueError, "does not match"):
            self.release.published(name, version, commit)

    def test_bootstrap_honors_the_registry_retry_time(self):
        log = ("received a 429 response. Please try again after Wed, 25 Nov 2026 12:00:00 GMT "
               "and see https://crates.io/docs/rate-limits for more details.")
        with patch.object(self.release.time, "time", return_value=1795607991):
            self.assertEqual(self.release.retry_delay(log), 10)
        self.assertIsNone(self.release.retry_delay("401 Unauthorized"))


class WindowsRuntimeArtifactContract(unittest.TestCase):
    @staticmethod
    def pe_image(machine):
        image = bytearray(256)
        image[:2] = b"MZ"
        struct.pack_into("<I", image, 60, 64)
        image[64:68] = b"PE\0\0"
        struct.pack_into("<H", image, 68, machine)
        struct.pack_into("<H", image, 84, 112)
        struct.pack_into("<H", image, 88, 0x20B)
        return bytes(image)

    def test_windows_arm64_rejects_wrong_machine_in_every_declared_binary(self):
        verify = release_script("native-inspect").verify_directory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"lib/llama.dll": self.pe_image(0xAA64),
                     "tools/runner.exe": self.pe_image(0xAA64),
                     "extras/helper.dll": self.pe_image(0xAA64)}
            manifest = {
                "schema_version": 2,
                "runtime": {
                    "id": "skippy-native-runtime-windows-aarch64-cpu",
                    "release_version": "0.80.0",
                    "skippy_abi": "0.1.74",
                    "platform": {"target": "aarch64-pc-windows-msvc", "os": "windows", "arch": "aarch64"},
                    "backend": {"kind": "cpu"},
                    "libraries": ["lib/llama.dll"],
                    "files": {},
                    "tools": {},
                },
                "build": {"llama_upstream_sha": "a" * 40, "llama_patched_sha": "b" * 40,
                          "llama_patch_digest": "c" * 64, "primary_library": "lib/llama.dll"},
            }
            for candidate in (*files, None):
                for name, arm_image in files.items():
                    image = self.pe_image(0x8664) if name == candidate else arm_image
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(image)
                    group = "tools" if name.endswith(".exe") else "files"
                    manifest["runtime"][group][name] = hashlib.sha256(image).hexdigest()
                manifest["build"]["library_sha256"] = manifest["runtime"]["files"]["lib/llama.dll"]
                (root / "manifest.json").write_text(json.dumps(manifest))
                if candidate is None:
                    verify(root, False)
                else:
                    with self.assertRaisesRegex(ValueError, "runtime architecture mismatch"):
                        verify(root, False)
            (root / "lib" / "unlisted.dll").write_bytes(self.pe_image(0xAA64))
            with self.assertRaisesRegex(ValueError, "unhashed runtime library"):
                verify(root, False)


class SmokeEvidenceContract(unittest.TestCase):
    def test_linux_rocm_release_policy_rejects_archive_only_evidence(self):
        release = release_script()
        with patch.dict(os.environ, SKIPPY_CI_PROFILE="full", GITHUB_OUTPUT=""):
            _, runtimes = release.targets()
        runtime = runtimes["linux-x86_64-rocm"]
        self.assertTrue(runtime["full_only"])
        self.assertEqual(runtime["validation"], "inference")
        artifact_only = [row["id"] for row in runtimes.values() if row["validation"] == "artifact"]
        self.assertEqual(len(artifact_only), 4)
        self.assertTrue(all(variant.startswith("windows-") for variant in artifact_only))
        source, digest = "a" * 40, "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "release-product-linux-x86_64-rocm/evidence"
            evidence.mkdir(parents=True)
            report = {"source": source, "variant": runtime["id"], "backend": runtime["backend"],
                      "archive_sha256": digest}
            archive_check = evidence / "artifact-smoke.json"
            release.artifacts.write_json(archive_check, report)
            policy = {"artifact_only": runtime["validation"] == "artifact"}
            with self.assertRaises(FileNotFoundError):
                release.verify_smoke(root, runtime["id"], source, "rocm", digest, **policy)
            archive_check.unlink()
            release.artifacts.write_json(evidence / "smoke.json", report)
            release.verify_smoke(root, runtime["id"], source, "rocm", digest, **policy)
            with self.assertRaisesRegex(ValueError, "archive"):
                release.verify_smoke(root, runtime["id"], source, "rocm", "c" * 64, **policy)

    def test_remote_smoke_binds_to_the_original_archive(self):
        release = release_script()
        variant = "linux-x86_64-cuda12"
        source = "a" * 40
        digest = "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            embedded = root / f"release-product-{variant}" / "evidence"
            embedded.mkdir(parents=True)
            report = {"source": source, "variant": variant, "backend": "cuda", "archive_sha256": digest}
            release.artifacts.write_json(embedded / "smoke.json", report)
            release.verify_smoke(root, variant, source, "cuda", digest)
            (embedded / "artifact-smoke.json").touch()
            with self.assertRaises(FileNotFoundError):
                release.verify_smoke(root, variant, source, "cuda", digest)
            detached = root / f"release-device-evidence-{variant}"
            detached.mkdir()
            release.artifacts.write_json(detached / "smoke.json", report)
            release.verify_smoke(root, variant, source, "cuda", digest)
            for key in report:
                release.artifacts.write_json(detached / "smoke.json", {**report, key: "different"})
                with self.assertRaises(ValueError):
                    release.verify_smoke(root, variant, source, "cuda", digest)


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
        self.assertEqual(first["url"], "https://github.com/Mesh-LLM/skippy/releases/"
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


class PreparedReleaseContract(unittest.TestCase):
    def setUp(self):
        self.release = release_script()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = "a" * 40
        self.variant = "linux-x86_64-cpu"
        self.enterContext(patch.object(self.release.artifacts, "source", return_value=self.source))
        self.enterContext(patch.object(self.release, "version", return_value="true"))
        self.enterContext(patch.object(self.release, "targets", return_value=({}, {self.variant: {}})))
        name = f"skippy-{TAG}-{self.variant}.tar.gz"
        # Publisher verification uses the exact compressed bytes, without extraction.
        self.archive = self.root / name
        self.archive.write_bytes(b"verified compressed product")
        digest = self.release.artifacts.digest(self.archive)
        catalog = self.release.catalog(TAG, [self.release.catalog_entry({
            "id": self.variant, "release_version": TAG[1:], "skippy_abi": "0.1.86",
        }, TAG, name, digest)])
        catalog_path = self.root / self.release.CATALOG
        self.release.artifacts.write_json(catalog_path, catalog)
        self.receipt = {
            "schema_version": 1, "tag": TAG, "source": self.source,
            "upstream_pin": "b" * 40, "variants": [self.variant],
            "assets": {name: digest, self.release.CATALOG: self.release.artifacts.digest(catalog_path)},
        }
        self.release.artifacts.write_json(self.root / "release.json", self.receipt)
        for file in list(self.root.iterdir()):
            self.release.artifacts.write_checksum(file)

    def test_prepared_assets_verify_without_unpacking_or_rebuilding_inventory(self):
        with patch.object(self.release.artifacts, "unpack", side_effect=AssertionError("unexpected extraction")):
            self.assertEqual(self.release.verify(self.root), self.receipt)

    def test_changed_archive_bytes_fail_even_with_an_updated_checksum_sidecar(self):
        self.archive.write_bytes(b"modified after preparation")
        self.release.artifacts.write_checksum(self.archive)
        with self.assertRaisesRegex(ValueError, "archive identity mismatch"):
            self.release.verify(self.root)

    def test_foreign_source_and_missing_archive_cannot_pass(self):
        with patch.object(self.release.artifacts, "source", return_value="c" * 40):
            with self.assertRaisesRegex(ValueError, "roster/source mismatch"):
                self.release.verify(self.root)
        self.archive.unlink()
        with self.assertRaises(FileNotFoundError):
            self.release.verify(self.root)


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

    def test_automatic_release_takes_the_next_unreleased_patch(self):
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location("candidate", SCRIPTS / "ci-release-candidate.py")
        candidate = module_from_spec(spec)
        spec.loader.exec_module(candidate)
        cases = (
            ("0.80.0", [], "v0.80.0"),
            ("0.80.0", ["v0.80.0"], "v0.80.1"),
            ("0.80.1", ["v0.80.0", "v0.80.1", "v0.80.10", "v0.80.11-rc.1", "v0.79.40"], "v0.80.11"),
            ("0.81.0", ["v0.80.7"], "v0.81.0"),
        )
        for version, tags, expected in cases:
            self.assertEqual(candidate.next_patch(version, tags), expected)
        with self.assertRaisesRegex(ValueError, "stable workspace version"):
            candidate.next_patch("0.81.0-rc.1", [])

    def test_automatic_release_skips_a_main_that_is_already_released(self):
        self.assertEqual(self.release("next").stdout.strip(), "v0.80.0")
        self.git("tag", "-a", "v0.80.0", "-m", "Skippy v0.80.0")
        self.git("push", "origin", "refs/tags/v0.80.0")
        self.assertEqual(self.release("next").stdout.strip(), "")
        self.git("commit", "--allow-empty", "-m", "change")
        self.env["GITHUB_SHA"] = self.git("rev-parse", "HEAD")
        self.assertEqual(self.release("next").stdout.strip(), "v0.80.1")

    def test_automatic_release_skips_a_main_released_by_a_tag_beside_it(self):
        self.git("tag", "v0.80.0")
        self.git("commit", "--allow-empty", "-m", "change")
        head = self.env["GITHUB_SHA"] = self.git("rev-parse", "HEAD")
        self.git("push", "origin", "main", "refs/tags/v0.80.0")
        self.git("commit", "--allow-empty", "-m", "release: prepare v0.80.1")
        self.git("push", "origin", "HEAD:refs/tags/v0.80.1")
        self.git("reset", "--hard", head)
        self.assertEqual(self.release("next").stdout.strip(), "")
        self.git("commit", "--allow-empty", "-m", "change")
        self.env["GITHUB_SHA"] = self.git("rev-parse", "HEAD")
        self.assertEqual(self.release("next").stdout.strip(), "v0.80.2")

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

    def test_tag_only_promotion_leaves_main_and_survives_a_moved_main(self):
        candidate = self.stage()
        tag_ref = f"refs/tags/{TAG}"
        self.git("checkout", "--detach", self.base)
        self.git("commit", "--allow-empty", "-m", "concurrent change")
        advanced = self.git("rev-parse", "HEAD")
        self.git("push", "origin", "HEAD:refs/heads/main")
        self.git("checkout", "--detach", candidate)
        self.env["SKIPPY_RELEASE_TAG_ONLY"] = "1"
        self.release("promote", TAG, self.base, candidate)
        self.release("promote", TAG, self.base, candidate)
        self.assertEqual(self.refs("refs/heads/main", tag_ref),
                         {"refs/heads/main": advanced, tag_ref: candidate})
        self.git("fetch", "origin", "refs/heads/main:refs/remotes/origin/main", f"{tag_ref}:{tag_ref}")
        check = [sys.executable, str(SCRIPTS / "ci-release.py"), "validate", TAG]
        self.run_command(*check)
        self.git("checkout", "--detach", advanced)
        self.git("commit", "--allow-empty", "-m", "not a release")
        self.git("tag", "-f", TAG)
        self.assertNotEqual(self.run_command(*check, check=False).returncode, 0)

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
