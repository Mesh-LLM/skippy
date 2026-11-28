"""Reject successful-looking split jobs with unrelated peer evidence."""

import copy
import json
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from lib.inference_peer_evidence import verify_split_peer
from inference_peer import identity, SHA


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.output = Path(self.enterContext(TemporaryDirectory()))
        self.row = {"id": "fixture-split", "mode": "split", "model": "Fixture/Model"}
        self.jobs = {}
        members = []
        for role in ("leader", "worker"):
            self.jobs[role] = {"id": "job_" + role, "formation_id": "frm_fixture", "repo_rev": SHA,
                               "status": "succeeded", "runs": [{"id": "run_" + role, "status": "succeeded", "machine_node": "node_" + role,
                                   "started_at": "2026-11-28T01:00:00Z", "ended_at": "2026-11-28T01:05:00Z"}]}
            members.append({"job_id": "job_" + role, "role": role, "status": "succeeded",
                            "node": "node_" + role, "run_id": "run_" + role})
        self.formation = {"id": "frm_fixture", "status": "succeeded", "members": members}
        self.worker_proof = {"local": identity("worker"), "peer": identity("leader"), "completion_acknowledged": True}
        self.qualification = {"source_sha": SHA, "model": "fixture", "profile": "pr", "mode": "split", "status": "passed",
                              "split_peer": {"local": identity("leader"), "peer": identity("worker"), "completion_acknowledged": True}}
        self.pilot = Mock()
        self.pilot.api.side_effect = self.read

    def read(self, _method, path):
        if path == "/v1/jobs/job_worker":
            return self.jobs["worker"]
        if path.endswith("qualification.json"):
            return self.qualification
        if path.endswith("split-peer.json"):
            return self.worker_proof
        raise AssertionError(path)

    def verify(self):
        (self.output / "fixture-split-formation.json").write_text(json.dumps(self.formation))
        verify_split_peer(self.pilot, self.jobs["leader"], self.row, SHA, "pr", self.output)

    def test_matching_successful_formation_passes_and_retains_both_proofs(self):
        self.verify()
        receipt = json.loads((self.output / "fixture-split-split-peer.json").read_text())
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["worker"], self.worker_proof)

    def test_wrong_formation_source_or_attempt_in_either_peer_proof_fails(self):
        for field, value in (("formation_id", "frm_other"), ("source_sha", "b" * 40), ("run_id", "run_wrong")):
            for producer in ("leader", "worker"):
                with self.subTest(field=field, producer=producer):
                    original = copy.deepcopy(self.qualification if producer == "leader" else self.worker_proof)
                    proof = self.qualification["split_peer"] if producer == "leader" else self.worker_proof
                    proof["peer"][field] = value
                    with self.assertRaisesRegex(ValueError, "peer evidence"):
                        self.verify()
                    if producer == "leader":
                        self.qualification = original
                    else:
                        self.worker_proof = original

    def test_missing_ack_or_legacy_proof_cannot_pass(self):
        for proof in (None, {}, {**self.qualification["split_peer"], "completion_acknowledged": False}):
            self.qualification["split_peer"] = proof
            with self.assertRaisesRegex(ValueError, "peer evidence"):
                self.verify()

    def test_leader_finishing_before_own_worker_starts_is_rejected(self):
        self.jobs["leader"]["runs"][0]["ended_at"] = "2026-11-28T01:42:16Z"
        self.jobs["worker"]["runs"][0]["started_at"] = "2026-11-28T01:45:13Z"
        self.jobs["worker"]["runs"][0]["ended_at"] = "2026-11-28T01:50:00Z"
        with self.assertRaisesRegex(ValueError, "did not execute together"):
            self.verify()
        self.assertEqual(self.pilot.api.call_count, 1)

    def test_wrong_worker_source_and_duplicate_nodes_are_rejected(self):
        self.jobs["worker"]["repo_rev"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "successful native attempt"):
            self.verify()
        self.jobs["worker"]["repo_rev"] = SHA
        self.formation["members"][1]["node"] = "node_leader"
        with self.assertRaisesRegex(ValueError, "distinct reserved nodes"):
            self.verify()

    def test_peer_validation_failure_is_fatal_and_cleans_up_dispatch(self):
        dispatch = run_path(str(Path(__file__).resolve().parents[1] / "inference-dispatch.py"))["dispatch"]
        cleanup, collect = Mock(), Mock()
        with patch.dict(dispatch.__globals__, {
            "plan": Mock(return_value={"rows": [self.row]}),
            "launch": Mock(return_value={"job_id": "job_leader"}),
            "wait": Mock(return_value=self.jobs["leader"]), "cancel": cleanup, "collect": collect,
            "verify_split_peer": Mock(side_effect=ValueError("bad peer evidence")),
        }):
            with self.assertRaisesRegex(ValueError, "bad peer evidence"):
                dispatch(self.pilot, "pr", SHA, self.output, self.row["id"], baseline="b" * 40)
        collect.assert_not_called()
        cleanup.assert_called_once()

    def test_attempt_on_a_different_node_is_rejected(self):
        self.jobs["worker"]["runs"][0]["machine_node"] = "node_other"
        with self.assertRaisesRegex(ValueError, "reserved nodes"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
