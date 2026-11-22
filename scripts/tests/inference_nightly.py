"""Offline contracts for collecting and comparing nightly inference metrics."""

import json
import os
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from lib.ci_artifacts import write_json
from lib.inference_baselines import apply_baselines, classify
from lib.inference_inputs import input_hashes
from lib.inference_ladder import matrix
from lib.inference_markdown import run_summary
from lib.inference_admission import split_admissions_digest
from lib.inference_nightly_metrics import agentx_metrics, await_artifacts, collect_measurements, contract
from lib.inference_nightly_results import nightly_run, qualification_run, read_configuration
from lib.inference_publication_policy import publication_allowed


SOURCE = "a" * 40
PR_PATH = Path(__file__).resolve().parents[1] / "inference-publish-nightly.py"
COMPARISON = run_path(str(PR_PATH.with_name("inference-compare.py")))


def admissions_fixture():
    return {"schema_version": 2, "stages": [
        {"package_id": "fixture", "plan_id": f"plan-{start}", "layer_start": start,
         "layer_end": start + 2, "resident_tensor_ids": [start], "profiles": [{"profile_id": "decode"}]}
        for start in (0, 2)]}


def trial(throughput, latency, requests):
    return {
        "aiperf_version": "0.13.0", "is_complete": True, "error_summary": [],
        "metadata": {"submission_valid": True}, "request_error_rate": {"avg": 0},
        "request_count": {"avg": requests}, "output_token_throughput": {"avg": throughput},
        "time_to_first_token": {"p95": latency}, "inter_token_latency": {"p95": 10},
    }


def smoke_receipt(root, sha, run_id, configuration="qwen35-4b-standalone", value=100,
                  *, published=True, attempt="1", backend="cuda"):
    profile = "nightly" if published else "pr"
    entry = next(row for row in matrix(profile, sha, include_smoke=published, backend=backend)
                 if (row["profile"], row["configuration"]) == ("pr", configuration))
    directory = root / f'inference-{entry["id"]}-{run_id}-{attempt}'
    directory.mkdir(parents=True)
    job = {"id": "job_fixture", "repo_rev": sha, "status": "succeeded",
           "runs": [{"id": "run_fixture", "status": "succeeded"}]}
    admission_hash = None
    if entry["mode"] == "split":
        admission_hash = split_admissions_digest(admissions_fixture())
        write_json(directory / "split-admissions.json", admissions_fixture())
        job["formation_id"] = "frm_fixture"
        write_json(directory / f"{configuration}-formation.json", {
            "id": "frm_fixture", "status": "succeeded",
            "members": [{"job_id": "job_fixture", "role": "leader", "status": "succeeded"},
                        {"job_id": "job_worker", "role": "worker", "status": "succeeded"}],
        })
    write_json(directory / f"{configuration}-job.json", job)
    model = configuration.removeprefix("metal-").removesuffix(f'-{entry["mode"]}')
    write_json(directory / "measurements.json", {
        "schema_version": 1, "source_sha": sha, "configuration": configuration, "profile": "pr",
        "backend": backend,
        "job_id": "job_fixture", "run_id": "run_fixture", "split_admissions_sha256": admission_hash,
        "contract_sha256": contract("pr", entry, model),
        "metrics": {"prefill_tok_s": value, "decode_tok_s": value},
    })


