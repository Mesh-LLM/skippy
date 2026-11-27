"""Offline checks for the performance comparison's plan, servers, parsing and report."""

import json
import unittest
from pathlib import Path
from runpy import run_path
from unittest.mock import Mock, patch

from lib import performance_plan
from lib.performance_arms import REQUIRED, capabilities, server_args
from lib.performance_load import measurements
from lib import performance_report
from lib.performance_report import markdown, summarize


class ScenarioTests(unittest.TestCase):
    def test_shipped_scenario_is_valid(self):
        shape = performance_plan.scenario()
        self.assertGreater(len(shape["servers"]), 1)

    def test_scenario_rejects_impossible_servers(self):
        shape = performance_plan.read_json(performance_plan.SCENARIO)
        server = shape["servers"][0]
        for change in ({"lanes": 0}, {"pool_tokens": 1024}):
            broken = json.loads(json.dumps(shape))
            broken["servers"][0].update(change)
            with patch.object(performance_plan, "read_json", return_value=broken):
                with self.assertRaises(ValueError):
                    performance_plan.scenario()
        for change in ({"concurrency": server["lanes"] + 1}, {"depth": shape["context_per_lane"]}):
            broken = json.loads(json.dumps(shape))
            broken["servers"][0]["points"][0].update(change)
            with patch.object(performance_plan, "read_json", return_value=broken):
                with self.assertRaises(ValueError):
                    performance_plan.scenario()

    def test_evidence_ids_cannot_alias_or_escape_directories(self):
        for key in ("server", "point"):
            for invalid in ("./short", "../short", "a/b", "a\\b", ".", "..", "", 1):
                broken = performance_plan.read_json(performance_plan.SCENARIO)
                target = broken["servers"][0]
                if key == "point":
                    target = target["points"][0]
                target["id"] = invalid
                with self.subTest(key=key, invalid=invalid), patch.object(performance_plan, "read_json", return_value=broken):
                    with self.assertRaises(ValueError):
                        performance_plan.scenario()


