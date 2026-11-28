"""Offline subprocess regressions for inherited runner configuration."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.ci_process import command

SCRIPTS = Path(__file__).resolve().parents[1]


class ProcessEnvironment(unittest.TestCase):
    def test_nested_commands_preserve_job_environment_and_apply_overrides(self):
        expected = {
            "ACTIONS_RUNTIME_TOKEN": "fixture-cache-access",
            "SCCACHE_GHA_ENABLED": "true", "SCCACHE_GHA_VERSION": "fixture-cache",
            "JIANYANG_GATEWAY_TOKEN": "fixture-model-access",
            "JIANYANG_API_KEY": "fixture-jy-access",
            "JIANYANG_INFERENCE_SSH_KEY": "fixture-ssh-key",
            "GOOSE_PROVIDER": "fixture-provider", "GOOSE_MODEL": "fixture-model",
            "SERVICE_SECRET": "fixture-secret", "SERVICE_PASSWORD": "fixture-password",
            "GITHUB_ENV": "fixture-env", "GITHUB_OUTPUT": "fixture-output",
            "GITHUB_PATH": "fixture-path", "GITHUB_STEP_SUMMARY": "fixture-summary",
        }
        reader = f"import json, os; print(json.dumps({{key: os.environ.get(key) for key in {list(expected)!r}}}))"
        nested = (
            "import sys\nfrom lib.ci_process import command\n"
            f"print(command([sys.executable, '-c', {reader!r}], capture_stdout=True), end='')\n"
        )
        with patch.dict(os.environ, {**expected, "PYTHONPATH": str(SCRIPTS), "GOOSE_MODEL": "old-model"}):
            output = command([sys.executable, "-c", nested], capture_stdout=True,
                             overrides={"GOOSE_MODEL": expected["GOOSE_MODEL"]})
            self.assertEqual(os.environ["GOOSE_MODEL"], "old-model")
        self.assertEqual(json.loads(output), expected)

    def test_explicit_environment_can_remove_a_parent_variable(self):
        with patch.dict(os.environ, SKIPPY_PROCESS_PARENT="parent"):
            output = command([sys.executable, "-c",
                "import json, os; print(json.dumps([os.environ.get('SKIPPY_PROCESS_PARENT'), "
                "os.environ.get('SKIPPY_PROCESS_CHILD')]))"], capture_stdout=True,
                inherit_env=False, overrides={"SKIPPY_PROCESS_CHILD": "child"})
        self.assertEqual(json.loads(output), [None, "child"])


if __name__ == "__main__":
    unittest.main()
