"""Offline statistical and evidence-admission regressions for paired inference."""

from copy import deepcopy
import math
from pathlib import Path
import os
from runpy import run_path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from lib.ci_artifacts import read_json
from lib.inference_pairs import PROTOCOL, REPETITIONS, ROUNDS, schedule, summarize
from lib.inference_paired_collect import collect_pairs
from lib.inference_rendezvous import Coordinator, exchange

CANDIDATE, BASELINE = "a" * 40, "b" * 40
CONFIGURATION = "qwen35-4b-standalone"


def evidence(ratio=1.1, aa_ratios=None, ab_ratios=None):
    placements = [{"role": "leader", "node": "node-a", "hostname": "host-a"}]
    builds = [{"role": "leader", "main": {"stack_commit": BASELINE, "cli_sha256": "c" * 64},
               "candidate": {"stack_commit": CANDIDATE, "cli_sha256": "d" * 64}}]
    data = {"schema_version": 1, "protocol": PROTOCOL, "source_sha": CANDIDATE,
            "baseline_sha": BASELINE, "configuration": CONFIGURATION, "rounds": ROUNDS,
            "repetitions": REPETITIONS, "placements": placements, "builds": builds, "runs": []}
    for design in schedule(BASELINE, CANDIDATE):
        ratios = aa_ratios if design["experiment"] == "aa" else ab_ratios
        gain = (ratios[design["round"]] if ratios else
                1 if design["experiment"] == "aa" else ratio) if design["arm"] == "b" else 1
        samples = [{"shape": shape, "prefill_tokens": 200 + shape, "prefill_ms": 100 / gain,
                    "decode_tokens": 128, "decode_ms": 200 / gain}
                   for _ in range(REPETITIONS) for shape in range(3)]
        data["runs"].append({**design, "placements": deepcopy(placements),
                             "quality": "passed", "samples": samples})
    return data


def summary(data):
    return summarize(data, CANDIDATE, BASELINE, CONFIGURATION)