class PlanTests(unittest.TestCase):
    candidate, baseline, pin = "a" * 40, "b" * 40, "c" * 40

    def test_arms_are_immutable_commits_and_reference_is_optional(self):
        labels = [arm["label"] for arm in performance_plan.arms(self.candidate, self.baseline, self.pin)]
        self.assertEqual(labels, ["candidate", "baseline", "reference"])
        self.assertEqual(len(performance_plan.arms(self.candidate, self.candidate, None)), 2)
        for bad in ("main", "a" * 39):
            with self.assertRaises(ValueError):
                performance_plan.arms(bad, self.baseline, None)

    def test_an_arm_can_be_an_open_pull_request_from_this_repository_only(self):
        self.assertEqual(performance_plan.pull_request_number("pr:130"), 130)
        for ref in ("main", "pr:0", "pr:x", "a" * 40):
            self.assertIsNone(performance_plan.pull_request_number(ref))
        repo = "Mesh-LLM/skippy"
        pull = {"number": 130, "state": "open", "head": {"sha": self.candidate, "repo": {"full_name": repo}}}
        self.assertEqual(performance_plan.pull_request_head(pull, repo), self.candidate)
        fork = {**pull, "head": {"sha": self.candidate, "repo": {"full_name": "someone/skippy"}}}
        for refused in ({**pull, "state": "closed"}, fork, {**pull, "head": {"sha": self.candidate, "repo": None}}):
            with self.assertRaises(ValueError):
                performance_plan.pull_request_head(refused, repo)

    def test_native_pr_admission_requires_collaborator_write_access(self):
        resolve = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["resolve"]
        pull = {"number": 130, "state": "open", "user": {"login": "author"},
                "head": {"sha": self.candidate, "repo": {"full_name": "Mesh-LLM/skippy"}}}
        for permission in ("read", "triage", "none", "write", "maintain", "admin"):
            calls = []
            def github(path):
                calls.append(path)
                return pull if path.endswith("/pulls/130") else {"permission": permission}
            with patch.dict(resolve.__globals__, github=github):
                if permission in {"write", "maintain", "admin"}:
                    self.assertEqual(resolve("pr:130"), (self.candidate, 130))
                else:
                    with self.assertRaisesRegex(ValueError, "collaborator"):
                        resolve("pr:130")
            self.assertEqual(calls[-1], "repos/Mesh-LLM/skippy/collaborators/author/permission")

    def test_every_arm_leads_one_round(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        firsts = [performance_plan.round_order(arm_list, index)[0]["label"] for index in range(3)]
        self.assertEqual(sorted(firsts), ["baseline", "candidate", "reference"])

    def test_job_runs_every_arm_from_its_own_checkout_on_a_metal_host(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        spec = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, "macstudio-2l0352", "m1")
        repos = spec["runtime"]["workspace"]["repos"]
        self.assertEqual([repo["path"] for repo in repos], ["harness", "candidate", "baseline", "reference"])
        self.assertEqual(repos[3]["url"], performance_plan.LLAMA)
        self.assertEqual(repos[3]["rev"], self.pin)
        target = spec["target"]
        self.assertEqual(target["nodes"], ["macstudio-2l0352"])
        self.assertEqual(target["selector"]["accelerator"]["backend"], "metal")
        self.assertGreaterEqual(target["selector"]["memory"]["system_gib"]["min"], 18)
        plan = json.loads(spec["runtime"]["env"]["SKIPPY_PERFORMANCE_PLAN"])
        self.assertEqual((plan["harness"], plan["rounds"], plan["arms"]), ("d" * 40, 3, arm_list))
        self.assertIs(plan["trace"], False)
        traced = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any", trace=True)
        self.assertIs(json.loads(traced["runtime"]["env"]["SKIPPY_PERFORMANCE_PLAN"])["trace"], True)
        with self.assertRaises(ValueError):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any", trace="true")
        self.assertNotIn("nodes", performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any")["target"])
        with self.assertRaises(ValueError):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], performance_plan.MAX_ROUNDS + 1, None, "x")

    def test_models_must_be_ladder_ids(self):
        with self.assertRaises(ValueError):
            performance_plan.models(["qwen35-4b", "qwen35-4b"])
        with self.assertRaises(ValueError):
            performance_plan.models(["llama-70b"])


class CleanupTests(unittest.TestCase):
    def test_all_jobs_are_attempted_despite_individual_cancel_failures(self):
        from tempfile import TemporaryDirectory
        cancel = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["cancel"]
        with TemporaryDirectory() as directory:
            ledger = Path(directory) / "jobs.json"
            ledger.write_text(json.dumps(["job_1", "job_2", "job_3"]))
            pilot = Mock()
            pilot.api.side_effect = [RuntimeError("GET failed"), {"status": "running"},
                                     RuntimeError("POST failed"), {"status": "queued"}, {}]
            with self.assertRaisesRegex(RuntimeError, "job_1.*job_2"):
                cancel(pilot, ledger)
            self.assertEqual(pilot.api.call_args.args, ("POST", "/v1/jobs/job_3/cancel"))

    def test_dispatch_retains_original_error_when_cleanup_fails(self):
        from tempfile import TemporaryDirectory
        dispatch = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["dispatch"]
        with TemporaryDirectory() as directory:
            evidence = Path(directory)
            (evidence / "plan.json").write_text(json.dumps({"jobs": [{"spec": {}, "label": "test"}]}))
            pilot = Mock()
            pilot.api.side_effect = RuntimeError("original dispatch failure")
            with patch.dict(dispatch.__globals__, Pilot=lambda: pilot,
                            cancel=Mock(side_effect=RuntimeError("cleanup failure"))), patch("sys.stderr") as stderr:
                with self.assertRaisesRegex(RuntimeError, "original dispatch failure"):
                    dispatch(evidence)
                self.assertIn("cleanup failure", "".join(str(call) for call in stderr.write.call_args_list))


