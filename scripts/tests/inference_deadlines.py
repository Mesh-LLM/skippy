"""Bound queueing separately from native PR execution without lab access."""

import io
import json
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.dispatch = run_path(str(ROOT / "scripts/inference-dispatch.py"))["dispatch"]
        self.now = 0
        self.enterContext(patch("time.monotonic", side_effect=lambda: self.now))
        self.enterContext(patch("time.sleep", side_effect=self.advance))
        self.enterContext(patch("sys.stdout", io.StringIO()))
        self.pilot = Mock()
        self.cleanup = Mock()
        self.launch = Mock(return_value={"job_id": "job_leader"})
        self.row = {"id": "fixture-standalone", "model": "Fixture/Model", "mode": "standalone"}
        self.rows = [self.row]
        self.starts = 4920
        self.ends = 6000
        self.status = "succeeded"
        self.deadlines = []
        self.pilot.api.side_effect = self.read
        self.enterContext(patch.dict(self.dispatch.__globals__, {
            "plan": lambda *_: {"rows": self.rows}, "launch": self.launch,
            "cancel": self.cleanup, "collect": Mock(), "collect_pairs": Mock(), "NativeLogs": Mock(),
        }))

    def advance(self, seconds):
        self.now += seconds

    def read(self, _method, path, *, status_deadline):
        self.deadlines.append(status_deadline)
        if path.startswith("/v1/formations/"):
            return {"status": self.status if self.now >= self.ends else "running",
                    "members": [{"role": "worker", "status": "running"
                                 if self.now >= self.starts else "queued"}]}
        status = self.status if self.now >= self.ends else (
            "running" if self.now >= self.starts else "queued")
        job = {"id": "job_leader", "status": status, "repo_rev": SHA}
        if self.row["mode"] == "split":
            job["formation_id"] = "frm_pair"
            # The worker can start before the leader's next claim.
            if self.now < self.starts + 300:
                job["status"] = "queued"
        return job

    def run_dispatch(self, profile="pr", all_rows=False):
        with TemporaryDirectory() as tmp:
            self.dispatch(self.pilot, profile, SHA, Path(tmp), None if all_rows else self.row["id"],
                          baseline="b" * 40 if profile == "pr" else None)

    def test_long_queue_does_not_consume_native_execution_budget(self):
        self.run_dispatch()
        self.assertEqual(self.now, 6000)
        self.cleanup.assert_not_called()
        self.launch.assert_called_once()

    def test_each_configuration_gets_a_fresh_queue_budget_after_prior_execution(self):
        self.rows.append({**self.row, "id": "fixture-other-standalone"})
        def launch(*_args):
            if self.now >= 6000:
                self.starts, self.ends = 10920, 12000
            return {"job_id": "job_leader"}
        self.launch.side_effect = launch
        self.run_dispatch(all_rows=True)
        self.assertEqual(self.now, 12000)
        self.assertEqual(self.launch.call_count, 2)
        self.cleanup.assert_not_called()

    def test_split_budget_starts_with_first_member_and_never_resets(self):
        self.row.update(id="fixture-split", mode="split")
        self.ends = float("inf")
        with self.assertRaisesRegex(RuntimeError, "timed out during native execution"):
            self.run_dispatch()
        self.assertEqual(self.now, self.starts + 14460)
        self.assertEqual(max(self.deadlines), self.starts + 14460)
        self.cleanup.assert_called_once()

    def test_queue_without_any_execution_remains_bounded(self):
        self.starts = self.ends = float("inf")
        with self.assertRaisesRegex(RuntimeError, "timed out waiting for native execution"):
            self.run_dispatch()
        self.assertEqual(self.now, 5400)
        self.cleanup.assert_called_once()

    def test_stalled_running_job_remains_bounded(self):
        self.starts, self.ends = 0, float("inf")
        with self.assertRaisesRegex(RuntimeError, "timed out during native execution"):
            self.run_dispatch()
        self.assertEqual(self.now, 14460)
        self.cleanup.assert_called_once()

    def test_native_failure_is_not_retried_or_converted_to_success(self):
        self.starts, self.ends, self.status = 0, 15, "failed"
        with self.assertRaisesRegex(RuntimeError, "fixture-standalone failed: failed"):
            self.run_dispatch()
        self.assertEqual(self.now, 15)
        self.launch.assert_called_once()
        self.cleanup.assert_called_once()

    def test_non_pr_profiles_keep_total_deadline(self):
        for profile in ("nightly", "release"):
            with self.subTest(profile=profile):
                self.now, self.starts, self.ends = 0, 20000, float("inf")
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    self.run_dispatch(profile)
                self.assertEqual(self.now, 21000)

    def test_execution_budget_matches_native_pr_templates_and_controller_headroom(self):
        templates = json.loads((ROOT / ".gi/templates.yaml").read_text())["templates"]
        budgets = [t["budget"] for t in templates if t["key"].startswith("inference-pr-")]
        self.assertEqual(len(budgets), 2)
        self.assertTrue(all(b["max_duration_s"] + b["grace_s"] == 14460 for b in budgets))
        self.assertIn("matrix.profile == 'pr' && 340 || 360",
                      (ROOT / ".github/workflows/inference.yml").read_text())
        self.assertGreater(340 * 60 - 5400 - 14460, 5 * 60)


if __name__ == "__main__":
    unittest.main()
