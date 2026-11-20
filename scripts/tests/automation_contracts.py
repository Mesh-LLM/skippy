"""Offline regressions for the artifact, patch, and candidate contracts."""

import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import ci_artifacts, ci_smoke
from lib.ci_process import command
from lib.ci_targets import validate_native_identity
from lib.native_policy import host_owned, rocm_host_library
from lib.upgrade_evidence import MANIFEST, load
from lib.upgrade_source import git, restore_at

SCRIPTS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures")
ROOT = SCRIPTS.parent


def module(name):
    path = SCRIPTS / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def commit(root, message):
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@skippy.local",
        "-c", "commit.gpgsign=false", "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


class Artifacts(unittest.TestCase):
    def test_artifact_smoke_requires_release_architectures_and_cleans_cli_environment(self):
        host = {"os": "linux", "arch": "aarch64", "target": "aarch64-unknown-linux-gnu"}
        variant = "linux-aarch64-cuda13"
        row = {"id": variant, "backend": "cuda", "toolkit": "13.1.0", "gpu_architectures": "90;120"}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(ci_artifacts, "source", return_value="a" * 40), \
             patch.object(ci_smoke, "require_host"), patch.object(ci_smoke, "command") as command, \
             patch.dict(os.environ, {"LD_LIBRARY_PATH": "/host/lib", "DYLD_LIBRARY_PATH": "/host/lib"}):
            product = Path(tmp) / "product"
            runtime = product / "native-runtimes" / variant
            runtime.mkdir(parents=True)
            manifest_path = runtime / "manifest.json"
            cuda = {"toolkit_major": 13, "gpu_arches": ["90"]}
            manifest = {"runtime": {"platform": host, "backend": {"kind": "cuda", "cuda": cuda}}}
            archive = Path(tmp) / "input" / f"skippy-{variant}.tar.gz"
            ci_artifacts.write_json(manifest_path, manifest)
            ci_artifacts.receipt(product, "product", host["target"], variant)
            ci_artifacts.archive(product, archive)
            with self.assertRaisesRegex(ValueError, "architectures differ"):
                ci_smoke.artifact_smoke(host, row, archive.parent)
            report = archive.parent / "evidence" / "artifact-smoke.json"
            self.assertFalse(report.exists())
            self.assertEqual(command.call_count, 1)

            cuda["gpu_arches"] = ["90", "120"]
            ci_artifacts.write_json(manifest_path, manifest)
            (product / "provenance.json").unlink()
            ci_artifacts.receipt(product, "product", host["target"], variant)
            ci_artifacts.archive(product, archive)
            command.reset_mock()
            ci_smoke.artifact_smoke(host, row, archive.parent)
            startup, native_verify = command.call_args_list
            self.assertEqual(startup.args[0][-1], "--version")
            self.assertEqual(Path(startup.args[0][0]).parent, startup.kwargs["cwd"].resolve())
            self.assertNotIn("LD_LIBRARY_PATH", startup.kwargs["overrides"])
            self.assertNotIn("DYLD_LIBRARY_PATH", startup.kwargs["overrides"])
            self.assertIn("verify-runtime-package", native_verify.args[0])
            self.assertEqual(ci_artifacts.read_json(report), {
                "source": "a" * 40, "variant": variant, "backend": "cuda",
                "archive_sha256": ci_artifacts.digest(archive),
            })

    def test_release_evidence_matches_target_policy_and_archive(self):
        release = module("ci-release")
        runtime = {"id": "linux-x86_64-rocm", "backend": "rocm", "validation": "artifact"}
        source, digest = "a" * 40, "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            evidence.mkdir()
            identity = {"source": source, "variant": runtime["id"], "backend": runtime["backend"]}
            artifact = evidence / "artifact-smoke.json"
            ci_artifacts.write_json(artifact, {**identity, "archive_sha256": "c" * 64})
            with self.assertRaisesRegex(ValueError, "mismatched artifact-smoke"):
                release.verify_package_evidence(root, runtime, source, digest)
            ci_artifacts.write_json(artifact, {**identity, "archive_sha256": digest})
            release.verify_package_evidence(root, runtime, source, digest)
            inference = {"id": "linux-x86_64-cuda13", "backend": "cuda", "validation": "inference"}
            ci_artifacts.write_json(artifact, {**identity, "variant": inference["id"], "backend": "cuda",
                                                  "archive_sha256": digest})
            with self.assertRaises(FileNotFoundError):
                release.verify_package_evidence(root, inference, source, digest)

    def test_rocm_manifest_matches_toolkit_and_architectures(self):
        host = {"os": "linux", "arch": "x86_64"}
        runtime = {"backend": "rocm", "toolkit": "7.2.4", "gpu_architectures": "gfx942;gfx1200"}
        rocm = {"version": "7.2.4", "gpu_arches": ["gfx942"]}
        manifest = {"runtime": {"platform": host, "backend": {"kind": "rocm", "rocm": rocm}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ci_artifacts.write_json(root / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "architectures differ"):
                validate_native_identity(root, host, runtime)
            rocm["gpu_arches"].append("gfx1200")
            ci_artifacts.write_json(root / "manifest.json", manifest)
            validate_native_identity(root, host, runtime)
            with self.assertRaisesRegex(ValueError, "toolkit mismatch"):
                validate_native_identity(root, host, {**runtime, "toolkit": "7.2.5"})
        self.assertTrue(rocm_host_library("libamdhip64.so.7", "elf"))
        self.assertFalse(rocm_host_library("libamdhip64.so.7", "pe"))
        self.assertFalse(rocm_host_library("libamdhip64.so.8", "elf"))
        self.assertFalse(host_owned("libamdhip64.so.7", "elf"))

    def test_windows_rocm_manifest_matches_toolkit_and_architectures(self):
        host = {"os": "windows", "arch": "x86_64"}
        arches = "gfx1100;gfx1101;gfx1102;gfx1151;gfx1200;gfx1201"
        runtime = {"backend": "rocm", "toolkit": "7.2.0", "gpu_architectures": arches}
        rocm = {"version": "7.2.0", "gpu_arches": arches.split(";")}
        manifest = {"runtime": {"platform": host, "backend": {"kind": "rocm", "rocm": rocm}}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ci_artifacts.write_json(root / "manifest.json", manifest)
            validate_native_identity(root, host, runtime)
            with self.assertRaisesRegex(ValueError, "toolkit mismatch"):
                validate_native_identity(root, host, {**runtime, "toolkit": "7.2.4"})

    def test_rocm_toolkit_version_reads_info_and_install_root(self):
        product = module("ci-product")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(product.rocm_toolkit_version(root))
            info = root / ".info"
            info.mkdir()
            (info / "version").write_text("7.2.4-93~24.04\n", encoding="utf-8")
            self.assertEqual(product.rocm_toolkit_version(root), "7.2.4-93~24.04")
        self.assertEqual(product.rocm_toolkit_version(Path(r"C:\Program Files\AMD\ROCm\7.2.0")), "7.2.0")
        self.assertIsNone(product.rocm_toolkit_version(Path("/opt/rocm")))

    def test_archive_preserves_executable_hardlink_and_internal_library_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = work / "root"
            root.mkdir()
            program = root / "runtime"
            program.write_bytes(b"binary")
            program.chmod(0o755)
            os.link(program, root / "hardlink")
            if os.name != "nt":
                (root / "alias").symlink_to("runtime")
            output = work / "producer/runtime.tar.gz"
            ci_artifacts.archive(root, output)
            ci_artifacts.unpack(output.parent, work / "restored")
            self.assertEqual(ci_artifacts.files(root), ci_artifacts.files(work / "restored"))
            self.assertTrue((work / "restored/runtime").samefile(work / "restored/hardlink"))
            if os.name != "nt":
                self.assertTrue(os.access(work / "restored/runtime", os.X_OK))
            output.write_bytes(b"modified archive")
            with self.assertRaisesRegex(ValueError, "checksum"):
                ci_artifacts.unpack(output.parent, work / "bad")

    def test_archive_rejects_extraction_through_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "input.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                entry = tarfile.TarInfo("link")
                entry.type = tarfile.SYMTYPE
                entry.linkname = "../outside"
                stream.addfile(entry)
                entry = tarfile.TarInfo("link/sentinel")
                entry.size = 4
                stream.addfile(entry, io.BytesIO(b"data"))
            with self.assertRaises(ValueError):
                ci_artifacts.extract_archive(archive, root / "out")
            self.assertFalse((root / "outside").exists())

    def test_raw_capture_keeps_original_bytes_and_enforces_limit(self):
        args = [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'one\r\ntwo\xff')"]
        self.assertEqual(command(args, capture_stdout=True, raw=True), b"one\r\ntwo\xff")
        with self.assertRaisesRegex(ValueError, "exceeded limit"):
            command(args, capture_stdout=True, raw=True, limit=2)


class Generator(unittest.TestCase):
    def test_patch_mail_bytes_match_frozen_fixture(self):
        generator = module("generate-skippy-family-patch")
        fixture = FIXTURES / "rewriter-patch"
        original = (fixture / "two-model.diff").read_bytes().decode("utf-8")
        output = generator.patch_text("skippy: generate model-family stage controls", original)
        self.assertEqual(output.encode("utf-8"), (fixture / "two-model.patch").read_bytes())

    def test_shards_match_frozen_mail_and_order(self):
        generator = module("generate-skippy-family-patch")
        fixture = FIXTURES / "rewriter-patch-shards"
        names = ("afmoe", "deepseek2--mistral4", "rwkv6")
        mail = {name: (fixture / (name + ".patch")).read_bytes() for name in names}
        sections = {name: data.partition(b"\n---\n")[2].removesuffix(b"-- \n2.54.0\n\n")
                    for name, data in mail.items()}
        diff = b"".join(sections[name] for name in reversed(names))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "generated"
            result = generator.write_family_shards(
                diff.decode("utf-8"), out, fixture / "family-map.json", fixture / "certified.json",
            )
            self.assertEqual(len(result["shards"]), len(names))
            for name, shard in zip(names, result["shards"]):
                self.assertTrue(shard["file"].endswith("-family-" + name + ".patch"))
                self.assertEqual((out / shard["file"]).read_bytes(), mail[name])

    def test_pinned_workload_roster(self):
        roster = load(ROOT)
        self.assertTrue(any(row["family"] == "qwen3-dense" for row in roster))
        self.assertTrue(any(row["family"] == "qwen3tts" for row in roster))


class Upgrade(unittest.TestCase):
    def test_bundle_accepts_only_allowed_direct_child(self):
        for relative in ("llama_cpp/upstream.txt", "elsewhere"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                sandbox = Path(tmp)
                root = sandbox / "checkout"
                root.mkdir()
                git(root, "init", "--initial-branch=main")
                pin = root / "llama_cpp/upstream.txt"
                pin.parent.mkdir()
                pin.write_text("a" * 40 + "\n", encoding="utf-8")
                base = commit(root, "base")
                (root / relative).write_text("b" * 40 + "\n", encoding="utf-8")
                child = commit(root, "candidate")
                bundle = sandbox / "candidate.bundle"
                git(root, "bundle", "create", str(bundle), f"{base}..HEAD")
                git(root, "checkout", "--detach", base)
                if relative == "elsewhere":
                    with self.assertRaisesRegex(ValueError, "allowlist"):
                        restore_at(root, bundle, child)
                    self.assertEqual(git(root, "rev-parse", "HEAD"), base)
                else:
                    restore_at(root, bundle, child)
                    self.assertEqual(git(root, "rev-parse", "HEAD"), child)

    def test_publisher_accepts_complete_exact_passes_only(self):
        publisher = module("upgrade-publish")
        manifest = ci_artifacts.read_json(ROOT / MANIFEST)
        candidate = {"candidate_sha": "a" * 40, "upstream_sha": "b" * 40}
        manifest_hash, recipe_hash = "c" * 64, "d" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pass_name in ("candidate", "independent"):
                for backend in ("cpu", "metal", "cuda"):
                    directory = root / f"llama-{pass_name}-{backend}-evidence"
                    directory.mkdir()
                    evidence = directory / "native-result.json"
                    evidence.write_bytes(b'{"success":true}')
                    ci_artifacts.write_json(directory / "receipt.json", {
                        "schema_version": 1, **candidate, "manifest_sha256": manifest_hash,
                        "native_recipe_sha256": recipe_hash, "backend": backend, "pass": pass_name,
                        "result": "pass", "families": list(publisher.expected_families(manifest).values()),
                        "artifacts": {evidence.name: ci_artifacts.digest(evidence)},
                    })
            publisher.admit_receipts(root, candidate, manifest, manifest_hash, recipe_hash)
            (root / "llama-independent-cuda-evidence/native-result.json").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "changed family evidence"):
                publisher.admit_receipts(root, candidate, manifest, manifest_hash, recipe_hash)


if __name__ == "__main__":
    unittest.main()
