"""Offline regressions for upgrade selection, evidence admission and repair boundaries."""

import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.ci_artifacts import read_json, write_json
from lib.upgrade_evidence import MANIFEST, artifact_files
from lib.upgrade_suite import POLICY, SELECTION, selected_models, parse_factory
from lib.upgrade_dgx import admit_dgx, validate
from lib.inference_ladder import plan
from lib.upgrade_source import git
from lib.upgrade_repair import repair
from lib.inference_admission import split_admissions_digest
from inference_nightly import admissions_fixture

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = read_json(ROOT / MANIFEST)["models"]
SHA = "a" * 40


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def copied_inputs(root):
    for path in (POLICY, SELECTION, MANIFEST, "ci/inference-artifacts.json", "llama_cpp/upstream.txt"):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())


class Selection(unittest.TestCase):
    def test_routine_cpu_and_cuda_rosters_and_exact_dgx_pins(self):
        original = copy.deepcopy(REGISTRY)
        cpu = selected_models(ROOT, REGISTRY, "cpu")
        cuda = selected_models(ROOT, REGISTRY, "cuda")
        self.assertEqual((len(cpu), len(cuda)), (18, 8))
        self.assertEqual(REGISTRY, original)
        self.assertTrue(all(model["class"] == "causal_generation" for model in cuda))
        fixtures = read_json(ROOT / "ci/inference-artifacts.json")
        for model in cpu:
            list(artifact_files(model["artifact"]))
            dgx = read_json(ROOT / POLICY)["dgx_families"].get(model["family"])
            if dgx:
                key = dgx["fixture"]
                self.assertEqual(model["artifact"]["selector"], dgx["quantization"])
                artifact = model["artifact"]
                self.assertEqual(artifact["revision"], fixtures[key]["revision"])
                self.assertEqual(artifact["files"], [item["path"] for item in fixtures[key]["files"]])
                self.assertEqual(model["execution"]["trunk_layers"], fixtures[key]["layers"])
                self.assertNotIn("mmproj_artifact", model)

    def test_changed_builders_add_real_tests_without_duplicate_core_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copied_inputs(root)
            selection = read_json(root / SELECTION)
            selection["additional_families"] = ["lfm2", "qwen35", "t5-encoder-decoder"]
            write_json(root / SELECTION, selection)
            models = selected_models(root, REGISTRY, "cuda")
            self.assertEqual(len(models), 9)
            self.assertEqual(sum(model["family"] == "qwen35" for model in models), 0)
            self.assertTrue(all(model["class"] == "causal_generation" for model in models))
            cpu = selected_models(root, REGISTRY, "cpu")
            self.assertEqual(sum(model["family"] == "t5-encoder-decoder" for model in cpu), 1)
            selection["upstream_sha"] = SHA
            write_json(root / SELECTION, selection)
            with self.assertRaisesRegex(ValueError, "candidate upstream"):
                selected_models(root, REGISTRY, "cpu")

    def test_full_roster_preserves_exact_dgx_inputs_and_cpu_only_workloads(self):
        self.assertEqual(len(selected_models(ROOT, REGISTRY, "cpu", "full")), 95)
        gpu = selected_models(ROOT, REGISTRY, "cuda", "full")
        self.assertEqual(len(gpu), 89)
        qwen = next(model for model in gpu if model["family"] == "qwen35")
        self.assertEqual(qwen["execution"]["trunk_layers"], 32)

    def test_factory_counts_executable_cases_and_rejects_unknown_enum(self):
        names = 'LLM_ARCH_NAMES = {{ LLM_ARCH_LLAMA, "llama" }, { LLM_ARCH_CLIP, "clip" }};'
        model = 'case LLM_ARCH_LLAMA: return new Model(); default: throw("unsupported model architecture:");'
        self.assertEqual(parse_factory(names, model), ["llama"])
        with self.assertRaisesRegex(ValueError, "reconcile"):
            parse_factory(names, model.replace("LLAMA", "MISSING"))


