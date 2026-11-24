"""Offline regressions for the explicit live rehearsal's evidence claims."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import upgrade_goose_rehearsal as rehearsal
from lib.upgrade_recovery import retain


class RehearsalEvidence(unittest.TestCase):
    def run_rehearsal(self):
        def agent(checkout, _diagnostics, *, session, timeout):
            self.assertGreater(timeout, 0)
            path = checkout / 'llama_cpp/patches/rehearsal.patch'
            path.write_text('phase-two' if path.exists() else 'phase-one')
            session['started'] = True
        with patch.object(rehearsal, 'repair', side_effect=agent), \
             patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND='["fixture-agent"]'):
            rehearsal.main()

    def test_repeated_invocations_report_only_their_own_checkpoints(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                for _ in range(2):
                    self.run_rehearsal()
                    result = json.loads(Path('target/upgrade-goose-rehearsal/result.json').read_text())
                    self.assertTrue(result['passed'])
                    self.assertEqual(result['recovery_snapshots'], 3)
                    self.assertEqual(result['agent_turns'], 2)
            finally:
                os.chdir(previous)

    def test_failed_final_capture_cannot_reuse_old_evidence_or_report_success(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                self.run_rehearsal()
                def missing_final(*args, **kwargs):
                    return None if args[2] == 'final' else retain(*args, **kwargs)
                with patch('lib.upgrade_lifecycle.retain', side_effect=missing_final):
                    with self.assertRaisesRegex(ValueError, 'completed final checkpoint'):
                        self.run_rehearsal()
                self.assertFalse(Path('target/upgrade-goose-rehearsal/result.json').exists())
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
