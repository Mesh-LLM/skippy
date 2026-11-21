"""Offline contracts for collecting and comparing nightly inference metrics."""

from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from lib.ci_artifacts import write_json
from lib.inference_baselines import apply_baselines, classify
from lib.inference_ladder import matrix
from lib.inference_markdown import run_summary
from lib.inference_nightly_metrics import agentx_metrics, await_artifacts, contract
from lib.inference_nightly_results import nightly_run, qualification_run


SOURCE = "a" * 40
PR_PATH = Path(__file__).resolve().parents[1] / "inference-publish-nightly.py"
COMPARISON = run_path(str(PR_PATH.with_name("inference-compare.py")))


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
    certification = None
    if entry["mode"] == "split":
        certification = "uncertified_override"
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
        "job_id": "job_fixture", "run_id": "run_fixture", "split_certification": certification,
        "backend": backend,
        "contract_sha256": contract("pr", entry, model, certification),
        "metrics": {"prefill_tok_s": value, "decode_tok_s": value},
    })


class NightlyTests(unittest.TestCase):
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
        self.assertEqual(classify("decode_tok_s", 95, 100), "passed")
        self.assertEqual(classify("decode_tok_s", 94.99, 100), "regressed")
        self.assertEqual(classify("ttft_p95_ms", 105.01, 100), "regressed")
        self.assertEqual(classify("ttft_p95_ms", 94, 100), "improved")

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
        _, passing = run(2, "b" * 40, [97, 97], seeded)
        self.assertEqual(passing[0]["value"], 100)
        self.assertEqual(passing[0]["latest_passing"]["value"], 97)
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
                         if config["workload"].endswith("(experimental split)"))
            self.assertEqual(split["outcome"], "passed")
            self.assertEqual(report["outcome"], "failed")
            self.assertEqual(len(report["configurations"]), 31)
            wrong_source = nightly_run(root, "b" * 40, "23", "1", report["started_at"], True)
            self.assertTrue(all(config["outcome"] != "passed" for config in wrong_source["configurations"]))

    def test_only_exact_protected_schedule_can_publish(self):
        admit = run_path(str(PR_PATH))["admit"]
        environment = {
            "GITHUB_EVENT_NAME": "schedule", "GITHUB_REPOSITORY": "general-inference/skippy",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": "general-inference/skippy/.github/workflows/inference.yml@refs/heads/main",
            "GITHUB_WORKFLOW_SHA": SOURCE, "GITHUB_SHA": SOURCE, "SOURCE_SHA": SOURCE,
            "GITHUB_RUN_ID": "23", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_NUMBER": "34",
            "RUN_STARTED_AT": "2026-11-21T00:00:00Z", "MATRIX_RESULT": "failure",
        }
        self.assertEqual(admit(environment), (SOURCE, "23", "1", 34, environment["RUN_STARTED_AT"], False))
        for name in ("pull_request_target", "workflow_dispatch", "push", "workflow_call"):
            with self.assertRaisesRegex(ValueError, "only scheduled"):
                admit({**environment, "GITHUB_EVENT_NAME": name})
        with self.assertRaisesRegex(ValueError, "must match"):
            admit({**environment, "SOURCE_SHA": "b" * 40})


class ComparisonTests(unittest.TestCase):
    def test_release_charts_compare_platforms_without_reading_a_nightly_baseline(self):
        run = {"id": "23-1", "profile": "release", "outcome": "passed", "source_sha": SOURCE,
               "started_at": "2026-11-21T00:00:00Z", "configurations": []}
        for prefix, gpu, value in (("", "DGX Spark", 100), ("metal-", "Apple M3 Ultra", 125)):
            workload = f"{gpu} AgentX release v1"
            run["configurations"].append({"id": f"{prefix}qwen35-4b-standalone", "workload": workload,
                                          "outcome": "passed", "metrics": [
                                              {"workload": workload, "metric": "output_tok_s", "measured": value},
                                          ]})
        read = Mock(side_effect=AssertionError("release has no matching public workload"))
        compared = COMPARISON["compare"](run, "general-inference/skippy", load=read)
        report = run_summary({"schema_version": 1, "runs": [compared]}, run["id"])
        self.assertIn("no matching release baseline", report)
        chart = report.split("```mermaid", 1)[1].split("```", 1)[0]
        self.assertIn("CUDA - Qwen3.5-4B (1 Spark) — 100", chart)
        self.assertIn("Metal - Qwen3.5-4B (M3 Ultra) — 125", chart)

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
        repo = "general-inference/skippy"
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
                                         compatible=Mock(return_value={"cuda", "metal"}))
        self.assertEqual(compared["outcome"], "failed")
        observations = [row for c in compared["configurations"] for row in c["metrics"]]
        self.assertEqual({row["verdict"] for row in observations}, {"regressed", "improved", "passed"})
        self.assertFalse(any("baseline_update" in row for row in observations))
        self.assertTrue(all(row["value"] == 100 and "latest_passing" not in row for row in saved))
        report = run_summary({"schema_version": 1, "runs": [compared]}, compared["id"])
        for expected in ("**Failed**", "```mermaid", "Red = regression", "Green = improvement",
                         "94 tok/s", "-6.00%", "CUDA - Qwen3.5-4B", "Metal - Qwen3.5-4B",
                         f"/blob/{revision}/baselines.json"):
            self.assertIn(expected, report)
        charts = [part.split("```", 1)[0] for part in report.split("```mermaid")[1:]]
        self.assertTrue(any("CUDA - Qwen3.5-4B" in chart and "Metal - Qwen3.5-4B" in chart for chart in charts))
        changed = COMPARISON["compare"](run, repo, load=lambda: (revision, saved),
                                        compatible=Mock(return_value={"metal"}))
        self.assertEqual(changed["outcome"], "passed")
        self.assertIn("benchmark inputs changed", changed["note"])
        first = COMPARISON["compare"](run, repo, load=lambda: (None, []))
        self.assertIn("first nightly", first["note"])
        self.assertNotIn("baseline_revision", first)


if __name__ == "__main__":
    unittest.main()
