"""Offline native log forwarding and qualification verdict regressions."""

import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from lib.inference_logs import NativeLogs
from lib.jy_qualification import Pilot


def page(run, start, texts, complete=False, caught_up=True):
    return {"run_id": run, "cursor": start + len(texts), "complete": complete,
            "caught_up": caught_up, "gap_at": None,
            "chunks": [{"seq": start + i + 1, "text": text, "stream": "out"}
                       for i, text in enumerate(texts)]}


class LogTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        self.output = Path(self.directory)
        self.pilot = Mock()
        self.logs = NativeLogs(self.pilot, "model-split", self.output)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.enterContext(redirect_stdout(self.stdout))
        self.enterContext(redirect_stderr(self.stderr))
        self.now = 0
        self.enterContext(patch("lib.inference_logs.time.monotonic", side_effect=lambda: self.now))
        self.enterContext(patch("lib.inference_logs.time.sleep", side_effect=self.advance))

    def advance(self, seconds):
        self.now += seconds

    def test_live_pages_cursor_deduplication_and_terminal_tail(self):
        self.pilot.api.side_effect = [page("run_a", 0, ["build"], caught_up=False),
                                      page("run_a", 1, ["test"]),
                                      page("run_a", 2, ["done"], complete=True)]
        job = {"id": "job_a", "runs": [{"id": "run_a"}]}
        self.logs.capture(job, None, 100)
        self.assertIn("build", self.stdout.getvalue())  # Visible before completion.
        self.logs.capture(job, None, 100, final=True)
        self.logs.capture(job, None, 100, final=True)
        self.assertEqual([call.args[1] for call in self.pilot.api.call_args_list], [
            f"/v1/runs/run_a/logs/full?since={cursor}&limit=500" for cursor in (0, 1, 2)])
        saved = self.output / "model-split-logs/run_a.jsonl"
        self.assertEqual([json.loads(line)["text"] for line in saved.read_text().splitlines()],
                         ["build", "test", "done"])
        self.assertEqual(self.stdout.getvalue().count("done"), 1)

    def test_split_members_and_all_attempts_have_separate_cursors(self):
        job = {"id": "job_a", "runs": [{"id": "run_a"}, {"id": "run_b"}]}
        formation = {"members": [{"job_id": "job_a"}, {"job_id": "job_b"}]}
        worker = {"id": "job_b", "runs": [{"id": "run_c"}]}
        self.pilot.api.side_effect = [worker] + [page(run, 0, [run], complete=True)
                                                for run in ("run_a", "run_b", "run_c")]
        self.logs.capture(job, formation, 100, final=True)
        self.assertEqual(len(list((self.output / "model-split-logs").glob("*.jsonl"))), 3)
        self.assertIn("/leader/run_a]", self.stdout.getvalue())
        self.assertIn("/worker/run_c]", self.stdout.getvalue())

    def test_untrusted_output_cannot_emit_actions_commands(self):
        text = "hello\n::error::forged\r::add-mask::hidden\n\x1b[2K::stop-commands::x\n"
        self.pilot.api.return_value = page("run_a", 0, [text], complete=True)
        self.logs.drain("run_a", "worker", 100)
        lines = self.stdout.getvalue().splitlines()
        self.assertRegex(lines[0], r"^::stop-commands::[a-f0-9]{32}$")
        self.assertEqual(lines[-1], "::" + lines[0].split("::")[-1] + "::")
        self.assertTrue(all(line.startswith("[model-split/worker/run_a] ") for line in lines[1:-1]))
        self.assertNotIn("\x1b", self.stdout.getvalue())
        self.assertNotIn("\r", self.stdout.getvalue())
        saved = self.output / "model-split-logs/run_a.jsonl"
        self.assertEqual(json.loads(saved.read_text())["text"], text)

    def test_invalid_pages_do_not_advance_or_write_logs(self):
        for invalid in ({"gap_at": 1}, {"cursor": 0}, {"run_id": "run_other"},
                        {"chunks": [{"seq": 2, "text": "bad"}]}):
            self.pilot.api.return_value = {**page("run_a", 0, ["text"]), **invalid}
            self.logs.capture({"id": "job_a", "runs": [{"id": "run_a"}]}, None, 100)
            self.assertEqual(self.logs.cursors, {})
            self.assertFalse((self.output / "model-split-logs/run_a.jsonl").exists())
        self.assertIn("incomplete", (self.output / "model-split-logs/diagnostics.txt").read_text())

    def test_read_failure_preserves_cursor_for_next_poll(self):
        self.pilot.api.side_effect = [TimeoutError(), page("run_a", 0, ["recovered"])]
        job = {"id": "job_a", "runs": [{"id": "run_a"}]}
        self.logs.capture(job, None, 100)
        self.logs.capture(job, None, 100)
        self.assertEqual(self.logs.cursors, {"run_a": 1})
        self.assertIn("recovered", self.stdout.getvalue())

    def test_final_drain_waits_for_late_tail(self):
        self.pilot.api.side_effect = [page("run_a", 0, ["first"]),
                                      page("run_a", 1, ["late"], complete=True)]
        with patch("lib.inference_logs.time.sleep") as sleep:
            self.logs.capture({"id": "job_a", "runs": [{"id": "run_a"}]}, None, 100, final=True)
        sleep.assert_called_once_with(2)
        self.assertIn("late", self.stdout.getvalue())

    def test_final_drain_deadline_reports_incomplete_capture(self):
        self.pilot.api.return_value = page("run_a", 0, [])
        self.logs.capture({"id": "job_a", "runs": [{"id": "run_a"}]}, None, 100, final=True)
        self.assertEqual(self.now, 30)
        self.assertIn("deadline reached", self.stderr.getvalue())
        self.assertIn("run_a", self.stderr.getvalue())
        self.assertNotIn("run_a.jsonl", self.stderr.getvalue())

    def test_busy_run_yields_to_other_attempts_and_next_status_poll(self):
        cursors = iter(range(20))

        def read(_method, path, *, status_deadline):
            self.assertLessEqual(status_deadline, self.now + 5)
            if "run_busy/" in path:
                self.advance(1)
                return page("run_busy", next(cursors), ["busy"], caught_up=False)
            return page("run_other", 0, ["other"], complete=True)

        self.pilot.api.side_effect = read
        job = {"id": "job_a", "runs": [{"id": "run_busy"}, {"id": "run_other"}]}
        self.logs.capture(job, None, 5400)
        self.assertEqual(self.now, 5)
        self.assertEqual(self.logs.cursors, {"run_busy": 5, "run_other": 1})
        self.assertIn("run_busy: log drain deadline reached", self.stderr.getvalue())
        self.logs.capture(job, None, 5400)
        self.assertEqual(self.logs.cursors["run_busy"], 10)
        self.assertEqual(self.stdout.getvalue().count("] other"), 1)

    def test_unexpected_run_error_does_not_block_other_attempts(self):
        self.pilot.api.side_effect = [AttributeError("do not expose response"),
                                      page("run_b", 0, ["other"], complete=True)]
        job = {"id": "job_a", "runs": [{"id": "run_a"}, {"id": "run_b"}]}
        self.logs.capture(job, None, 100)
        self.assertEqual(self.logs.cursors, {"run_b": 1})
        self.assertIn("AttributeError", self.stderr.getvalue())
        self.assertNotIn("do not expose response", self.stderr.getvalue())

    def test_dead_worker_is_bounded_and_leader_logs_are_retained(self):
        def read(_method, path, *, status_deadline):
            if path == "/v1/jobs/job_b":
                self.advance(status_deadline - self.now)
                raise TimeoutError()
            return page("run_a", 0, ["leader tail"], complete=True)

        self.pilot.api.side_effect = read
        job = {"id": "job_a", "runs": [{"id": "run_a"}]}
        formation = {"members": [{"job_id": "job_a"}, {"job_id": "job_b"}]}
        self.logs.capture(job, formation, 100, final=True)
        self.assertEqual(self.now, 30)
        self.assertIn("leader tail", self.stdout.getvalue())
        diagnostics = (self.logs.output / "diagnostics.txt").read_text()
        self.assertIn("job_b: TimeoutError; log capture incomplete", diagnostics)
        self.assertIn("deadline reached", diagnostics)

    def test_log_failure_never_replaces_success_or_failure_verdict(self):
        wait = run_path(str(Path(__file__).resolve().parents[1] / "inference-dispatch.py"))["wait"]
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                self.now = 0
                job = {"id": "job_a", "status": status, "repo_rev": "a" * 40,
                       "runs": [{"id": "run_a"}]}
                self.pilot.api.side_effect = lambda _method, path, **_: (
                    job if path == "/v1/jobs/job_a" else None)
                if status == "failed":
                    with self.assertRaisesRegex(RuntimeError, "model-split failed: failed"):
                        wait(self.pilot, "job_a", "model-split", "a" * 40, self.output, 100)
                else:
                    self.assertEqual(wait(self.pilot, "job_a", "model-split", "a" * 40,
                                          self.output, 100), job)
                self.assertEqual(self.now, 30)

    def test_diagnostic_disk_failure_does_not_escape(self):
        self.pilot.api.side_effect = AttributeError()
        with patch.object(Path, "mkdir", side_effect=PermissionError()):
            self.logs.capture({"id": "job_a", "runs": [{"id": "run_a"}]}, None, 100)
        self.assertIn("cannot retain diagnostics", self.stderr.getvalue())

    def test_cancellation_is_not_swallowed(self):
        self.pilot.api.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.logs.capture({"id": "job_a", "runs": [{"id": "run_a"}]}, None, 100)

    def test_log_socket_timeout_uses_run_budget_not_qualification_budget(self):
        pilot = Pilot()
        logs = NativeLogs(pilot, "timeout", self.output)

        def timeout(_request, *, timeout):
            self.advance(timeout)
            raise TimeoutError()

        with patch.object(pilot, "auth", return_value="test"), \
                patch.object(pilot.opener, "open", side_effect=timeout) as send:
            logs.capture({"id": "job_a", "runs": [{"id": "run_a"}]}, None, 5400)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["timeout"], 5)
        self.assertEqual(self.now, 5)


    def test_invalid_run_identity_is_not_used_in_requests_or_paths(self):
        self.logs.capture({"runs": [{"id": "../../outside"}]}, None, 100)
        self.pilot.api.assert_not_called()

    def test_failed_qualification_emits_final_logs_before_raising(self):
        wait = run_path(str(Path(__file__).resolve().parents[1] / "inference-dispatch.py"))["wait"]
        job = {"id": "job_a", "status": "failed", "runs": [{"id": "run_a"}]}
        self.pilot.api.side_effect = [job, page("run_a", 0, ["compiler failed"], complete=True)]
        with self.assertRaisesRegex(RuntimeError, "model-split failed: failed"):
            wait(self.pilot, "job_a", "model-split", "a" * 40, self.output, 100)
        self.assertIn("compiler failed", self.stdout.getvalue())
        self.assertTrue((self.output / "model-split-job.json").exists())


if __name__ == "__main__":
    unittest.main()
