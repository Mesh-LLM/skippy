"""Readiness retries retain their deadline and never replay inference requests."""

import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib import ci_smoke


class Readiness(unittest.TestCase):
    def setUp(self):
        self.server = SimpleNamespace(poll=lambda: None)
        self.endpoint = "http://127.0.0.1:40000/v1"
        self.response = {"object": "chat.completion", "choices": [
            {"message": {"role": "assistant", "content": "Hello"}},
        ], "usage": {"completion_tokens": 1}}

    def test_transient_probe_errors_can_recover_before_completion(self):
        for error in (subprocess.TimeoutExpired("curl", 4), subprocess.CalledProcessError(28, "curl")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(ci_smoke, "curl", side_effect=[error, "{}", json.dumps(self.response)]) as curl, \
                    patch.object(ci_smoke.time, "sleep"):
                result = ci_smoke.completion(self.server, self.endpoint, ci_smoke.time.monotonic() + 10)
            self.assertEqual(result, self.response)
            self.assertEqual(curl.call_count, 3)
            self.assertEqual(curl.call_args_list[0].args[0], [self.endpoint + "/models"])
            self.assertEqual(curl.call_args_list[1].args[0], [self.endpoint + "/models"])

    def test_probe_timeouts_cannot_outlive_the_readiness_deadline(self):
        with patch.object(ci_smoke, "curl", side_effect=subprocess.TimeoutExpired("curl", 4)) as curl, \
                patch.object(ci_smoke.time, "monotonic", side_effect=[0, 1, 700]), \
                patch.object(ci_smoke.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "server readiness deadline"):
                ci_smoke.completion(self.server, self.endpoint, 1000)
        curl.assert_called_once()

    def test_completion_timeout_is_not_retried_as_a_readiness_probe(self):
        with patch.object(ci_smoke, "curl", side_effect=["{}", subprocess.TimeoutExpired("curl", 122)]) as curl:
            with self.assertRaises(subprocess.TimeoutExpired):
                ci_smoke.completion(self.server, self.endpoint, ci_smoke.time.monotonic() + 10)
        self.assertEqual(curl.call_count, 2)


if __name__ == "__main__":
    unittest.main()
