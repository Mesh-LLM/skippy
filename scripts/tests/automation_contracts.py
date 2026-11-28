"""Offline regressions for the artifact, patch, and candidate contracts."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib import ci_artifacts, ci_smoke
from lib.ci_process import command
from lib.ci_targets import validate_native_identity
from lib.graph_contracts import MANIFEST
from lib.native_policy import host_owned, rocm_host_library
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
            self.assertFalse(startup.kwargs["inherit_env"])
            self.assertIn("verify-runtime-package", native_verify.args[0])
            self.assertEqual(ci_artifacts.read_json(report), {
                "source": "a" * 40, "variant": variant, "backend": "cuda",
                "archive_sha256": ci_artifacts.digest(archive),
            })

    def test_release_evidence_matches_target_policy_and_archive(self):
        release = module("ci-release")
        variant = "linux-x86_64-rocm"
        source, digest = "a" * 40, "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / f"release-product-{variant}" / "evidence"
            evidence.mkdir(parents=True)
            identity = {"source": source, "variant": variant, "backend": "rocm"}
            artifact = evidence / "artifact-smoke.json"
            ci_artifacts.write_json(artifact, {**identity, "archive_sha256": "c" * 64})
            with self.assertRaisesRegex(ValueError, "archive"):
                release.verify_smoke(root, variant, source, "rocm", digest, artifact_only=True)
            ci_artifacts.write_json(artifact, {**identity, "archive_sha256": digest})
            release.verify_smoke(root, variant, source, "rocm", digest, artifact_only=True)
            with self.assertRaises(FileNotFoundError):
                release.verify_smoke(root, variant, source, "rocm", digest)

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
        with patch.object(Path, "is_file", return_value=False):
            self.assertEqual(product.rocm_toolkit_version(Path(r"C:\Program Files\AMD\ROCm\7.2.0")), "7.2.0")

    def test_rocm_versions_match_accepts_the_windows_short_root(self):
        product = module("ci-product")
        self.assertTrue(product.rocm_versions_match("7.2", "7.2.0"))
        self.assertTrue(product.rocm_versions_match("7.2.0", "7.2.0"))
        self.assertTrue(product.rocm_versions_match("7.2.4-93~24.04", "7.2.4"))
        self.assertFalse(product.rocm_versions_match("7.2", "7.2.4"))
        self.assertFalse(product.rocm_versions_match("7.1", "7.2.0"))
        self.assertFalse(product.rocm_versions_match(None, "7.2.0"))

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
    def test_generator_commands_inherit_job_environment(self):
        generator = module("generate-skippy-family-patch")
        args = SimpleNamespace(timeout=10, max_diff_bytes=1024)
        with patch.dict(os.environ, ACTIONS_RUNTIME_TOKEN="fixture-cache-access"):
            output = generator.run(args, [sys.executable, "-c",
                "import os; print(os.environ['ACTIONS_RUNTIME_TOKEN'])"], cwd=ROOT, capture=True)
        self.assertEqual(output.strip(), "fixture-cache-access")

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
        roster = ci_artifacts.read_json(ROOT / MANIFEST)["models"]
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

    def prepare_fixture(self, sandbox):
        root = sandbox / "checkout"
        root.mkdir()
        git(root, "init", "--initial-branch=main")
        pin = root / "llama_cpp/upstream.txt"
        pin.parent.mkdir()
        pin.write_text("a" * 40 + "\n", encoding="utf-8")
        (root / ".gitignore").write_text("/.deps/\n/target/\n", encoding="utf-8")
        # prepare() stages every allowlisted path, so the fixture carries them all.
        for relative in ("crates/skippy-ffi/src/bindings/llama.rs", "docs/native-api.md"):
            (root / relative).parent.mkdir(parents=True)
            (root / relative).write_text("fixture\n", encoding="utf-8")
        return root, commit(root, "base")

    def run_prepare(self, root, regenerate, upstream):
        source = module("upgrade-source")
        real = source.command

        def native(args, **kwargs):
            if args[:1] == ["just"]:
                return None
            return real(args, **kwargs)

        cwd = os.getcwd()
        os.chdir(root)
        try:
            with patch.object(source, "seed_patch_preimages"), patch.object(source, "regenerate", regenerate), patch.object(source, "check_upgrade_coverage"), \
                    patch.object(source, "write_native_recipe"), patch.object(source, "verify_offline"), patch.object(source, "command", native), \
                    patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GITHUB_OUTPUT", None)
                with patch("sys.stdout", io.StringIO()) as stdout:
                    source.prepare(upstream)
                return stdout.getvalue()
        finally:
            os.chdir(cwd)

    def test_failed_preparation_keeps_native_evidence_without_candidate(self):
        failure = RuntimeError("native patch replay failed")

        def conflicting(checkout):
            native = checkout / ".deps/llama.cpp"
            native.mkdir(parents=True)
            git(native, "init", "--initial-branch=main")
            (native / "model.cpp").write_text("upstream\n", encoding="utf-8")
            commit(native, "base")
            (native / "model.cpp").write_text("skippy\n", encoding="utf-8")
            commit(native, "skippy change")
            mail = checkout / "conflict.patch"
            mail.write_text(git(native, "format-patch", "-1", "--stdout") + "\n", encoding="utf-8")
            git(native, "reset", "--hard", "HEAD~1")
            (native / "model.cpp").write_text("refactored upstream\n", encoding="utf-8")
            commit(native, "upstream refactor")
            with self.assertRaises(subprocess.CalledProcessError):
                git(native, "-c", "user.name=Fixture", "-c", "user.email=fixture@skippy.local",
                    "am", "--3way", str(mail))
            raise failure

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly", "GIT_CONFIG_VALUE_0": "true",
        }):
            for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
                os.environ.pop(key, None)
            root, base = self.prepare_fixture(Path(tmp))
            with self.assertRaises(RuntimeError) as raised:
                self.run_prepare(root, conflicting, "b" * 40)
            self.assertIs(raised.exception, failure)
            output = root / "target/llama-upgrade"
            evidence = output / "diagnostics"
            self.assertIn("UU model.cpp", (evidence / "source-status.txt").read_text())
            self.assertIn("<<<<<<<", (evidence / "source.diff").read_text())
            failed = (evidence / "failed.patch").read_text()
            self.assertIn("diff --git a/model.cpp b/model.cpp", failed)
            self.assertIn("+skippy", failed)
            self.assertEqual(sorted(path.name for path in output.iterdir()), ["diagnostics"])
            self.assertEqual(git(root, "rev-parse", "HEAD"), base)
            self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_prepared_candidate_bundle_restores_as_direct_child(self):
        upstream = "b" * 40

        def regenerated(checkout):
            generated = checkout / "llama_cpp/patches/generated"
            generated.mkdir(parents=True)
            (generated / "series").write_text("0001-family-fixture.patch\n", encoding="utf-8")
            (generated / "0001-family-fixture.patch").write_text("fixture\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            root, base = self.prepare_fixture(Path(tmp))
            stdout = self.run_prepare(root, regenerated, upstream)
            output = root / "target/llama-upgrade"
            self.assertEqual(sorted(path.name for path in output.iterdir()),
                             ["candidate.bundle", "candidate.json", "diagnostics"])
            metadata = json.loads((output / "candidate.json").read_text())
            candidate = metadata["candidate_sha"]
            self.assertIn(f"candidate_sha={candidate}", stdout)
            self.assertEqual(metadata, {"schema_version": 1, "base_sha": base, "candidate_sha": candidate,
                                        "upstream_sha": upstream,
                                        "bundle_sha256": ci_artifacts.digest(output / "candidate.bundle")})
            restore_at(root, output / "candidate.bundle", candidate)
            self.assertEqual(git(root, "rev-parse", "HEAD"), candidate)
            self.assertEqual(git(root, "show", f"{candidate}:llama_cpp/upstream.txt"), upstream)
            self.assertEqual(sorted(git(root, "diff", "--name-only", base, candidate).splitlines()),
                             ["llama_cpp/patches/generated/0001-family-fixture.patch",
                              "llama_cpp/patches/generated/series", "llama_cpp/upstream.txt"])

    def test_failed_ci_logs_reach_ffi_repair_and_a_fresh_restorable_candidate(self):
        source = module("upgrade-source")
        upstream = "b" * 40
        with tempfile.TemporaryDirectory() as tmp:
            root, base = self.prepare_fixture(Path(tmp))
            def regenerate(checkout):
                generated = checkout / "llama_cpp/patches/generated"
                generated.mkdir(parents=True, exist_ok=True)
                (generated / "series").write_text("0001-fixture.patch\n")
                (generated / "0001-fixture.patch").write_text("fixture\n")
            self.run_prepare(root, regenerate, upstream)
            output = root / "target/llama-upgrade"
            original_candidate = json.loads((output / "candidate.json").read_text())["candidate_sha"]
            diagnostics = root / "target/diagnostics"
            collector = module("upgrade-diagnostics")
            failure_log = 'undefined symbol: llama_set_embeddings_nextn(llama_context*, bool, bool)\n'
            with patch.dict(os.environ, GITHUB_RUN_ID='45296606108'), \
                 patch.object(collector, 'gh', side_effect=['--jq --paginate', json.dumps({'id': 138197945347, 'name': 'qualify / Quality canary', 'steps': []}), failure_log]):
                collector.collect(diagnostics)
            agent = Path(tmp) / 'agent.py'
            adapters = ('static_bindings.rs', 'dynamic.rs', 'llama_draft.rs')
            agent.write_text(
                'import sys\nfrom pathlib import Path\n'
                f'evidence=Path({str(output / "diagnostics/previous-ci")!r})\n'
                f'assert (evidence/"actions-138197945347.log").read_text()=={failure_log!r}\n'
                'assert (evidence/"actions-log-collection.json").is_file()\n'
                f'for adapter in {adapters!r}:\n'
                '    path=Path("crates/skippy-ffi/src")/adapter\n'
                '    assert str(path) in sys.argv[-1]\n'
                '    path.write_text("upstream C linkage adapter repair\\n")\n')
            real = source.command
            def native(args, **kwargs):
                return None if args[:1] == ["just"] else real(args, **kwargs)
            cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.object(source, "seed_patch_preimages"), patch.object(source, "regenerate", regenerate), \
                     patch.object(source, "check_upgrade_coverage"), patch.object(source, "write_native_recipe"), patch.object(source, "verify_offline"), \
                     patch.object(source, "repair", wraps=source.repair) as repair_agent, \
                     patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND=json.dumps([sys.executable, str(agent)])), \
                     patch.object(source, "command", side_effect=native), \
                     patch("sys.stdout", io.StringIO()):
                    source.prepare(upstream, output / "candidate.bundle", diagnostics)
                repaired = json.loads((output / "candidate.json").read_text())["candidate_sha"]
                self.assertNotEqual(repaired, original_candidate)
                self.assertEqual(git(root, "bundle", "list-heads", str(output / "candidate.bundle")), f"{repaired} HEAD")
                git(root, "fetch", str(output / "candidate.bundle"), "HEAD")
                self.assertEqual(git(root, "rev-list", "--parents", "-n1", repaired), f"{repaired} {base}")
                repair_agent.assert_called_once()
                restore_at(root, output / 'candidate.bundle', repaired)
                for adapter in adapters:
                    self.assertEqual((root / 'crates/skippy-ffi/src' / adapter).read_text(),
                                     'upstream C linkage adapter repair\n')
            finally:
                os.chdir(cwd)

    def test_preparation_graph_failure_gets_one_bounded_repair(self):
        source = module("upgrade-source")
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.prepare_fixture(Path(tmp))
            def regenerate(checkout):
                (checkout / "target/llama-regeneration").mkdir(parents=True, exist_ok=True)
            def coverage(checkout, _base):
                output = checkout / "target/llama-regeneration/graph-coverage.json"
                errors = ["qwen3-dense: existing contract mismatch"] if checked.call_count == 1 else []
                output.write_text(json.dumps({"errors": errors}))
                if errors:
                    raise ValueError(errors[0])
            def agent(checkout, diagnostics, **_options):
                self.assertTrue((diagnostics / "graph-coverage.json").is_file())
                (checkout / "llama_cpp/patches").mkdir(exist_ok=True)
                (checkout / "llama_cpp/patches/repair.patch").write_text("existing contract fix\n")
            real = source.command
            def native(args, **kwargs):
                return None if args[:1] == ["just"] else real(args, **kwargs)
            cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch.object(source, "seed_patch_preimages"), patch.object(source, "regenerate", side_effect=regenerate), \
                     patch.object(source, "check_upgrade_coverage", side_effect=coverage) as checked, \
                     patch.object(source, "write_native_recipe"), patch.object(source, "verify_offline"), \
                     patch.object(source, "repair", side_effect=agent) as repaired, \
                     patch.object(source, "command", side_effect=native), \
                     patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND='["agent"]'), \
                     patch("sys.stdout", io.StringIO()):
                    source.prepare("b" * 40)
                self.assertEqual(checked.call_count, 2)
                repaired.assert_called_once()
                self.assertTrue((root / "target/llama-upgrade/candidate.bundle").is_file())
            finally:
                os.chdir(cwd)

    def test_publisher_rejects_a_different_candidate_than_successful_ci(self):
        publisher = module("upgrade-publish")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "candidate.bundle"
            bundle.write_bytes(b"fixture")
            candidate = {"candidate_sha": "a" * 40}
            with patch.object(publisher, "import_candidate", return_value=candidate):
                self.assertEqual(publisher.validated_candidate(root, bundle, "a" * 40), candidate)
                with self.assertRaisesRegex(ValueError, "successful full CI"):
                    publisher.validated_candidate(root, bundle, "b" * 40)
                with self.assertRaisesRegex(ValueError, "complete lowercase Git"):
                    publisher.validated_candidate(root, bundle, "main")

    def test_generated_upgrade_description_passes_the_actual_protected_acceptance_gate(self):
        publisher = module("upgrade-publish")
        candidate = {'candidate_sha': 'a' * 40, 'upstream_sha': 'b' * 40}
        with patch.dict(os.environ, GITHUB_RUN_ID='45354222627'):
            body = publisher.description(candidate)
        script = 'let body="";process.stdin.on("data",s=>body+=s);process.stdin.on("end",()=>require(process.argv[1])(body));'
        checked = subprocess.run(['node', '-e', script, str(ROOT / '.github/scripts/pr-acceptance.cjs')],
                                 input=body, text=True, capture_output=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn(candidate['candidate_sha'], body)
        self.assertIn('/actions/runs/45354222627', body)


class RewriterReport(unittest.TestCase):
    @staticmethod
    def run_report(*args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "rewriter-report.py"), *args],
            capture_output=True, text=True, cwd=SCRIPTS)

    def write_report(self, directory):
        report = directory / "report.json"
        report.write_text(json.dumps({
            "builders": [{"verdict": "transformable", "file": "model.cpp"}],
            "summary": {"transformable": 1, "error": 0},
        }))
        return report

    def test_failed_gate_prints_report_and_fails_the_run(self):
        # Regression: --graph-verify-result rejected 'fail' at argparse, so a
        # failing native verification died on usage text and the report never
        # reached the job log. The report must print, then the gate must fail.
        with tempfile.TemporaryDirectory() as tmp:
            report = self.write_report(Path(tmp))
            result = self.run_report("--report", str(report),
                                     "--patch-drift-gate", "fail",
                                     "--graph-verify-result", "fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("native validation gates failed: graph-verify-result",
                      result.stderr)
        self.assertIn('"transformable": 1', result.stdout)

    def test_passing_gates_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self.write_report(Path(tmp))
            result = self.run_report("--report", str(report),
                                     "--patch-check", "pass",
                                     "--compile-result", "pass",
                                     "--graph-verify-result", "skipped")
        self.assertEqual(result.returncode, 0, result.stderr)


class CargoDownloadsCache(unittest.TestCase):
    """The ~/.cargo downloads cache must not cross filesystem layouts."""

    action = ROOT / ".github" / "actions" / "cache" / "action.yml"

    def downloads_key_lines(self):
        lines = [line.strip() for line in self.action.read_text().splitlines()]
        key = [line for line in lines if line.startswith("key: cargo-downloads-v1")]
        prefix = [line for line in lines
                  if line.startswith("cargo-downloads-v1-") and not line.startswith("key:")]
        self.assertTrue(key, "cargo-downloads cache key is missing")
        self.assertTrue(prefix, "cargo-downloads restore-keys prefix is missing")
        return key[0], prefix[0]

    def test_downloads_keys_carry_a_layout_signature(self):
        # Regression: the downloads cache archives ~/.cargo relative to the
        # workspace. Archives saved by VM-layout jobs store registry paths as
        # ../../../.cargo/..., and container-layout jobs (workspace
        # /__w/skippy/skippy) could not extract them: ../../../ escapes to /
        # where mkdir is denied, so every crate re-downloaded on each run.
        # Both the exact key and the restore-keys prefix must carry the layout
        # signature, or a prefix match would still restore foreign layouts.
        key, prefix = self.downloads_key_lines()
        self.assertIn("${{ env.SKIPPY_CARGO_CACHE_LAYOUT }}", key)
        self.assertIn("${{ env.SKIPPY_CARGO_CACHE_LAYOUT }}", prefix)

    def test_layout_signature_binds_workspace_and_cargo_home(self):
        # The signature must digest the resolved cargo home together with the
        # workspace, because the tar geometry depends on both; hashing only
        # one anchor still lets mixed-layout jobs share an archive lineage.
        lines = self.action.read_text().splitlines()
        self.assertTrue(any('cargo_home="${CARGO_HOME:-$HOME/.cargo}"' in line for line in lines),
                        "cargo home is not resolved from CARGO_HOME")
        self.assertTrue(any("SKIPPY_CARGO_CACHE_LAYOUT=" in line for line in lines),
                        "layout signature is never exported")
        digests = [line for line in lines if "sha256sum" in line or "shasum" in line]
        self.assertTrue(digests, "layout signature is not hashed")
        self.assertTrue(any('"$cargo_home"' in line and '"$GITHUB_WORKSPACE"' in line
                            for line in lines),
                        "layout digest must cover both the cargo home and the workspace")


if __name__ == "__main__":
    unittest.main()
