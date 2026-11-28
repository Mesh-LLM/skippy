"""Offline loopback regressions for formation-bound split qualification."""

import json
import os
import socket
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from lib.inference_peer import PeerControl, port_available

SHA = "a" * 40


def identity(role):
    return {"formation_id": "frm_fixture", "run_id": "run_" + role, "source_sha": SHA,
            "model": "fixture", "profile": "pr", "role": role}


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        self.leader = self.stack.enter_context(PeerControl(identity("leader"), "127.0.0.1", "", port=0))
        self.worker = self.stack.enter_context(PeerControl(identity("worker"), "127.0.0.1", "", port=0))
        self.leader.peer_url = f"http://127.0.0.1:{self.worker.port}"
        self.worker.peer_url = f"http://127.0.0.1:{self.leader.port}"
        self.process = Mock(poll=Mock(return_value=None))
        self.child_log = Path(self.enterContext(TemporaryDirectory())) / "server.log"
        self.child_log.touch()

    def stage(self, port):
        return {"bind_addr": f"127.0.0.1:{port}", "stage_id": "stage-1"}

    def listener_report(self, port):
        return f"INFO skippy-serving listening: binary=127.0.0.1:{port} stage_id=stage-1 layer_range=2..4\n"

    def ready(self):
        self.worker.await_peer(seconds=1)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.child_log.write_text(self.listener_report(port))
            self.worker.worker_ready(self.process, self.child_log, self.stage(port), port=port, seconds=1)
        self.leader.await_peer("ready", seconds=1)

    def test_ready_and_completion_require_both_matching_attempts(self):
        self.ready()
        def worker():
            self.worker.await_peer("complete", self.process, seconds=3)
            self.worker.acknowledge()
            return self.worker.proof()
        with ThreadPoolExecutor(max_workers=1) as pool:
            finished = pool.submit(worker)
            self.leader.complete(self.process, seconds=3)
            proof = finished.result(timeout=4)
        self.assertEqual(self.leader.proof()["peer"], proof["local"])
        self.assertEqual(proof["peer"], self.leader.proof()["local"])
        self.assertTrue(proof["completion_acknowledged"])

    def test_live_listener_from_another_formation_source_or_model_is_rejected(self):
        self.worker.phase = "ready"
        original = dict(self.worker.identity)
        for field, value in (("formation_id", "frm_other"), ("source_sha", "b" * 40), ("model", "other")):
            with self.subTest(field=field):
                self.worker.identity = dict(original, **{field: value})
                with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                    self.leader.await_peer("ready", seconds=0.04)
                self.assertIsNone(self.leader.peer)

    def test_attempt_identity_cannot_change_after_readiness(self):
        self.ready()
        self.worker.identity = dict(self.worker.identity, run_id="run_replacement")
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            self.leader.await_peer("ready", seconds=0.04)

    def test_missing_or_wrong_completion_acknowledgement_cannot_pass(self):
        self.ready()
        with self.assertRaisesRegex(RuntimeError, "acknowledgement timed out"):
            self.leader.complete(self.process, seconds=0.04)
        self.worker.identity = dict(self.worker.identity, run_id="run_wrong")
        with self.assertRaises(OSError):
            self.worker.acknowledge()
        self.assertFalse(self.leader.ack.is_set())
        with self.assertRaisesRegex(ValueError, "acknowledged completion"):
            self.leader.proof()

    def test_exited_native_process_cannot_become_ready(self):
        with self.assertRaisesRegex(RuntimeError, "worker exited"):
            self.worker.worker_ready(Mock(poll=Mock(return_value=1)), self.child_log, self.stage(9401), seconds=0.1)

    def test_listener_taken_during_model_load_cannot_make_child_ready(self):
        with socket.socket() as unrelated:
            unrelated.bind(("127.0.0.1", 0))
            unrelated.listen(1)
            port = unrelated.getsockname()[1]
            for log in ("loading runtime\n", self.listener_report(port).replace("stage-1", "stage-0")):
                with self.subTest(log=log):
                    self.child_log.write_text(log)
                    with self.assertRaisesRegex(RuntimeError, "own bound listener"):
                        self.worker.worker_ready(self.process, self.child_log, self.stage(port), port=port, seconds=0.04)
                    self.assertEqual(self.worker.phase, "starting")

    def test_child_post_bind_report_can_arrive_in_partial_writes(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            report = self.listener_report(port)
            fragments = iter((report[:40], report[40:]))
            def poll():
                with self.child_log.open("a") as output:
                    output.write(next(fragments, ""))
                return None
            self.worker.worker_ready(Mock(poll=poll), self.child_log, self.stage(port), port=port, seconds=2)
            self.assertEqual(self.worker.phase, "ready")

    def test_existing_native_listener_is_not_admitted_as_new_worker(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            with self.assertRaises(OSError):
                port_available("127.0.0.1", listener.getsockname()[1])

    def test_control_listener_is_closed_on_failure(self):
        port = self.leader.port
        self.stack.close()
        port_available("127.0.0.1", port)

    def test_environment_requires_formation_and_run_identity(self):
        env = {"JIANYANG_FORMATION_ID": "frm_fixture", "JIANYANG_RUN_ID": "run_leader",
               "JIANYANG_ROLE": "leader", "JIANYANG_PEERS": json.dumps([
                   {"role": "leader", "host": "127.0.0.1"}, {"role": "worker", "host": "127.0.0.2"}])}
        with patch.dict(os.environ, env):
            self.assertEqual(PeerControl.from_environment(SHA, "fixture", "pr").identity, identity("leader"))
            with patch.dict(os.environ, JIANYANG_FORMATION_ID=""):
                with self.assertRaises(ValueError):
                    PeerControl.from_environment(SHA, "fixture", "pr")


class ExecuteTests(unittest.TestCase):
    def test_leader_control_is_served_during_nightly_and_release_numerical_checks(self):
        execute = run_path(str(Path(__file__).resolve().parents[1] / "inference-execute.py"))["execute"]
        for profile in ("nightly", "release"):
            with self.subTest(profile=profile), TemporaryDirectory() as tmp:
                leader = PeerControl(dict(identity("leader"), profile=profile), "127.0.0.1", "", port=0)
                worker = PeerControl(dict(identity("worker"), profile=profile), "127.0.0.1", "", port=0)
                def numerical(*_args):
                    self.assertTrue(hasattr(leader, "server"), "control must open before numerical preparation")
                    worker.peer_url = f"http://127.0.0.1:{leader.port}"
                    worker.await_peer(seconds=1)
                    self.assertEqual(worker.peer, leader.identity)
                    raise RuntimeError("stop after observing control during numerical preparation")
                with worker, patch.dict(os.environ, JIANYANG_ROLE="leader", JIANYANG_ARTIFACT_DIR=tmp), \
                     patch.dict(execute.__globals__, {
                         "authority": lambda: (SHA, "GB10"),
                         "plan": lambda *_: {"rows": [{"id": "fixture-split"}]},
                         "record": Mock(), "available_memory": lambda: 10**15,
                         "fixture": lambda _: {"files": [{"size": 1}], "repo": "Fixture/Model", "revision": "b" * 40},
                         "admit": Mock(), "prepare_split": Mock(return_value=[]), "numerical": numerical,
                         "PeerControl": Mock(from_environment=Mock(return_value=leader)),
                     }):
                    with self.assertRaisesRegex(RuntimeError, "stop after observing control"):
                        execute(profile, "fixture", "split")
                port_available("127.0.0.1", leader.port)

    def test_failed_peer_acknowledgement_does_not_publish_qualification(self):
        execute = run_path(str(Path(__file__).resolve().parents[1] / "inference-execute.py"))["execute"]
        peer = MagicMock()
        peer.__enter__.return_value = peer
        peer.__exit__.return_value = False
        peer.complete.side_effect = RuntimeError("missing acknowledgement")
        native = Mock()
        stop = Mock()
        with TemporaryDirectory() as tmp, patch.dict(os.environ, JIANYANG_ROLE="leader", JIANYANG_ARTIFACT_DIR=tmp), \
             patch.dict(execute.__globals__, {
                 "authority": lambda: (SHA, "GB10"),
                 "plan": lambda *_: {"rows": [{"id": "fixture-split"}], "performance_claim": "smoke-only"},
                 "record": Mock(), "available_memory": lambda: 10**15,
                 "fixture": lambda _: {"files": [{"size": 1}], "repo": "Fixture/Model", "revision": "b" * 40, "layers": 2},
                 "admit": Mock(), "prepare_split": Mock(return_value=[]), "port_available": Mock(),
                 "PeerControl": Mock(from_environment=Mock(return_value=peer)),
                 "start": Mock(return_value=native), "stop": stop, "wait_for": Mock(), "offload": Mock(),
                 "score": Mock(), "streaming": Mock(), "measure": Mock(),
             }):
            with self.assertRaisesRegex(RuntimeError, "missing acknowledgement"):
                execute("pr", "fixture", "split")
            self.assertFalse((Path(tmp) / "qualification.json").exists())
        stop.assert_called_once_with(native)


if __name__ == "__main__":
    unittest.main()
