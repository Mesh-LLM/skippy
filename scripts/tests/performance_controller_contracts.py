"""Failure-path contracts for controlled comparison submission and evidence."""

import json
import subprocess
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from lib.ci_artifacts import read_json, write_json

SCRIPT = Path(__file__).resolve().parents[1] / "inference-performance.py"


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.functions = run_path(str(SCRIPT))
        self.directory = self.enterContext(TemporaryDirectory())
        self.root = Path(self.directory)

    def test_cancel_continues_after_subprocess_auth_failure(self):
        ledger = self.root / "jobs.json"
        write_json(ledger, ["job_a", "job_b"])
        pilot = Mock()
        pilot.api.side_effect = [subprocess.CalledProcessError(1, "ssh-keygen"), {"status": "queued"}, {}]
        with self.assertRaisesRegex(RuntimeError, "job_a"):
            self.functions["cancel"](pilot, ledger)
        self.assertEqual(pilot.api.call_args.args, ("POST", "/v1/jobs/job_b/cancel"))

    def test_pending_submission_replays_same_key_after_accepted_response_is_lost(self):
        dispatch = self.functions["dispatch"]
        write_json(self.root / "plan.json", {"jobs": [{"spec": {"name": "test"}, "label": "host"}]})
        for failure in (KeyboardInterrupt("cancelled"), RuntimeError("lost response")):
            with self.subTest(failure=type(failure).__name__):
                pilot = Mock()
                pilot.api.side_effect = [failure, {"id": "job_a"}, {"status": "queued"}, {}]
                with patch.dict(dispatch.__globals__, Pilot=lambda: pilot):
                    with self.assertRaises(type(failure)):
                        dispatch(self.root)
                calls = pilot.api.call_args_list
                self.assertEqual(calls[0].args, calls[1].args)
                self.assertEqual(calls[-1].args, ("POST", "/v1/jobs/job_a/cancel"))
                self.assertIsNone(read_json(self.root / "jobs.json")[0]["id"])

    def test_subprocess_cleanup_failure_preserves_original_dispatch_error(self):
        dispatch = self.functions["dispatch"]
        write_json(self.root / "plan.json", {"jobs": [{"spec": {}, "label": "host"}]})
        pilot = Mock(); pilot.api.side_effect = RuntimeError("original")
        with patch.dict(dispatch.__globals__, Pilot=lambda: pilot,
                        cancel=Mock(side_effect=subprocess.TimeoutExpired("ssh-keygen", 5))):
            with self.assertRaisesRegex(RuntimeError, "original"):
                dispatch(self.root)

    def test_failed_ledger_replace_retains_pending_submission_for_recovery(self):
        save = self.functions["save_ledger"]
        ledger = self.root / "jobs.json"
        save(ledger, [{"id": None, "idempotency_key": "same-key", "spec": {}}])
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                save(ledger, [{"id": "job_a"}])
        self.assertIsNone(read_json(ledger)[0]["id"])

    def test_collection_failure_keeps_later_reports_and_writes_summary(self):
        dispatch = self.functions["dispatch"]
        write_json(self.root / "plan.json", {"pull_requests": {}, "jobs": [
            {"spec": {}, "label": "first"}, {"spec": {}, "label": "second"}]})
        pilot = Mock(); pilot.api.side_effect = [{"id": "job_a"}, {"id": "job_b"}]
        collect = Mock(side_effect=[OSError("missing artifact"), "second report retained"])
        with patch.dict(dispatch.__globals__, Pilot=lambda: pilot,
                        wait=Mock(return_value={"status": "succeeded"}), collect=collect):
            with self.assertRaisesRegex(RuntimeError, "first: collection failed"):
                dispatch(self.root)
        self.assertEqual(collect.call_count, 2)
        summary = (self.root / "summary.md").read_text()
        self.assertIn("second report retained", summary)
        self.assertIn("first: collection failed: missing artifact", summary)

    def test_successful_collection_downloads_raw_aiperf_evidence(self):
        collect = self.functions["collect"]
        result = {"harness": "h", "arms": [], "samples": [], "host": {"chip": "Mac"}}
        pilot = Mock(); pilot.api.return_value = result
        download = Mock()
        plan = {"harness": "h", "arms": [], "trace": False, "committed_at": {}, "pull_requests": {}}
        with patch.dict(collect.__globals__, download=download, rows=lambda *_: [], markdown=Mock(return_value="report")):
            collect(pilot, {"id": "job_a", "runs": [{"id": "run_a"}]}, plan, "host", self.root)
        download.assert_called_once_with(pilot, "run_a", "aiperf.tar.gz", self.root / "host-aiperf.tar.gz")

    def test_optional_trace_failure_does_not_prevent_later_models_being_measured(self):
        execute = self.functions["execute"]
        built = [{"label": label, "sha": label, "binary": label, "binary_sha256": "digest"}
                 for label in ("candidate", "baseline")]
        plan = {"harness": "h", "arms": built, "models": ["m1", "m2"], "rounds": 1, "trace": True}
        shape = {"servers": [{"id": "s"}]}
        fixture = {"repo": "org/model", "revision": "r", "files": [{"size": 1}]}
        calls = []
        def measure(_entry, _help, _model, _inputs, key, index, *_args, **_kwargs):
            calls.append((key, index))
            if index is None:
                raise RuntimeError("trace failed")
            return [{"model": key, "round": index + 1}]
        overrides = dict(native_plan=lambda: plan, read_json=lambda _: built, digest=lambda _: "digest",
                         capture=lambda _: "mock", capabilities=lambda _: "help", scenario=lambda: shape,
                         fixture=lambda _: fixture, admit_model=lambda *_: "model", tokenizer=lambda _: "tokenizer",
                         measure=measure, WORK=self.root / "work", TRACES=self.root / "traces")
        with patch.dict(execute.__globals__, **overrides), patch("os.geteuid", return_value=1000), \
                patch.dict("os.environ", JIANYANG_ARTIFACT_DIR=str(self.root)), \
                patch("lib.inference_metal.chip", return_value="Mac"), \
                patch("lib.inference_metal.working_set", return_value=100 * 1024**3), \
                patch("lib.inference_metal.available_memory", return_value=100 * 1024**3):
            execute()
        self.assertEqual(calls, [("m1", 0), ("m1", 0), ("m2", 0), ("m2", 0), ("m1", None), ("m2", None)])
        result = read_json(self.root / "performance.json")
        self.assertEqual(len(result["samples"]), 4)
        self.assertEqual(len(result["diagnostic"]["failures"]), 2)
        self.assertTrue((self.root / "traces.tar.gz").is_file())


if __name__ == "__main__":
    unittest.main()
