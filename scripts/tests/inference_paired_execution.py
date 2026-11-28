"""Offline native orchestration checks; these do not qualify any GPU."""

import os
import tarfile
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from lib.ci_artifacts import write_json
from lib.inference_pairs import schedule

MODULE = Path(__file__).resolve().parents[1] / "inference-paired.py"
BASELINE, CANDIDATE = "a" * 40, "b" * 40


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.module = run_path(str(MODULE))

    def test_arms_use_the_selected_build_and_restore_state_even_after_failure(self):
        run = self.module["run_arm"]
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            candidate, main, output = (root / name for name in ("candidate", "main", "evidence"))
            for directory in (candidate, main, output):
                directory.mkdir()
            previous = Path.cwd()
            environment = os.environ.copy()
            calls = []
            def execute(profile, key, mode, repeats):
                calls.append((Path.cwd(), os.environ["JIANYANG_COMMIT"], profile, key, mode, repeats))
                artifacts = Path(os.environ["JIANYANG_ARTIFACT_DIR"])
                (artifacts / "probe-1").mkdir()
                (artifacts / "probe-1/throughput-probe.json").write_text('{"raw_body":"retained"}')
                if len(calls) == 2:
                    raise RuntimeError("injected failed arm")
            with patch.dict(run.__globals__, ROOT=candidate, EXECUTE=execute):
                for index, sha in enumerate((BASELINE, CANDIDATE)):
                    design = {"experiment": "ab", "round": index, "arm": "a", "source_sha": sha}
                    if index == 0:
                        run(design, main, BASELINE, "qwen35-4b", "standalone", output)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "injected"):
                            run(design, main, BASELINE, "qwen35-4b", "standalone", output)
                    self.assertEqual(Path.cwd(), previous)
                    self.assertEqual(os.environ, environment)
                    archive_path = output / f"ab-{index}-a-evidence.tar.gz"
                    with tarfile.open(archive_path) as archive:
                        response = archive.extractfile(f"ab-{index}-a/probe-1/throughput-probe.json").read()
                    self.assertEqual(response, b'{"raw_body":"retained"}')
            self.assertEqual(calls, [(main, BASELINE, "pr", "qwen35-4b", "standalone", 3),
                                     (candidate, CANDIDATE, "pr", "qwen35-4b", "standalone", 3)])

    def test_builds_main_once_before_measurement_and_rejects_changed_fixtures(self):
        prepare = self.module["prepare"]
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate, main, output = (root / name for name in ("candidate", "main", "evidence"))
            for directory, sha in ((main, BASELINE), (candidate, CANDIDATE)):
                (directory / "ci").mkdir(parents=True)
                (directory / "target").mkdir()
                for name in ("inference-artifacts.json", "inference-quality.json"):
                    write_json(directory / "ci" / name, {})
                write_json(directory / "target/inference-build.json", {
                    "stack_commit": sha, "build_inputs": {"policy": {"cargo_profile": "release"}}})
            command = Mock()
            with patch.dict(prepare.__globals__, ROOT=candidate, command=command):
                prepare(main, BASELINE, CANDIDATE, output)
                command.assert_called_once_with(["just", "--justfile", str(candidate / "Justfile"),
                                                 "--working-directory", str(main), "inference-build"], timeout=7200)
                write_json(main / "target/inference-build.json", {
                    "stack_commit": BASELINE, "build_inputs": {"policy": {"cargo_profile": "dev"}}})
                with self.assertRaisesRegex(ValueError, "build policies"):
                    prepare(main, BASELINE, CANDIDATE, output)
                write_json(main / "ci/inference-artifacts.json", {"changed": True})
                command.reset_mock()
                with self.assertRaisesRegex(ValueError, "fixtures changed"):
                    prepare(main, BASELINE, CANDIDATE, output)
                command.assert_not_called()

    def test_legacy_rollout_does_not_relabel_a_smoke_as_a_paired_result(self):
        execute = self.module["execute"]
        native = Mock()
        with patch.dict(execute.__globals__, EXECUTE=native):
            execute("qwen35-4b", "standalone", "legacy")
        native.assert_called_once_with("pr", "qwen35-4b", "standalone")

    def test_worker_retains_candidate_peer_proof_for_the_controller(self):
        worker = self.module["worker"]
        design = {"experiment": "ab", "round": 5, "arm": "b", "source_sha": CANDIDATE}
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            arm = output / "ab-5-b"
            arm.mkdir()
            write_json(arm / "split-peer.json", {"completion_acknowledged": True})
            exchange, run_arm = Mock(return_value=True), Mock(return_value=arm)
            with patch.dict(worker.__globals__, worker_state=Mock(), await_condition=Mock(),
                            exchange=exchange, run_arm=run_arm):
                worker("http://leader", [design], output, BASELINE, "model", output, {})
            self.assertEqual((output / "split-peer.json").read_bytes(),
                             (arm / "split-peer.json").read_bytes())
            exchange.assert_called_with("http://leader", "/done", {"index": 0})

    def test_controller_keeps_one_job_for_the_entire_design_and_requires_its_receipt(self):
        module = run_path(str(MODULE.with_name("inference-dispatch.py")))
        dispatch = module["dispatch"]
        configuration = "qwen35-4b-standalone"
        pilot, launch, wait, collect, pairs = Mock(), Mock(return_value={"job_id": "job_test"}), Mock(), Mock(), Mock()
        cancel = Mock()
        with TemporaryDirectory() as temporary, patch.dict(
                dispatch.__globals__, launch=launch, wait=wait, collect=collect, collect_pairs=pairs, cancel=cancel):
            output = Path(temporary)
            dispatch(pilot, "pr", CANDIDATE, output, configuration, baseline=BASELINE)
            launch.assert_called_once()
            body = launch.call_args.args[2]
            self.assertEqual(body["ref"], CANDIDATE)
            self.assertEqual(body["inputs"]["baseline_sha"], BASELINE)
            self.assertEqual(len(schedule(BASELINE, CANDIDATE)), 24)
            pairs.assert_called_once()
            pairs.side_effect = ValueError("missing paired evidence")
            with self.assertRaisesRegex(ValueError, "missing paired evidence"):
                dispatch(pilot, "pr", CANDIDATE, output, configuration, baseline=BASELINE)
            cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
