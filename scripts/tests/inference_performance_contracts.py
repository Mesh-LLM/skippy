"""Offline checks for the performance comparison's plan, servers, parsing and report."""

import json
import unittest
from unittest.mock import patch

from lib import performance_plan
from lib.performance_arms import server_args
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


class PlanTests(unittest.TestCase):
    candidate, baseline, pin = "a" * 40, "b" * 40, "c" * 40

    def test_arms_are_immutable_commits_and_reference_is_optional(self):
        labels = [arm["label"] for arm in performance_plan.arms(self.candidate, self.baseline, self.pin)]
        self.assertEqual(labels, ["candidate", "baseline", "reference"])
        self.assertEqual(len(performance_plan.arms(self.candidate, self.candidate, None)), 2)
        for bad in ("main", "a" * 39):
            with self.assertRaises(ValueError):
                performance_plan.arms(bad, self.baseline, None)

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


class ServerTests(unittest.TestCase):
    skippy = {"label": "baseline", "kind": "skippy", "binary": "/b/skippy"}
    server = {"id": "lanes4-pool16k", "lanes": 4, "pool_tokens": 16384}

    def test_an_older_skippy_runs_without_settings_it_lacks_and_says_so(self):
        help_text = "  --generation-concurrency <N>\n  --adaptive-generation-concurrency\n"
        args, applied, missing = server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertIn("--generation-concurrency", args)
        self.assertEqual(applied["generation_concurrency"], 4)
        self.assertEqual(missing, ["--speculative-strategy", "--kv-pool-tokens"])

    def test_llama_server_gets_the_same_cells(self):
        built = {"label": "reference", "kind": "llama-server", "binary": "/b/llama-server"}
        args, applied, _ = server_args(built, "", "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertEqual(args[args.index("-c") + 1], "16384")
        unpooled = dict(self.server, pool_tokens=None)
        _, applied, _ = server_args(built, "", "/m.gguf", "org/m@rev", 8192, unpooled)
        self.assertEqual(applied["total_context"], 4 * 8192)


class ReportTests(unittest.TestCase):
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
        self.assertIn("+20.0% better", markdown("t", "h", rows))
        close = summarize([sample("baseline", v) for v in (100, 110)] + [sample("candidate", v) for v in (105, 108)])
        self.assertIn("within spread", markdown("t", "h", close))

    def test_stored_rows_carry_their_arm_point_and_host_but_not_the_trace_pass(self):
        shape = performance_plan.scenario()
        server = shape["servers"][0]
        point = server["points"][0]
        measured = {"model": "qwen35-4b", "round": 2, "arm": "baseline", "server": server["id"],
                    "point": point["id"], "applied": {}, "unsupported": [],
                    "metrics": {"output_tok_s": 99.5, "ttft_ms_p50": None}}
        record = {"harness": "d" * 40, "scenario_sha256": "e" * 64, "started_at": "2026-11-21T10:00:00+00:00",
                  "host": {"chip": "Apple M1 Ultra", "node": "n1"},
                  "arms": [{"label": "baseline", "kind": "skippy", "sha": "b" * 40, "binary_sha256": "f" * 64}],
                  "models": {"qwen35-4b": {"revision": "r1"}}, "samples": [measured],
                  "diagnostic": {"samples": [{**measured, "round": None}]}}
        [row] = performance_report.rows(record, shape, {"b" * 40: "2026-11-20T10:16:30Z"}, "job_1")
        self.assertEqual((row["sha"], row["committed_at"], row["round"]), ("b" * 40, "2026-11-20T10:16:30Z", 2))
        self.assertEqual((row["lanes"], row["concurrency"], row["depth"]),
                         (server["lanes"], point["concurrency"], point["depth"]))
        self.assertEqual((row["output_tok_s"], row["ttft_ms_p50"], row["chip"]), (99.5, None, "Apple M1 Ultra"))
        json.dumps(row)


if __name__ == "__main__":
    unittest.main()