class NightlyTests(unittest.TestCase):
    def test_every_profile_requires_native_stage_admissions_without_an_override(self):
        prepare_split = run_path(str(PR_PATH.with_name("inference-execute.py")))["prepare_split"]
        peers = json.dumps([{"role": "leader", "host": "192.0.2.1", "node": "leader"},
                            {"role": "worker", "host": "192.0.2.2", "node": "worker"}])
        for profile in ("pr", "nightly", "release"):
            for valid in (True, False):
                with self.subTest(profile=profile, valid=valid), TemporaryDirectory() as temporary:
                    def plan_split(args, *, timeout):
                        plan_dir = Path(args[args.index("--output-dir") + 1])
                        plan_dir.mkdir()
                        write_json(plan_dir / "admissions.json", admissions_fixture() if valid else {})
                        write_json(plan_dir / "stage-1.json", {})
                    command = Mock(side_effect=plan_split)
                    with patch.dict(prepare_split.__globals__, command=command), patch.dict(os.environ, JIANYANG_PEERS=peers):
                        args = (Path("model.gguf"), "test-model", profile, 65536, Path(temporary), True)
                        if valid:
                            self.assertEqual(len(prepare_split(*args)), 2)
                        else:
                            with self.assertRaisesRegex(ValueError, "native stage admissions"):
                                prepare_split(*args)
                    self.assertNotIn("--allow-uncertified-split", command.call_args.args[0])

    def test_nightly_split_retains_native_admission_after_valid_agentx_trials(self):
        key = "qwen38-27b"
        configuration = f"{key}-split"
        entry = next(row for row in matrix("nightly", SOURCE) if row["id"] == configuration)
        job = {"id": "job_leader", "repo_rev": SOURCE, "status": "succeeded", "formation_id": "frm_fixture",
               "runs": [{"id": "run_fixture", "status": "succeeded"}]}
        qualification = {"source_sha": SOURCE, "model": key, "mode": "split", "profile": "nightly",
                         "status": "passed", "artifact_manifest_sha256": input_hashes()["model_manifest_sha256"],
                         "gpu": "NVIDIA GB10", "split_admissions_sha256": split_admissions_digest(admissions_fixture())}
        formation = {"id": "frm_fixture", "status": "succeeded",
                     "members": [{"role": "leader", "job_id": "job_leader", "status": "succeeded"},
                                 {"role": "worker", "job_id": "job_worker", "status": "succeeded"}]}
        pilot = Mock()
        pilot.api.side_effect = [qualification, trial(20, 1200, 70), trial(22, 1000, 60), admissions_fixture()]
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / f"inference-{configuration}-23-1"
            directory.mkdir()
            collect_measurements(pilot, job, entry, SOURCE, directory, key, "nightly")
            write_json(directory / f"{configuration}-job.json", job)
            write_json(directory / f"{configuration}-formation.json", formation)
            result = read_configuration(root, entry, SOURCE, "23", "1")
            self.assertEqual(result["outcome"], "passed")
            self.assertEqual(result["workload"], "DGX Spark AgentX nightly v1")
            self.assertEqual(result["metrics"][0]["measured"], 21)
            self.assertEqual(result["metrics"][0]["contract_sha256"], contract("nightly", entry, key))

    def test_platform_recipes_and_audited_cuda_history_stay_separate(self):
        from lib.inference_comparability import canonical_contract
        audit = json.loads(Path("ci/inference-comparability.json").read_text())
        for entry in audit["series"]:
            row = next(row for row in matrix("nightly", SOURCE) if row["id"] == entry["configuration"])
            recipe = contract("nightly", row, row["id"].removesuffix("-" + row["mode"]))
            canonical = canonical_contract(row["id"], entry["workload"], entry["legacy_sha256"])
            if entry["workload"].endswith("(experimental split)"):
                self.assertNotEqual(canonical, recipe)
                self.assertEqual(canonical, entry["recipe_sha256"])
            else:
                self.assertEqual(canonical, recipe)
        cuda = matrix("nightly", SOURCE)[0]
        metal = matrix("nightly", SOURCE, backend="metal")[0]
        key = cuda["id"].removesuffix("-standalone")
        self.assertNotEqual(contract("nightly", cuda, key), contract("nightly", metal, key))

    def test_matrix_preserves_existing_profiles_and_uses_pr_smoke(self):
        ordinary = matrix("nightly", SOURCE)
        both = matrix("nightly", SOURCE, include_smoke=True)
        self.assertEqual(both[:12], ordinary)
        self.assertEqual(len(both), 19)
        self.assertEqual(len({row["id"] for row in both}), 19)
        self.assertEqual(
            [{**row, "id": row["configuration"]} for row in both[12:]], matrix("pr", SOURCE),
        )
        with self.assertRaises(ValueError):
            matrix("release", SOURCE, include_smoke=True)

    def test_metric_direction_and_exact_tolerance(self):
        self.assertEqual(classify("decode_tok_s", 99, 100), "passed")
        self.assertEqual(classify("decode_tok_s", 98.99, 100), "regressed")
        self.assertEqual(classify("ttft_p95_ms", 101.01, 100), "regressed")
        self.assertEqual(classify("ttft_p95_ms", 98, 100), "improved")
        self.assertEqual(classify("decode_tok_s", 101, 100), "passed")
        self.assertEqual(classify("decode_tok_s", 101.01, 100), "improved")
        self.assertEqual(classify("ttft_p95_ms", 99, 100), "passed")
        self.assertEqual(classify("ttft_p95_ms", 98.99, 100), "improved")
        self.assertEqual(classify("ttft_p95_ms", 101, 100), "passed")

    def test_promotes_independent_improvement_and_never_walks_baseline_down(self):
        metrics = [
            {"workload": "smoke", "metric": name, "measured": 100, "contract_sha256": "c" * 64}
            for name in ("prefill_tok_s", "decode_tok_s")
        ]
        config = {"id": "example", "outcome": "passed", "metrics": metrics}

        def run(number, sha, values, saved):
            for metric, value in zip(metrics, values):
                metric["measured"] = value
            return apply_baselines({"id": str(number), "source_sha": sha, "outcome": "passed",
                                    "configurations": [config]}, saved)

        _, seeded = run(1, SOURCE, [100, 100], [])
        _, passing = run(2, "b" * 40, [99, 99], seeded)
        self.assertEqual(passing[0]["value"], 100)
        self.assertEqual(passing[0]["latest_passing"]["value"], 99)
        failed, next_baselines = run(3, "d" * 40, [94, 110], passing)
        self.assertEqual(failed["outcome"], "failed")
        self.assertEqual({item["metric"]: item["value"] for item in next_baselines},
                         {"prefill_tok_s": 100, "decode_tok_s": 110})
        same_source, replayed = run(4, "d" * 40, [100, 120], next_baselines)
        self.assertEqual(replayed[0]["value"], 110)
        self.assertEqual(same_source["configurations"][0]["metrics"][1]["verdict"], "improved")
        metrics[1]["contract_sha256"] = "d" * 64
        updated, fresh = run(5, "e" * 40, [100, 80], replayed)
        self.assertEqual(updated["outcome"], "passed")
        self.assertEqual(fresh[0]["value"], 80)
        self.assertEqual(updated["configurations"][0]["metrics"][1]["baseline_update"], "seeded")

    def test_agentx_aggregates_two_valid_trials(self):
        first = trial(20, 1200, 70)
        second = trial(22, 1000, 60)
        self.assertEqual(agentx_metrics([first, second]), {
            "output_tok_s": 21, "ttft_p95_ms": 1100, "itl_p95_ms": 10,
            "requests": 130, "request_error_pct": 0,
        })
        second["request_error_rate"]["avg"] = 5
        with self.assertRaisesRegex(ValueError, "unsuccessful"):
            agentx_metrics([first, second])

    def test_artifact_retry_is_limited_to_not_found(self):
        with patch("lib.inference_nightly_metrics.time.sleep"):
            read = Mock(side_effect=[RuntimeError("JY HTTP 404: not found"), True])
            self.assertTrue(await_artifacts(read))
            self.assertEqual(read.call_count, 2)
            read = Mock(side_effect=RuntimeError("JY HTTP 403: forbidden"))
            with self.assertRaisesRegex(RuntimeError, "403"):
                await_artifacts(read)
            self.assertEqual(read.call_count, 1)

    def test_receipts_are_bound_to_source_and_missing_evidence_fails(self):
        with TemporaryDirectory() as temporary, patch("lib.inference_nightly_results.print", create=True):
            root = Path(temporary)
            smoke_receipt(root, SOURCE, "23", configuration="qwen3-coder-30b-a3b-split")
            report = nightly_run(root, SOURCE, "23", "1", "2026-11-21T00:00:00Z", True)
            split = next(config for config in report["configurations"]
                         if config["id"] == "qwen3-coder-30b-a3b-split" and config["workload"] == "DGX Spark smoke v1")
            self.assertEqual(split["outcome"], "passed")
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(len(report["configurations"]), 31)
            wrong_source = nightly_run(root, "b" * 40, "23", "1", report["started_at"], True)
            self.assertTrue(all(config["outcome"] != "passed" for config in wrong_source["configurations"]))

    def test_scheduled_and_manual_nightly_require_the_same_protected_controller(self):
        admit = run_path(str(PR_PATH))["admit"]
        environment = {
            "GITHUB_EVENT_NAME": "schedule", "GITHUB_REPOSITORY": "Mesh-LLM/skippy",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": "Mesh-LLM/skippy/.github/workflows/inference.yml@refs/heads/main",
            "GITHUB_WORKFLOW_SHA": SOURCE, "GITHUB_SHA": SOURCE, "SOURCE_SHA": SOURCE,
            "GITHUB_RUN_ID": "23", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_NUMBER": "34",
            "RUN_STARTED_AT": "2026-11-21T00:00:00Z", "MATRIX_RESULT": "failure",
            "QUALIFICATION_PROFILE": "nightly",
        }
        for name in ("schedule", "workflow_dispatch"):
            with self.subTest(event=name):
                candidate = {**environment, "GITHUB_EVENT_NAME": name}
                self.assertTrue(publication_allowed(candidate, "nightly"))
                self.assertEqual(admit(candidate),
                                 (SOURCE, "23", "1", 34, environment["RUN_STARTED_AT"], False))
        for name in ("pull_request_target", "pull_request", "push", "workflow_call"):
            with self.assertRaisesRegex(ValueError, "only nightly"):
                admit({**environment, "GITHUB_EVENT_NAME": name})
        for field, value in (
            ("GITHUB_REPOSITORY", "someone/skippy"),
            ("GITHUB_REF", "refs/heads/topic"),
            ("GITHUB_WORKFLOW_REF", "Mesh-LLM/skippy/.github/workflows/release.yml@refs/heads/main"),
            ("GITHUB_WORKFLOW_SHA", "b" * 40),
            ("QUALIFICATION_PROFILE", "pr"),
            ("QUALIFICATION_PROFILE", "release"),
        ):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "only nightly"):
                admit({**environment, field: value})
        with self.assertRaisesRegex(ValueError, "must match"):
            admit({**environment, "SOURCE_SHA": "b" * 40})

    def test_publication_admission_controls_manual_matrix_and_collection(self):
        admit = run_path(str(PR_PATH.with_name("inference-plan.py")))["admit"]
        with TemporaryDirectory() as temporary:
            event, output = (Path(temporary) / name for name in ("event.json", "output"))
            write_json(event, {})
            environment = {
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": "Mesh-LLM/skippy",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_WORKFLOW_REF": "Mesh-LLM/skippy/.github/workflows/inference.yml@refs/heads/main",
                "GITHUB_WORKFLOW_SHA": SOURCE, "GITHUB_SHA": SOURCE,
                "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
                "QUALIFICATION_PROFILE": "nightly",
            }
            for profile in ("nightly", "pr", "release"):
                output.write_text("")
                with patch.dict(os.environ, {**environment, "QUALIFICATION_PROFILE": profile}, clear=True):
                    admit()
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                publish = values["publication"] == "true"
                self.assertEqual(publish, profile == "nightly")
                rows = [row for backend in ("cuda", "metal") for row in matrix(
                    values["profile"], values["source"], include_smoke=publish, backend=backend)]
                self.assertEqual(len(rows), {"nightly": 31, "pr": 11, "release": 20}[profile])


