"""Offline failure evidence and verdict preservation contracts."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lib.inference_failure import LIMIT, command_evidence, failure_diagnostics, record_failure, read_evidence
from lib.inference_logs import NativeLogs
from runpy import run_path
from unittest.mock import Mock


class FailureDiagnosticsTests(unittest.TestCase):
    def test_failure_streams_and_saves_bounded_source_bound_server_and_resource_evidence(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, JIANYANG_RUN_ID="run_test"), \
                patch("lib.inference_failure.resources", return_value={"gpu_processes": {"text": "pid, memory"}}):
            output = Path(directory)
            (output / "server.log").write_text("x" * LIMIT + "CUDA initialization failed")
            stream = io.StringIO()
            original = RuntimeError("server exited")
            with redirect_stdout(stream), self.assertRaises(RuntimeError) as raised:
                with failure_diagnostics(output, "a" * 40, "model-standalone", "pr"):
                    record_failure(output, "a" * 40, "model-standalone", "pr", original, 123)
                    (output / "server.log").write_text("cleanup changed log")
                    raise original
            self.assertIs(raised.exception, original)
            report = json.loads((output / "failure-diagnostics.json").read_text())
            self.assertEqual(report["run_id"], "run_test")
            self.assertEqual(report["source_sha"], "a" * 40)
            self.assertEqual(report["server_pid"], 123)
            self.assertTrue(report["server_log_tail"]["truncated"])
            self.assertEqual(len(report["server_log_tail"]["text"]), LIMIT)
            self.assertTrue(report["server_log_tail"]["text"].endswith("CUDA initialization failed"))
            self.assertEqual(json.loads(stream.getvalue().removeprefix("Native failure diagnostics: ")), report)

    def test_success_collects_no_resources_and_disk_or_collection_failure_preserves_original_error(self):
        with TemporaryDirectory() as directory, patch("lib.inference_failure.resources") as collect:
            with failure_diagnostics(Path(directory), "sha", "model-split", "pr"):
                pass
            collect.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])
            for target, failure in [("resources", RuntimeError("collection")), ("write_json", OSError("disk"))]:
                with self.subTest(target=target), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                        patch("lib.inference_failure.resources", return_value={}), \
                        patch("lib.inference_failure." + target, side_effect=failure):
                    original = RuntimeError("original qualification failure")
                    with self.assertRaises(RuntimeError) as raised:
                        with failure_diagnostics(Path(directory), "sha", "model-split", "pr"):
                            raise original
                    self.assertIs(raised.exception, original)

    def test_execute_captures_evidence_before_cleanup_and_actions_retains_it(self):
        execute = run_path(str(Path(__file__).resolve().parents[1] / "inference-execute.py"))["execute"]
        with TemporaryDirectory() as directory, patch.dict(os.environ, JIANYANG_ARTIFACT_DIR=directory), \
                patch("lib.inference_failure.resources", return_value={"memory.max": "unlimited"}):
            output, stream = Path(directory), io.StringIO()
            server = Mock(pid=123)
            def stop(_server):
                self.assertTrue((output / "failure-diagnostics.json").exists())
            with patch.dict(execute.__globals__, {
                "authority": lambda: ("a" * 40, "GB10"),
                "plan": lambda *_: {"rows": [{"id": "fixture-standalone"}]},
                "record": Mock(), "available_memory": lambda: 10**15,
                "fixture": lambda _: {"files": [{"size": 1}], "repo": "Fixture/Model", "revision": "b" * 40},
                "admit": Mock(), "port_available": Mock(), "start": lambda *_args, **_kw: server,
                "stop": stop, "wait_for": Mock(side_effect=RuntimeError("CUDA startup exited")),
            }), redirect_stdout(stream), self.assertRaisesRegex(RuntimeError, "CUDA startup exited"):
                execute("pr", "fixture", "standalone")
            pilot = Mock()
            pilot.api.return_value = {"run_id": "run_test", "cursor": 1, "caught_up": True,
                "complete": True, "gap_at": None, "chunks": [{"seq": 1, "text": stream.getvalue()}]}
            actions = io.StringIO()
            with redirect_stdout(actions):
                NativeLogs(pilot, "fixture-standalone", output).capture(
                    {"id": "job_test", "runs": [{"id": "run_test"}]}, None, float("inf"), final=True)
            self.assertIn("CUDA startup exited", actions.getvalue())
            self.assertIn("memory.max", (output / "fixture-standalone-logs/run_test.jsonl").read_text())

    def test_proc_style_files_are_read_without_seeking(self):
        content = io.BytesIO(b"MemAvailable: 123456 kB\n")
        content.seek = Mock(side_effect=OSError("proc files cannot seek to end"))
        with patch.object(Path, "open", return_value=content):
            self.assertEqual(read_evidence(Path("/proc/meminfo"))["text"], "MemAvailable: 123456 kB\n")

    def test_command_capture_is_bounded_and_missing_tools_are_evidence(self):
        report = command_evidence([sys.executable, "-c", f"print('x' * {LIMIT * 2})"])
        self.assertEqual(report["exit_code"], 0)
        self.assertTrue(report["truncated"])
        self.assertEqual(len(report["text"]), LIMIT)
        self.assertEqual(command_evidence(["/nonexistent/skippy-diagnostic-tool"])["error"], "FileNotFoundError")


if __name__ == "__main__":
    unittest.main()
