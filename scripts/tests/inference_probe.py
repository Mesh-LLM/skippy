"""Offline guards against warmup, cache hits and truncated output becoming TPS."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lib import inference_probe as probe

MODEL = "fixture@" + "a" * 40


def sample(model, index, warmup):
    request = probe.payload(model, index, warmup)
    count = request["max_tokens"]
    return {"payload": request, "client_ms": 300,
            "response": {"choices": [{"message": {"content": "diagnostic answer"}}],
                         "usage": {"prompt_tokens": 200 + index, "completion_tokens": count,
                                   "prompt_tokens_details": {"cached_tokens": 0}},
                         "timings": {"prompt_n": 200 + index, "prompt_ms": 100,
                                     "predicted_n": count, "predicted_ms": 200}}}


def evidence(model=MODEL):
    return {"schema_version": 1, "workload": probe.WORKLOAD, "status": "passed",
            "samples": [{"warmup": sample(model, i, True), "measured": sample(model, i, False)}
                        for i in range(len(probe.PARAGRAPHS))]}


class ProbeTests(unittest.TestCase):
    def test_only_measured_tokens_survive_validation(self):
        responses = probe.validate_evidence(evidence(), MODEL)
        self.assertEqual(len(responses), 3)
        self.assertEqual(sum(r["usage"]["completion_tokens"] for r in responses), 384)
        self.assertNotEqual(probe.payload(MODEL, 0, True)["messages"],
                            probe.payload(MODEL, 0, False)["messages"])

    def test_rejects_cache_hits_truncation_and_invalid_timings(self):
        mutations = [
            lambda s: s["response"]["usage"]["prompt_tokens_details"].update(cached_tokens=128),
            lambda s: s["response"]["usage"].update(completion_tokens=11),
            lambda s: s["response"]["timings"].update(predicted_n=11),
            lambda s: s["response"]["timings"].update(predicted_ms=float("nan")),
            lambda s: s.update(client_ms=0),
            lambda s: s["payload"].update(ignore_eos=False),
        ]
        for mutate in mutations:
            for kind in ("warmup", "measured"):
                with self.subTest(mutate=mutate, kind=kind):
                    data = evidence()
                    mutate(data["samples"][0][kind])
                    with self.assertRaises(ValueError):
                        probe.validate_evidence(data, MODEL)

    def test_rejects_missing_probe_or_different_warmup_shape(self):
        for change in (lambda d: d["samples"].pop(),
                       lambda d: d.update(workload="DGX Spark smoke v1"),
                       lambda d: d.update(status="incomplete"),
                       lambda d: d["samples"][0]["warmup"]["response"]["usage"].update(prompt_tokens=123)):
            data = evidence()
            change(data)
            with self.assertRaises(ValueError):
                probe.validate_evidence(data, MODEL)

    def test_measure_retains_failed_response_and_stops(self):
        bad = sample(MODEL, 0, True)
        bad["response"]["usage"]["completion_tokens"] = 1
        with TemporaryDirectory() as temporary, patch.object(
                probe, "send", side_effect=lambda _request, record: record.update(bad)) as send:
            with self.assertRaisesRegex(ValueError, "full output"):
                probe.measure(MODEL, Path(temporary))
            saved = json.loads((Path(temporary) / "throughput-probe.json").read_text())
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["samples"][0]["warmup"], bad)
        send.assert_called_once()

    def test_measure_keeps_warmups_separate(self):
        replies = [sample(MODEL, i, warmup)
                   for i in range(len(probe.PARAGRAPHS)) for warmup in (True, False)]
        with TemporaryDirectory() as temporary, patch.object(
                probe, "send", side_effect=lambda _request, record: record.update(replies.pop(0))):
            probe.measure(MODEL, Path(temporary))
            saved = json.loads((Path(temporary) / "throughput-probe.json").read_text())
        self.assertEqual(len(probe.validate_evidence(saved, MODEL)), 3)

    def test_malformed_measured_json_retains_raw_body_request_and_time(self):
        body = '{"broken":'
        with TemporaryDirectory() as temporary, patch.object(
                probe, "curl", side_effect=[json.dumps(sample(MODEL, 0, True)["response"]), body]):
            with self.assertRaises(json.JSONDecodeError):
                probe.measure(MODEL, Path(temporary))
            saved = json.loads((Path(temporary) / "throughput-probe.json").read_text())
        failed = saved["samples"][0]["measured"]
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(failed["payload"], probe.payload(MODEL, 0, False))
        self.assertEqual(failed["raw_body"], body)
        self.assertGreater(failed["client_ms"], 0)
        self.assertNotIn("response", failed)


if __name__ == "__main__":
    unittest.main()