class ServerTests(unittest.TestCase):
    skippy = {"label": "baseline", "kind": "skippy", "binary": "/b/skippy"}
    server = {"id": "lanes4-pool16k", "lanes": 4, "pool_tokens": 16384}

    def test_an_older_skippy_runs_without_settings_it_lacks_and_says_so(self):
        help_text = " ".join(REQUIRED) + "  --generation-concurrency <N>\n  --adaptive-generation-concurrency\n"
        args, applied, missing = server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertIn("--generation-concurrency", args)
        self.assertEqual(applied["generation_concurrency"], 4)
        self.assertEqual(missing, ["--speculative-strategy", "--kv-pool-tokens", "--debug"])

    def test_measured_servers_run_at_debug_verbosity_for_offload_evidence(self):
        help_text = (" ".join(REQUIRED) + " --debug --generation-concurrency <N>"
                     " --speculative-strategy <S> --kv-pool-tokens <N>\n")
        args, applied, missing = server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertEqual(args.count("--debug"), 1)
        self.assertNotIn("None", args)
        self.assertIs(applied["debug"], True)
        self.assertEqual(missing, [])

    def test_required_options_are_probed_before_any_measured_round(self):
        for missing in REQUIRED:
            help_text = " ".join(flag for flag in REQUIRED if flag != missing)
            with patch("lib.performance_arms.command", return_value=help_text):
                with self.assertRaisesRegex(ValueError, "unsupported.*" + missing):
                    capabilities(self.skippy)
            with self.assertRaisesRegex(ValueError, "unsupported"):
                server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, self.server)

    def test_llama_server_gets_the_same_cells(self):
        built = {"label": "reference", "kind": "llama-server", "binary": "/b/llama-server"}
        args, applied, _ = server_args(built, "", "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertEqual(args[args.index("-c") + 1], "16384")
        unpooled = dict(self.server, pool_tokens=None)
        _, applied, _ = server_args(built, "", "/m.gguf", "org/m@rev", 8192, unpooled)
        self.assertEqual(applied["total_context"], 4 * 8192)


class ReportTests(unittest.TestCase):
    record = {"started_at": "2026-11-21T10:00:00Z", "arms": [{"label": "candidate", "sha": "a" * 40}]}

    def export(self, **overrides):
        value = {"request_count": {"avg": 16}, "request_error_rate": {"avg": 0}, "error_summary": [],
                 "is_complete": True, "output_token_throughput": {"avg": 120.5},
                 "time_to_first_token": {"p50": 80, "p95": 95}}
        value.update(overrides)
        return value

    def test_measurements_reject_failed_requests_and_keep_missing_metrics_null(self):
        metrics = measurements(self.export())
        self.assertEqual(metrics["output_tok_s"], 120.5)
        self.assertIsNone(metrics["itl_ms_p50"])
        with self.assertRaises(ValueError):
            measurements(self.export(request_error_rate={"avg": 0.25}))

    def test_medians_spreads_and_verdicts(self):
        def sample(arm, value):
            return {"model": "m", "server": "s", "point": "p", "arm": arm, "metrics": {"output_tok_s": value}}

        rows = summarize([sample("baseline", v) for v in (100, 102, 98)]
                         + [sample("candidate", v) for v in (120, 118, 121)])
        delta = rows[0]["candidate_vs_baseline"]
        self.assertAlmostEqual(delta["percent"], 20.0)
        self.assertFalse(delta["ranges_overlap"])
        self.assertIn("+20.00% · Improved", markdown("t", "h", rows, self.record))
        close = summarize([sample("baseline", v) for v in (100, 110)] + [sample("candidate", v) for v in (105, 108)])
        self.assertIn("round ranges overlap", markdown("t", "h", close, self.record))

    def test_one_percent_rule_keeps_range_uncertainty_and_actual_p50_labels(self):
        def row(metric, candidate):
            return summarize([{"model": "m", "server": "s", "point": "p", "arm": arm,
                               "metrics": {metric: value}}
                              for arm, values in (("baseline", (90, 100, 110)),
                                                  ("candidate", (candidate - 10, candidate, candidate + 10)))
                              for value in values])[0]
        for metric, within, improved, regressed in (("output_tok_s", 101, 102, 98),
                                                   ("ttft_ms_p50", 99, 98, 102)):
            for value, expected in ((within, "Within tolerance"), (improved, "Improved"), (regressed, "Regressed")):
                result = performance_report.verdict(row(metric, value))
                self.assertIn(expected, result)
                self.assertIn("round ranges overlap", result)
        report_rows = [row("ttft_ms_p50", 102), row("output_tok_s", 102)]
        extra = {**report_rows[0], "server": "s2", "point": "p2"}
        extra["arms"] = {**extra["arms"], "reference": {"median": 70, "min": 70, "max": 70, "n": 3}}
        report = markdown("t", "Apple M1 Ultra", [*report_rows, extra], self.record)
        for text in ("2 regressions", "1 improvements", "Tolerance: ±1%", "Time to first token (p50)",
                     "m · s2 · p2", "Apple M1 Ultra", "Reference", "```mermaid", "## Latest numbers"):
            self.assertIn(text, report)
        for text in ("p95", "Two trials", "One Spark", "Performance over time", "M3 Ultra"):
            self.assertNotIn(text, report)
        self.assertEqual(report.count("xychart-beta"), 3)
        self.assertIn('bar [100.00000000, 102.00000000, 70.00000000]', report)
        with self.assertRaises(ValueError):
            summarize([{"model": "m", "server": "s", "point": "p", "arm": "candidate",
                        "metrics": {"output_tok_s": float("nan")}}])

    def test_different_applied_settings_keep_measurements_without_health_verdict(self):
        samples = [{"model": "m", "server": "s", "point": "p", "arm": arm,
                    "metrics": {"output_tok_s": value}, "applied": applied, "unsupported": missing}
                   for arm, value, applied, missing in (
                       ("baseline", 100, {}, ["--kv-pool-tokens"]),
                       ("candidate", 120, {"kv_pool_tokens": 16384}, []))]
        result = summarize(samples)
        self.assertEqual(set(result[0]["arms"]), {"baseline", "candidate"})
        self.assertIsNone(result[0]["candidate_vs_baseline"])
        report = markdown("test", "Mac", result, self.record)
        self.assertIn("Different settings", report)
        self.assertIn("0 improvements", report)
        self.assertIn("100.0 (", report)
        self.assertIn("120.0 (", report)

    def test_stored_rows_carry_their_arm_point_and_host_but_not_the_trace_pass(self):
        shape = performance_plan.scenario()
        server = shape["servers"][0]
        point = server["points"][0]
        measured = {"model": "qwen35-4b", "round": 2, "arm": "baseline", "server": server["id"],
                    "point": point["id"], "applied": {}, "unsupported": [],
                    "metrics": {"output_tok_s": 99.5, "ttft_ms_p50": None}}
        record = {"harness": "d" * 40, "scenario_sha256": "e" * 64, "started_at": "2026-11-21T10:00:00+00:00",
                  "host": {"chip": "Apple M1 Ultra", "node": "n1"},
                  "arms": [{"label": "baseline", "kind": "skippy", "sha": "b" * 40, "binary_sha256": "f" * 64},
                           {"label": "candidate", "kind": "skippy", "sha": "a" * 40, "binary_sha256": "f" * 64}],
                  "models": {"qwen35-4b": {"revision": "r1"}}, "samples": [measured, {**measured, "arm": "candidate"}],
                  "diagnostic": {"samples": [{**measured, "round": None}]}}
        row, pulled = performance_report.rows(record, shape, {"b" * 40: "2026-11-20T10:16:30Z"}, "job_1",
                                              {"candidate": 130, "baseline": None})
        self.assertEqual((row["sha"], row["committed_at"], row["round"]), ("b" * 40, "2026-11-20T10:16:30Z", 2))
        self.assertEqual((row["on_main"], row["pull_request"]), (True, None))
        self.assertEqual((pulled["sha"], pulled["on_main"], pulled["pull_request"]), ("a" * 40, False, 130))
        self.assertEqual((row["lanes"], row["concurrency"], row["depth"]),
                         (server["lanes"], point["concurrency"], point["depth"]))
        self.assertEqual((row["output_tok_s"], row["ttft_ms_p50"], row["chip"]), (99.5, None, "Apple M1 Ultra"))
        json.dumps(row)


if __name__ == "__main__":
    unittest.main()