class Evidence(unittest.TestCase):
    def test_upgrade_is_seven_smoke_rows_with_strict_split_admission(self):
        rows = plan("upgrade", SHA)["rows"]
        self.assertEqual(len(rows), 7)
        self.assertEqual(sum(row["mode"] == "split" for row in rows), 3)
        self.assertEqual(rows, plan("pr", SHA)["rows"])

    def fixture(self, row):
        from lib.ci_artifacts import digest
        key = row["id"].removesuffix("-" + row["mode"])
        qualification = {
            "source_sha": SHA, "model": key, "mode": row["mode"], "profile": "upgrade", "status": "passed",
            "split_admissions_sha256": split_admissions_digest(admissions_fixture()) if row["mode"] == "split" else None,
            "artifact_manifest_sha256": digest(ROOT / "ci/inference-artifacts.json"),
        }
        quality = [{"case": case, "passed": True} for case in read_json(ROOT / "ci/inference-quality.json")]
        return qualification, quality, {"stack_commit": SHA}, key

    def test_dgx_rejects_missing_rows_wrong_source_and_experimental_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for row in plan("upgrade", SHA)["rows"]:
                qualification, quality, provenance, key = self.fixture(row)
                validate(qualification, quality, provenance, row, SHA, key, admissions_fixture())
                write_json(root / (row["id"] + "-qualification.json"), {
                    "qualification": qualification, "quality": quality, "provenance": provenance, "admissions": admissions_fixture(),
                })
            admit_dgx(root, SHA)
            row = plan("upgrade", SHA)["rows"][-1]
            qualification, quality, provenance, key = self.fixture(row)
            qualification["split_admissions_sha256"] = "bad"
            with self.assertRaisesRegex(ValueError, "native split admission"):
                validate(qualification, quality, provenance, row, SHA, key, admissions_fixture())
            qualification["source_sha"] = "b" * 40
            with self.assertRaisesRegex(ValueError, "identity"):
                validate(qualification, quality, provenance, row, SHA, key, admissions_fixture())
            (root / (row["id"] + "-qualification.json")).unlink()
            with self.assertRaises(FileNotFoundError):
                admit_dgx(root, SHA)

    def test_standalone_exercises_public_server_and_rejects_different_output(self):
        from types import SimpleNamespace
        from lib.upgrade_standalone import standalone
        bins = SimpleNamespace(native=Path("native"), bin=Path("rust"), environment=lambda: {})
        response = {"choices": [{"text": "Paris"}], "usage": {"completion_tokens": 1}}
        with tempfile.TemporaryDirectory() as tmp, patch("lib.upgrade_standalone.completion") as completion:
            completion.side_effect = [response, response]
            standalone(REGISTRY[0], Path("model.gguf"), bins, Path(tmp))
            args = completion.call_args_list[1].args[0]
            self.assertEqual(args[:2], ["rust/skippy", "serve"])
            self.assertIn("--model-path", args)
            completion.side_effect = [response, {"choices": [{"text": "London"}]}]
            with self.assertRaisesRegex(ValueError, "differs"):
                standalone(REGISTRY[0], Path("model.gguf"), bins, Path(tmp))


class Qualification(unittest.TestCase):
    def test_cpu_workloads_run_once_and_cuda_uses_no_cpu_build(self):
        qualifier = module("upgrade-certify")
        for backend, expected in (("cpu", (89, 12, 6)), ("cuda", (0, 8, 0))):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                copied_inputs(root)
                original = Path.cwd()
                try:
                    os.chdir(root)
                    with patch.object(qualifier, "load", return_value=REGISTRY), \
                         patch.object(qualifier, "source", return_value=SHA), \
                         patch.object(qualifier, "cache_root", return_value=root), \
                         patch.object(qualifier, "cached", return_value=root / "model.gguf"), \
                         patch.object(qualifier, "recipe_digest", return_value="recipe"), \
                         patch.object(qualifier, "build") as build, \
                         patch.object(qualifier, "standalone") as standalone, \
                         patch.object(qualifier, "causal") as causal, \
                         patch.object(qualifier, "workload") as workload:
                        qualifier.certify(backend, "candidate")
                        self.assertEqual((standalone.call_count, causal.call_count, workload.call_count), expected)
                        self.assertEqual(build.call_count, 1)
                        self.assertEqual(build.call_args.args[1], backend)
                        receipt = read_json(root / "target/llama-upgrade/evidence/receipt.json")
                        self.assertEqual(len(receipt["families"]), 95 if backend == "cpu" else 8)
                finally:
                    os.chdir(original)


class Repair(unittest.TestCase):
    def test_packaged_recipe_has_source_identity_without_an_architecture_roster(self):
        from lib.upgrade_selection import write_native_recipe
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"llama_cpp/upstream.txt": SHA,
                     "llama_cpp/patches/0001.patch": "fixture patch",
                     "llama_cpp/patches/model_support/series": "0001.patch\n",
                     "llama_cpp/patches/model_support/0001.patch": "model patch",
                     "llama_cpp/patches/generated/series": "0001.patch\n",
                     "llama_cpp/patches/generated/0001.patch": "generated patch"}
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            (root / "crates/skippy-api/src").mkdir(parents=True)
            write_native_recipe(root)
            recipe = read_json(root / "crates/skippy-api/src/native-recipe.json")
            self.assertEqual(set(recipe), {"llama_upstream_sha", "patch_queue_sha256"})
            self.assertEqual(recipe["llama_upstream_sha"], SHA)
            before = recipe["patch_queue_sha256"]
            (root / "llama_cpp/patches/0001.patch").write_text("changed patch")
            write_native_recipe(root)
            self.assertNotEqual(read_json(root / "crates/skippy-api/src/native-recipe.json")["patch_queue_sha256"], before)

    def checkout(self, root):
        git(root, "init", "--initial-branch=main")
        (root / "llama_cpp").mkdir()
        (root / "llama_cpp/upstream.txt").write_text(SHA + "\n")
        git(root, "add", ".")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@skippy.local",
            "-c", "commit.gpgsign=false", "commit", "-m", "base")

    def test_agent_cannot_change_qualification_or_selected_upstream(self):
        for path in ("scripts/upgrade-publish.py", "llama_cpp/upstream.txt"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.checkout(root)
                def agent(*_args, **_kwargs):
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("changed\n")
                with patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND='["agent"]'), \
                     patch("lib.upgrade_repair.command", side_effect=agent):
                    with self.assertRaisesRegex(ValueError, "allowlist|upstream pin"):
                        repair(root, root)

    def test_missing_agent_command_is_an_explicit_failure(self):
        with patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND="[]"):
            with self.assertRaisesRegex(ValueError, "installed agent"):
                repair(ROOT, ROOT)


if __name__ == "__main__":
    unittest.main()
