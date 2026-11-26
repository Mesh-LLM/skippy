"""Offline regressions for transient JY reads during native qualification."""

import io
import json
import ssl
import unittest
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, call, patch

from lib import jy_qualification
from lib.jy_qualification import Pilot, PilotHTTPError


class ReadRecoveryTests(unittest.TestCase):
    path = "/v1/jobs/job_test"

    def setUp(self):
        self.pilot = Pilot()
        self.auth = self.enterContext(patch.object(self.pilot, "auth", return_value="test-credential"))
        self.sleep = self.enterContext(patch.object(jy_qualification.time, "sleep"))
        self.enterContext(patch.object(jy_qualification.sys, "stderr", io.StringIO()))

    def test_transient_disconnect_timeout_and_partial_reads_recover(self):
        for error in (RemoteDisconnected("connection closed"), ConnectionResetError(), TimeoutError(),
                      IncompleteRead(b"{", 10), URLError(ConnectionResetError()), URLError(TimeoutError())):
            with self.subTest(error=type(error).__name__), \
                    patch.object(self.pilot.opener, "open",
                                 side_effect=[error, io.BytesIO(b'{"status":"running"}')]) as send:
                self.assertEqual(self.pilot.api("GET", self.path), {"status": "running"})
                self.assertEqual(send.call_count, 2)
                self.assertTrue(all(item.args[0].method == "GET" for item in send.call_args_list))

    def test_transient_http_responses_recover(self):
        for status in (408, 429, 500, 502, 503, 504):
            error = HTTPError("https://pilot.jianyang.cloud" + self.path, status, "Unavailable", {},
                              io.BytesIO(b"temporary failure"))
            with self.subTest(status=status), patch.object(self.pilot.opener, "open",
                    side_effect=[error, io.BytesIO(b'{"status":"running"}')]) as send:
                self.assertEqual(self.pilot.api("GET", self.path), {"status": "running"})
                self.assertEqual(send.call_count, 2)

    def test_persistent_disconnect_stops_after_four_attempts(self):
        with patch.object(self.pilot.opener, "open", side_effect=RemoteDisconnected("still down")) as send:
            with self.assertRaisesRegex(RemoteDisconnected, "still down"):
                self.pilot.api("GET", self.path)
        self.assertEqual(send.call_count, 4)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2), call(4)])
        self.auth.assert_called_once()

    def test_deadline_caps_request_timeout_and_prevents_retry_after_expiry(self):
        with patch.object(jy_qualification.time, "monotonic", side_effect=[10, 14]), \
                patch.object(self.pilot.opener, "open", side_effect=TimeoutError()) as send:
            with self.assertRaises(TimeoutError):
                self.pilot.api("GET", self.path, deadline=15)
        self.assertEqual(send.call_args.kwargs["timeout"], 5)
        send.assert_called_once()
        self.sleep.assert_not_called()

    def test_expired_deadline_sends_no_request(self):
        with patch.object(jy_qualification.time, "monotonic", return_value=15), \
                patch.object(self.pilot.opener, "open") as send:
            with self.assertRaisesRegex(TimeoutError, "deadline reached"):
                self.pilot.api("GET", self.path, deadline=15)
        send.assert_not_called()

    def test_retry_request_uses_remaining_budget(self):
        with patch.object(jy_qualification.time, "monotonic", side_effect=[10, 11, 12]), \
                patch.object(self.pilot.opener, "open", side_effect=[TimeoutError(),
                             io.BytesIO(b'{"status":"running"}')]) as send:
            self.assertEqual(self.pilot.api("GET", self.path, deadline=15), {"status": "running"})
        self.assertEqual([item.kwargs["timeout"] for item in send.call_args_list], [5, 3])
        self.sleep.assert_called_once_with(1)


    def test_permanent_errors_fail_immediately(self):
        errors = [HTTPError("https://pilot.jianyang.cloud" + self.path, status, "Permanent", {},
                            io.BytesIO(b"permanent failure")) for status in (400, 401, 403, 404, 409)]
        errors += [URLError(ssl.SSLCertVerificationError("invalid certificate")), URLError("bad URL"),
                   ValueError("invalid response")]
        for error in errors:
            with self.subTest(error=error), patch.object(self.pilot.opener, "open", side_effect=error) as send:
                with self.assertRaises((PilotHTTPError, URLError, ValueError)):
                    self.pilot.api("GET", self.path)
                send.assert_called_once()
        self.sleep.assert_not_called()

    def test_launch_cancel_and_authentication_are_not_replayed(self):
        for path in ("/v1/repos/skippy/templates/inference-split/launch", "/v1/formations/frm_test/cancel"):
            with self.subTest(path=path), \
                    patch.object(self.pilot.opener, "open", side_effect=RemoteDisconnected("write interrupted")) as send:
                with self.assertRaises(RemoteDisconnected):
                    self.pilot.api("POST", path, {})
                send.assert_called_once()
        self.auth.side_effect = RemoteDisconnected("authentication interrupted")
        with patch.object(self.pilot, "request") as send:
            with self.assertRaisesRegex(RemoteDisconnected, "authentication interrupted"):
                self.pilot.api("GET", self.path)
            send.assert_not_called()
        self.sleep.assert_not_called()

    def dispatch(self):
        dispatch = run_path(str(Path(__file__).resolve().parents[1] / "inference-dispatch.py"))["dispatch"]
        row = {"id": "model-split", "model": "Fixture/Model", "mode": "split"}
        launch = Mock(return_value={"job_id": "job_test"})
        cleanup = Mock()
        return dispatch, {"plan": Mock(return_value={"rows": [row]}), "launch": launch, "cancel": cleanup}

    def test_polling_disconnect_does_not_cancel_or_relaunch_native_formation(self):
        sha = "a" * 40
        succeeded = {"status": "succeeded", "repo_rev": sha, "formation_id": "frm_test"}
        replies = [RemoteDisconnected("connection closed"), io.BytesIO(b'{"status":"running"}'),
                   io.BytesIO(json.dumps(succeeded).encode()), io.BytesIO(b'{"status":"succeeded","members":[]}')]
        with TemporaryDirectory() as tmp, patch.object(self.pilot.opener, "open", side_effect=replies) as send:
            dispatch, mocks = self.dispatch()
            with patch.dict(dispatch.__globals__, mocks):
                dispatch(self.pilot, "nightly", sha, Path(tmp), "model-split")
            mocks["launch"].assert_called_once()
            mocks["cancel"].assert_not_called()
            self.assertTrue(all(item.args[0].method == "GET" for item in send.call_args_list))
            self.assertEqual(json.loads((Path(tmp) / "model-split-formation.json").read_text()),
                             {"status": "succeeded", "members": []})

    def test_exhausted_polling_still_cancels_the_native_job(self):
        with TemporaryDirectory() as tmp, \
                patch.object(self.pilot.opener, "open", side_effect=RemoteDisconnected("still down")) as send:
            dispatch, mocks = self.dispatch()
            with patch.dict(dispatch.__globals__, mocks):
                with self.assertRaises(RemoteDisconnected):
                    dispatch(self.pilot, "nightly", "a" * 40, Path(tmp), "model-split")
            self.assertEqual(send.call_count, 4)
            mocks["launch"].assert_called_once()
            mocks["cancel"].assert_called_once_with(self.pilot, Path(tmp) / "jobs.json")
            self.assertEqual(json.loads((Path(tmp) / "jobs.json").read_text()), ["job_test"])


if __name__ == "__main__":
    unittest.main()