class PairedTests(unittest.TestCase):
    def test_balanced_order_and_identical_source_for_noise_calibration(self):
        design = schedule(BASELINE, CANDIDATE)
        self.assertEqual(len(design), 24)
        self.assertEqual([d["arm"] for d in design[:8]], list("abbaabba"))
        self.assertTrue(all(d["source_sha"] == BASELINE for d in design[:12]))
        self.assertEqual(sum(d["source_sha"] == CANDIDATE for d in design), 6)

    def test_detects_both_signs_per_shape_without_treating_nested_requests_as_rounds(self):
        for ratio, verdict in ((1.1, "improved"), (0.9, "regressed"), (1, "inconclusive")):
            for row in summary(evidence(ratio)):
                for phase in ("prefill", "decode"):
                    self.assertAlmostEqual(row[phase]["ratio"], ratio)
                    self.assertEqual(row[phase]["verdict"], verdict)
        # Known df=5 Student-t interval on six log ratios: mean zero, stdev sqrt(28/5).
        logs = [-3, -2, -1, 1, 2, 3]
        data = evidence(ab_ratios=[math.exp(x / 100) for x in logs])
        item = summary(data)[0]["decode"]
        margin = 2.570581835636305 * math.sqrt(28 / 5) / math.sqrt(6) / 100
        self.assertAlmostEqual(item["ci95"][0], math.exp(-margin))
        self.assertAlmostEqual(item["ci95"][1], math.exp(margin))
        self.assertEqual(item["verdict"], "inconclusive")

    def test_noise_or_systematic_order_bias_blocks_a_performance_verdict(self):
        for noise in ([0.9, 1.1] * 3, [1.02] * 6):
            item = summary(evidence(1.2, aa_ratios=noise))[0]["prefill"]
            self.assertFalse(item["calibrated"])
            self.assertEqual(item["verdict"], "inconclusive")
        item = summary(evidence(1.001, aa_ratios=[0.99, 1.01] * 3))[0]["prefill"]
        self.assertTrue(item["calibrated"])
        self.assertEqual(item["verdict"], "inconclusive")

    def test_rejects_partial_reordered_changed_hardware_quality_or_workload(self):
        changes = [lambda d: d["runs"].pop(),
                   lambda d: d["runs"].reverse(),
                   lambda d: d["runs"][0].update(source_sha=CANDIDATE),
                   lambda d: d["runs"][0].update(placements=[]),
                   lambda d: d["runs"][0].update(quality="failed"),
                   lambda d: d["runs"][0]["samples"].pop(),
                   lambda d: d["runs"][0]["samples"][0].update(prefill_tokens=201),
                   lambda d: d["runs"][0]["samples"][0].update(decode_tokens=127),
                   lambda d: d["runs"][0]["samples"][0].update(decode_ms=float("nan")),
                   lambda d: d.update(rounds=True),
                   lambda d: d.update(baseline_sha=CANDIDATE)]
        for change in changes:
            data = evidence()
            change(data)
            with self.subTest(change=change), self.assertRaises(ValueError):
                summary(data)

    def test_controller_recomputes_native_summary_and_binds_it_to_the_job(self):
        data = evidence()
        data["summary"] = "forged result"
        job = {"id": "job_test", "repo_rev": CANDIDATE, "status": "succeeded",
               "runs": [{"id": "run_test", "status": "succeeded"}],
               "machine": {"host": {"hostname": "host-a"}}}
        pilot = Mock()
        pilot.api.return_value = data
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            collect_pairs(pilot, job, {"id": CONFIGURATION, "mode": "standalone"},
                          CANDIDATE, BASELINE, output)
            receipt = read_json(output / f"{CONFIGURATION}-paired.json")
            self.assertEqual(receipt["summary"][0]["decode"]["verdict"], "improved")
            self.assertEqual(receipt["run_id"], "run_test")
            job["machine"]["host"]["hostname"] = "other-host"
            with self.assertRaisesRegex(ValueError, "leader differs"):
                collect_pairs(pilot, job, {"id": CONFIGURATION, "mode": "standalone"},
                              CANDIDATE, BASELINE, output)

    def test_split_collector_rejects_a_different_worker_even_with_valid_rates(self):
        data = evidence()
        configuration = "qwen3-coder-30b-a3b-split"
        data["configuration"] = configuration
        data["placements"].append({"role": "worker", "node": "node-b", "hostname": "host-b"})
        data["builds"].append({**deepcopy(data["builds"][0]), "role": "worker"})
        for run in data["runs"]:
            run["placements"] = deepcopy(data["placements"])
        job = {"id": "job_test", "repo_rev": CANDIDATE, "status": "succeeded", "formation_id": "frm_test",
               "runs": [{"id": "run_test", "status": "succeeded"}],
               "machine": {"host": {"hostname": "host-a"}}}
        formation = {"status": "succeeded", "members": [
            {"role": "leader", "node": "node-a", "status": "succeeded"},
            {"role": "worker", "node": "node-b", "status": "succeeded"}]}
        pilot = Mock()
        with TemporaryDirectory() as temporary:
            for valid in (True, False):
                formation["members"][1]["node"] = "node-b" if valid else "replacement-node"
                pilot.api.side_effect = [data, formation]
                args = (pilot, job, {"id": configuration, "mode": "split"}, CANDIDATE, BASELINE, Path(temporary))
                if valid:
                    collect_pairs(*args)
                else:
                    with self.assertRaisesRegex(ValueError, "formation"):
                        collect_pairs(*args)

    def test_rendezvous_requires_worker_completion_and_propagates_failure(self):
        with Coordinator("127.0.0.1", port=0) as coordinator:
            origin = f"http://127.0.0.1:{coordinator.server.server_address[1]}"
            exchange(origin, "/ready", {"host": "worker"})
            self.assertEqual(coordinator.wait(lambda s: s["worker"]), {"host": "worker"})
            coordinator.state["index"] = 2
            self.assertEqual(exchange(origin, "/state")["index"], 2)
            exchange(origin, "/done", {"index": 2})
            self.assertTrue(coordinator.wait(lambda s: s["done"] == 2))
            exchange(origin, "/error", {"failed": True})
            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                coordinator.wait(lambda s: s["done"] == 3)

    def test_admission_pins_current_main_instead_of_selecting_a_successful_nightly(self):
        module = run_path(str(Path(__file__).resolve().parents[1] / "inference-plan.py"))
        admit = module["admit"]
        event = {"pull_request": {"head": {"sha": CANDIDATE}}}
        github = Mock(return_value={"object": {"sha": BASELINE}})
        output = Mock()
        with patch.dict(os.environ, GITHUB_EVENT_NAME="pull_request_target", GITHUB_EVENT_PATH="event.json"), \
                patch.dict(admit.__globals__, read_json=Mock(return_value=event), admit_pr=Mock(),
                           github=github, output=output):
            admit()
        github.assert_called_once_with("repos/Mesh-LLM/skippy/git/ref/heads/main")
        output.assert_called_once_with(source=CANDIDATE, profile="pr", baseline=BASELINE)

        # A manual PR-profile run on a branch must not call that branch "main".
        github.reset_mock()
        output.reset_mock()
        with patch.dict(os.environ, GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_EVENT_PATH="event.json",
                        GITHUB_SHA=CANDIDATE, QUALIFICATION_PROFILE="pr"), \
                patch.dict(admit.__globals__, read_json=Mock(return_value={}), github=github, output=output):
            admit()
        github.assert_called_once_with("repos/Mesh-LLM/skippy/git/ref/heads/main")
        output.assert_called_once_with(source=CANDIDATE, profile="pr", baseline=BASELINE)


if __name__ == "__main__":
    unittest.main()
