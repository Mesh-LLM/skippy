"""Offline regression checks for the real publisher configuration gate."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class PublisherConfiguration(unittest.TestCase):
    def invoke(self, app_id=None, key_configured=None):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            for name, value in (("SKIPPY_UPGRADE_APP_ID", app_id),
                                ("SKIPPY_UPGRADE_APP_KEY_CONFIGURED", key_configured)):
                os.environ.pop(name, None)
                if value is not None:
                    os.environ[name] = value
            return subprocess.run(["bash", str(ROOT / "scripts/check-upgrade-publisher.sh")],
                                  cwd=directory, capture_output=True, text=True, check=False)

    def test_missing_settings_stop_before_candidate_preparation(self):
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SKIPPY_UPGRADE_APP_ID", result.stderr)
        self.assertIn("SKIPPY_UPGRADE_APP_PRIVATE_KEY", result.stderr)
        self.assertIn("candidate preparation has not started", result.stderr)

    def test_partial_configuration_is_not_accepted(self):
        for app_id, configured, missing in ((None, "true", "SKIPPY_UPGRADE_APP_ID"),
                                            ("fixture-app", None, "SKIPPY_UPGRADE_APP_PRIVATE_KEY"),
                                            ("fixture-app", "false", "SKIPPY_UPGRADE_APP_PRIVATE_KEY")):
            with self.subTest(missing=missing, configured=configured):
                result = self.invoke(app_id, configured)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(missing, result.stderr)

    def test_presence_check_does_not_claim_app_access_is_verified(self):
        result = self.invoke("fixture-app-id", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verifying App access next", result.stdout)
        self.assertNotIn("fixture-app-id", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
