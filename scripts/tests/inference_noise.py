"""Offline guards for the registered diagnostic; no hardware qualification."""

import json
import unittest
from contextlib import nullcontext
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from lib import inference_noise as noise

MODEL = "fixture@" + "a" * 40


def reply(request, record):
    shape = next(i for i, n in enumerate(noise.PARAGRAPHS)
                 if request["messages"][0]["content"].count("Record ") == n)
    count = request["max_tokens"]
    response = {"choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 200 + shape, "completion_tokens": count,
                          "prompt_tokens_details": {"cached_tokens": 0}},
                "timings": {"prompt_n": 200 + shape, "prompt_ms": 100,
                            "predicted_n": count, "predicted_ms": 200}}
    record.update(payload=request, client_ms=350, raw_body=json.dumps(response), response=response)


class NoiseTests(unittest.TestCase):
    def test_registered_design_balances_order_and_rejects_another_baseline(self):
        design = noise.registration()
        self.assertEqual(design["arms"], [["cycled", "blocked"], ["blocked", "cycled"]])
        arms = noise.schedule(design["baseline_sha"])
        self.assertEqual(len(arms), 2)
        self.assertTrue(all(arm["source_sha"] == design["baseline_sha"] for arm in arms))
        with self.assertRaisesRegex(ValueError, "registration"):
            noise.schedule("b" * 40)

    def test_conditions_change_only_order_and_have_unique_equal_width_prefixes(self):
        cycled, blocked = (noise.requests(condition, 12) for condition in ("cycled", "blocked"))
        self.assertEqual(sorted(cycled), sorted(blocked))
        self.assertEqual(cycled[:3], [(0, 0), (1, 0), (2, 0)])
        self.assertEqual(blocked[:3], [(0, 0), (0, 1), (0, 2)])
        prefixes = {noise.request(MODEL, pos, shape, rep, warm)["messages"][0]["content"].splitlines()[0]
                    for pos in range(2) for shape, rep in cycled for warm in (True, False)}
        self.assertEqual(len(prefixes), 144)
        self.assertEqual({len(prefix) for prefix in prefixes}, {4})

    def test_all_raw_requests_retained_separate_from_paired_qualification(self):
        with TemporaryDirectory() as temporary, patch.object(noise, "send", side_effect=reply) as send:
            output = Path(temporary)
            noise.measure(MODEL, output, 0)
            evidence = json.loads((output / "noise-pilot.json").read_text())
            self.assertFalse((output / "paired-probe.json").exists())
        self.assertEqual(send.call_count, 144)
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(len(evidence["samples"]), 72)
        for sample in evidence["samples"]:
            for name in ("warmup", "measured"):
                self.assertIn("raw_body", sample[name])
                self.assertIn("started_at", sample[name])
                self.assertIn("completed_at", sample[name])

    def test_rejects_shape_changes_cache_hits_and_truncation_without_dropping_data(self):
        def shape_change(record):
            record["response"]["usage"]["prompt_tokens"] += 1
            record["response"]["timings"]["prompt_n"] += 1
        mutations = [shape_change,
                     lambda r: r["response"]["usage"]["prompt_tokens_details"].update(cached_tokens=1),
                     lambda r: r["response"]["usage"].update(completion_tokens=1)]
        for mutate in mutations:
            calls = []
            def send(request, record):
                reply(request, record)
                calls.append(record)
                if len(calls) == 2:
                    mutate(record)
            with self.subTest(mutate=mutate), TemporaryDirectory() as temporary, \
                    patch.object(noise, "send", side_effect=send):
                output = Path(temporary)
                with self.assertRaises(ValueError):
                    noise.measure(MODEL, output, 0)
                evidence = json.loads((output / "noise-pilot.json").read_text())
                self.assertEqual(evidence["status"], "failed")
                self.assertEqual(len(evidence["samples"]), 1)
                self.assertIn("raw_body", evidence["samples"][0]["measured"])

    def test_pilot_arm_keeps_standard_native_checks_and_partial_archive(self):
        module = run_path(str(noise.ROOT / "scripts/inference-paired.py"))
        run = module["run_arm"]
        design = noise.schedule(noise.registration()["baseline_sha"])[0]
        native = Mock(side_effect=RuntimeError("injected"))
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.dict(run.__globals__, EXECUTE=native), \
                    patch.object(noise, "diagnostics", return_value=nullcontext()):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    run(design, output, design["source_sha"], "model", "split", output)
            self.assertTrue((output / "noise-0-a-evidence.tar.gz").is_file())
        self.assertEqual(native.call_args.args, ("pr", "model", "split"))
        self.assertTrue(callable(native.call_args.kwargs["probe"]))

    def test_pilot_cannot_take_legacy_or_unregistered_configuration(self):
        execute = run_path(str(noise.ROOT / "scripts/inference-paired.py"))["execute"]
        for model, mode, baseline in (("qwen35-4b", "standalone", noise.registration()["baseline_sha"]),
                                       ("qwen3-coder-30b-a3b", "split", "legacy")):
            with self.assertRaisesRegex(ValueError, "registered configuration"):
                execute(model, mode, baseline, "noise-pilot")


if __name__ == "__main__":
    unittest.main()
