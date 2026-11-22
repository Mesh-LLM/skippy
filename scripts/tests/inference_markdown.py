"""Offline health-report contracts shared by Hugging Face and Actions."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lib.inference_charts import dataset_charts, health_charts, image_path
from lib.inference_comparability import AUDITS, benchmark_functions, benchmark_tree, contracts_match
from lib.inference_health import health_view
from lib.inference_markdown import comparison, dataset_card, run_summary

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/design/inference-qualification-examples"


def configuration(name="qwen35-4b-standalone", fingerprint="a" * 64, factor=1):
    workload = "DGX Spark AgentX nightly v1"
    if name.endswith("-split"):
        workload += " (experimental split)"
    return {"id": name, "outcome": "passed", "workload": workload, "metrics": [
        {"workload": workload, "metric": metric, "measured": value * factor,
         "contract_sha256": fingerprint}
        for metric, value in (("output_tok_s", 100), ("ttft_p95_ms", 1000), ("itl_p95_ms", 10))
    ]}


def run(identifier, date, configs):
    return {"id": identifier, "profile": "nightly", "outcome": "passed", "source_sha": "a" * 40,
            "started_at": date, "published": True, "configurations": configs}


def history():
    return {"schema_version": 1, "active_baselines": [], "runs": [
        run("1-1", "2026-11-21T00:00:00Z", [configuration()]),
        run("2-1", "2026-11-22T00:00:00Z", [configuration(factor=.98),
            configuration("qwen3-coder-30b-a3b-split", "b" * 64)]),
    ]}


class InferenceMarkdownTests(unittest.TestCase):
    def test_card_and_actions_share_health_counts_regressions_and_current_numbers(self):
        report = history()
        card, summary = dataset_card(report), run_summary(report, "2-1")
        for text in (card, summary):
            self.assertIn("1 regressions · 2 improvements · 0 within tolerance · 3 first measurements", text)
            self.assertIn("Tolerance: ±1%", text)
            self.assertIn("## What regressed", text)
            self.assertIn("98.00 tok/s | 100.00 tok/s | -2.00%", text)
            self.assertIn("## Latest numbers", text)
            self.assertIn("Two Sparks · experimental split", text)
            self.assertIn("Qwen3-Coder-30B-A3B", text)
            self.assertNotIn("Lab outcomes", text)
            self.assertNotIn("Baseline updates", text)
            self.assertNotIn("Recent measurements", text)
        self.assertEqual(card.split("**1 regressions", 1)[1], summary.split("**1 regressions", 1)[1])

    def test_figures_cover_all_models_and_all_three_metrics_including_split(self):
        figures = dataset_charts(history())
        self.assertEqual([figure.metric for figure in figures], ["output_tok_s", "ttft_p95_ms", "itl_p95_ms"])
        for figure in figures:
            self.assertEqual(len(figure.panels), 2)
            split = next(panel for panel in figure.panels if panel.configuration.endswith("-split"))
            self.assertIsNone(split.verdict)
            self.assertIsNone(split.reference)
            self.assertEqual(len(split.points), 1)
            standalone = next(panel for panel in figure.panels if panel.configuration.endswith("-standalone"))
            self.assertEqual(len(standalone.points), 2)

    def test_absent_smoke_workloads_do_not_become_performance_rows(self):
        report = history()
        missing = {"id": "unexecuted-smoke", "outcome": "incomplete", "metrics": [
            {"metric": "prefill_tok_s", "workload": "DGX Spark smoke v1", "measured": None},
        ]}
        report["runs"][-1]["configurations"].append(missing)
        text = dataset_card(report)
        self.assertNotIn("unexecuted-smoke", text)
        self.assertNotIn("qualification is incomplete", text)
        self.assertNotIn("Measurement unavailable", text)
        self.assertNotIn("Smoke test", text)

    def test_real_execution_failures_and_request_errors_remain_visible(self):
        report = history()
        report["runs"][-1]["configurations"].append({"id": "qwen35-9b-standalone", "outcome": "failed", "metrics": []})
        report["runs"][-1]["configurations"][0]["metrics"].append({
            "metric": "request_error_pct", "workload": "DGX Spark AgentX nightly v1", "measured": 2,
        })
        text = dataset_card(report)
        self.assertIn("## Execution failures", text)
        self.assertIn("Qwen3.5-9B (1 Spark) | Failed", text)
        self.assertIn("Request errors were measured", text)

    def test_recipe_changes_start_new_series_without_bridging_an_older_match(self):
        report = history()
        report["runs"][0]["configurations"][0]["metrics"][0]["contract_sha256"] = "b" * 64
        report["runs"].insert(0, run("0-1", "2026-11-20T00:00:00Z", [configuration()]))
        output = dataset_charts(report)[0].panels[0]
        self.assertEqual(len(output.points), 1)
        self.assertIsNone(output.verdict)

    def test_a_flat_regression_does_not_become_healthy_against_the_last_regressed_run(self):
        report = history()
        prior = report["runs"][-1]
        current = deepcopy(prior)
        current.update(id="3-1", started_at="2026-11-23T00:00:00Z")
        current["configurations"][0]["metrics"][0]["reference"] = {"value": 100, "source_sha": "b" * 40}
        report["runs"].append(current)
        panel = dataset_charts(report)[0].panels[0]
        self.assertEqual(panel.current, 98)
        self.assertEqual(panel.reference, 100)
        self.assertEqual(panel.verdict, "regressed")

    def test_missing_samples_leave_gaps_and_future_runs_do_not_leak_into_summaries(self):
        report = history()
        report["runs"].insert(1, run("gap-1", "2026-11-21T12:00:00Z", []))
        view = health_view(report, "2-1")
        panel = health_charts(view)[0].panels[0]
        self.assertEqual([value for _, value in panel.points], [100, None, 98])
        report["runs"].append(run("future-1", "2026-11-23T00:00:00Z", [configuration(factor=9)]))
        self.assertEqual(view, health_view(report, "2-1"))

    def test_history_aliases_are_explicit_and_scoped_to_configuration_and_workload(self):
        audit = json.loads(AUDITS.read_text())
        self.assertEqual(len(audit["series"]), 32)
        entry = audit["series"][0]
        args = entry["legacy_sha256"], entry["recipe_sha256"]
        self.assertTrue(contracts_match(entry["configuration"], entry["workload"], *args))
        self.assertFalse(contracts_match("different-model", entry["workload"], *args))
        self.assertFalse(contracts_match(entry["configuration"], "different workload", *args))
        self.assertFalse(contracts_match(entry["configuration"], entry["workload"], None, None))

    def test_recipe_function_digest_ignores_comments_and_unrelated_functions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "benchmark.py"
            path.write_text("def measure():\n    return 1\n")
            initial = benchmark_functions(path, ["measure"])
            path.write_text("# documentation\ndef measure():\n    return 1\ndef admission():\n    return False\n")
            self.assertEqual(initial, benchmark_functions(path, ["measure"]))
            path.write_text("def measure():\n    return 2\n")
            self.assertNotEqual(initial, benchmark_functions(path, ["measure"]))
            with self.assertRaisesRegex(ValueError, "missing"):
                benchmark_functions(path, ["absent"])
            path.write_text("VALUE = 1\ndef measure():\n    return VALUE\n")
            initial = benchmark_functions(path, ["measure"])
            path.write_text("VALUE = 2\ndef measure():\n    return VALUE\n")
            self.assertNotEqual(initial, benchmark_functions(path, ["measure"]))

    def test_optional_python_ast_fields_do_not_reset_benchmark_identity(self):
        import ast
        node = ast.parse("def measure():\n    return 1\n").body[0]
        original = benchmark_tree(node)
        node._fields = (*node._fields, "future_optional_field")
        node.future_optional_field = []
        self.assertEqual(original, benchmark_tree(node))

    def test_preview_metadata_and_empty_or_skipped_runs(self):
        report = history()
        preview = dataset_card({**report, "preview": True})
        self.assertIn("illustrative", preview)
        self.assertNotIn("configs:", preview)
        self.assertIn('path: "runs/*/*.parquet"', dataset_card(report))
        self.assertIn("Awaiting the first nightly publication", dataset_card({"schema_version": 1, "runs": []}))
        empty = run("skipped", "2026-11-22T00:00:00Z", [])
        empty["outcome"] = "skipped"
        text = run_summary({"schema_version": 1, "runs": [empty]}, "skipped")
        self.assertIn("Qualification intentionally skipped", text)
        self.assertNotIn("Performance over time", text)
        with self.assertRaisesRegex(ValueError, "unknown"):
            run_summary(report, "nonexistent")

    def test_actions_embed_public_revision_pinned_pngs(self):
        report = history()
        base = "https://huggingface.co/datasets/meshllm/skippy-inference-qualification/resolve/" + "f" * 40
        summary = run_summary(report, "2-1", image_base_url=base)
        self.assertIn(base + "/charts/qualification-3.png", summary)
        self.assertNotIn("xychart-beta", summary)

    def test_validation_rejects_duplicate_data_and_nonfinite_measurements(self):
        report = history()
        report["runs"][-1]["configurations"][0]["metrics"].append(deepcopy(report["runs"][-1]["configurations"][0]["metrics"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            dataset_card(report)
        report = history()
        report["runs"][-1]["configurations"][0]["metrics"][0]["measured"] = float("nan")
        with self.assertRaisesRegex(ValueError, "invalid"):
            dataset_card(report)
        with self.assertRaises(ValueError):
            comparison({"metric": "output_tok_s", "measured": 1, "verdict": "regressed"})

    def test_review_examples_match_shared_renderer(self):
        report = json.loads((EXAMPLES / "examples.json").read_text())
        for example in EXAMPLES.glob("*.md"):
            expected = dataset_card(report) if example.name == "README.md" else run_summary(
                report, example.stem, image_base_url="summaries/" + example.stem,
            )
            self.assertEqual(expected, example.read_text(), example.name)

    def test_recovered_history_has_exact_one_percent_verdicts_for_all_twelve_configurations(self):
        fixture = Path(__file__).with_name("fixtures") / "inference-health-history.json"
        report = json.loads(fixture.read_text())
        view = health_view(report)
        self.assertEqual(view["counts"], {"new": 12, "passed": 14, "improved": 7, "regressed": 3})
        self.assertEqual(len(view["configurations"]), 12)
        charts = dataset_charts(report)
        self.assertEqual([len(chart.panels) for chart in charts], [12, 12, 12])
        for chart in charts:
            self.assertEqual(sum(panel.configuration.endswith("-split") for panel in chart.panels), 4)


if __name__ == "__main__":
    unittest.main()