class ComparisonTests(unittest.TestCase):
    def test_candidate_benchmark_comparison_ignores_reporting_and_unrelated_split_changes(self):
        import ast
        compare_harness = COMPARISON["same_harness"]
        protected, candidate = "a" * 40, "b" * 40
        source = Path("scripts/inference-execute.py").read_text()
        tree = ast.parse(source)
        split = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "prepare_split")
        split.body.insert(0, ast.Expr(value=ast.Constant(value="changed split admission")))
        changed = ast.unparse(tree)

        def read(sha, path, repository):
            return changed if sha == candidate and path == "scripts/inference-execute.py" else Path(path).read_text()

        with patch.dict(compare_harness.__globals__, source_text=read), patch(
            "subprocess.check_output", return_value=protected + "\n",
        ):
            self.assertTrue(compare_harness("nightly", candidate, "Mesh-LLM/skippy", "qwen35-4b-standalone"))
            self.assertFalse(compare_harness("nightly", candidate, "Mesh-LLM/skippy", "qwen38-27b-split"))

    def test_public_baseline_is_loaded_at_an_immutable_revision(self):
        data = COMPARISON["DATASET"]
        revision = "c" * 40
        files = {"id": data, "private": False, "gated": False, "sha": revision,
                 "siblings": [{"rfilename": "README.md"}]}
        preview = Mock(return_value=files)
        self.assertEqual(COMPARISON["published_baselines"](preview), (None, []))
        self.assertEqual(preview.call_count, 1)
        history = "runs/23/1.parquet"
        files["siblings"].extend([{"rfilename": "baselines.json"}, {"rfilename": history}])
        manifest = {"schema_version": 1, "last_run_number": 2, "history": [history], "baselines": []}
        fetch = Mock(side_effect=[files, manifest])
        self.assertEqual(COMPARISON["published_baselines"](fetch), (revision, []))
        self.assertEqual(fetch.call_args.args, (f"https://huggingface.co/datasets/{data}/resolve/{revision}/baselines.json",))
        files["siblings"].pop(1)
        with self.assertRaisesRegex(ValueError, "without a baseline manifest"):
            COMPARISON["published_baselines"](lambda _: files)

    def test_pr_comparison_reuses_verified_receipts_and_cannot_publish_a_baseline(self):
        candidate = "b" * 40
        repo = "Mesh-LLM/skippy"
        revision = "c" * 40
        stamp = "2026-11-21T00:00:00Z"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for backend in ("cuda", "metal"):
                for index, row in enumerate(matrix("pr", candidate, backend=backend)):
                    value = (94, 107, 99)[index % 3] + (12 if backend == "metal" else 0)
                    smoke_receipt(root, candidate, "23", row["id"], value, published=False, backend=backend)
            smoke_receipt(root, candidate, "23", matrix("pr", candidate)[1]["id"], 108,
                          published=False, attempt="2")
            run = qualification_run(root, "pr", candidate, "23", "2", stamp, True)
        self.assertEqual(run["outcome"], "passed")
        self.assertEqual(run["configurations"][1]["metrics"][0]["measured"], 108)
        saved = [
            {"configuration": c["id"], "workload": row["workload"], "metric": row["metric"],
             "contract_sha256": row["contract_sha256"], "source_sha": SOURCE, "value": 100}
            for c in run["configurations"] for row in c["metrics"]
        ]
        compared = COMPARISON["compare"](run, repo, load=lambda: (revision, saved),
                                         compatible=Mock(return_value=True))
        self.assertEqual(compared["outcome"], "failed")
        observations = [row for c in compared["configurations"] for row in c["metrics"]]
        self.assertEqual({row["verdict"] for row in observations}, {"regressed", "improved", "passed"})
        self.assertFalse(any("baseline_update" in row for row in observations))
        self.assertTrue(all(row["value"] == 100 and "latest_passing" not in row for row in saved))
        report = run_summary({"schema_version": 1, "runs": [compared]}, compared["id"], chart_format="mermaid")
        for expected in ("6 regressions", "```mermaid", "Regressed", "Improved",
                         "94.00 tok/s", "-6.00%", f"/blob/{revision}/baselines.json"):
            self.assertIn(expected, report)
        charts = [part.split("```", 1)[0] for part in report.split("```mermaid")[1:]]
        self.assertIn("Metal - Qwen3.5-4B", report)
        changed = COMPARISON["compare"](run, repo, load=lambda: (revision, saved),
                                        compatible=Mock(return_value=False))
        self.assertEqual(changed["outcome"], "passed")
        self.assertIn("changed benchmark inputs", changed["note"])
        first = COMPARISON["compare"](run, repo, load=lambda: (None, []))
        self.assertIn("No saved baseline", first["note"])
        self.assertNotIn("baseline_revision", first)


if __name__ == "__main__":
    unittest.main()
