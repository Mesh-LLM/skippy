"""Offline checks for inference planning and protected placement."""

import io
import os
import unittest
from pathlib import Path
from runpy import run_path
from urllib.error import HTTPError
from unittest.mock import patch

from lib import inference_ladder
from lib import inference_metal
from lib.jy_qualification import Pilot, placement


class LadderTests(unittest.TestCase):
    sha = "0123456789abcdef0123456789abcdef01234567"

    def test_short_profile(self):
        plan = inference_ladder.plan("pr", self.sha)
        rows = plan["rows"]
        self.assertEqual(len(rows), 7)
        self.assertEqual(len({row["architecture"] for row in rows}), 4)
        self.assertEqual(sum(row["mode"] == "split" for row in rows), 3)
        self.assertNotIn("qwen35-4b-split", {row["id"] for row in rows})
        self.assertEqual(plan["performance_claim"], "smoke-only")
        for row in rows:
            self.assertEqual(row["machines"], 2 if row["mode"] == "split" else 1)
            self.assertEqual(row["checks"], ["gpu-startup", "scored-quality", "streaming-smoke"])

    def test_extended_profiles(self):
        nightly = inference_ladder.plan("nightly", self.sha)["rows"]
        release = inference_ladder.plan("release", self.sha)["rows"]
        self.assertEqual(nightly, release)
        self.assertEqual(len({row["id"] for row in release}), 12)
        self.assertEqual({row["id"] for row in release if row["mode"] == "split"}, {
            "qwen38-27b-split", "qwen3-coder-30b-a3b-split",
            "qwen36-35b-a3b-split", "qwen38-flash-next-split",
        })

    def test_invalid_source_and_duplicate_model(self):
        for sha in ("main", "pr:1", "", "a" * 39 + "z"):
            with self.assertRaises(ValueError):
                inference_ladder.plan("pr", sha)
        ladder = inference_ladder.read_json(inference_ladder.LADDER)
        ladder["models"].append(ladder["models"][0])
        with patch.object(inference_ladder, "read_json", return_value=ladder):
            with self.assertRaises(ValueError):
                inference_ladder.plan("pr", self.sha)

    def test_metal_uses_the_same_models_only_in_standalone_mode(self):
        for profile, count in (("pr", 4), ("nightly", 8), ("release", 8)):
            cuda = inference_ladder.matrix(profile, self.sha)
            metal = inference_ladder.matrix(profile, self.sha, backend="metal")
            self.assertEqual(len(metal), count)
            self.assertEqual([row["model"] for row in metal],
                             [row["model"] for row in cuda if row["mode"] == "standalone"])
            self.assertTrue(all(row["mode"] == "standalone" and row["name"].startswith("Metal - ")
                                for row in metal))
            self.assertFalse({row["id"] for row in cuda} & {row["id"] for row in metal})
        self.assertEqual(len(inference_ladder.matrix("nightly", self.sha, True, backend="metal")), 12)


class MetalAdmissionTests(unittest.TestCase):
    def test_available_memory_is_bounded_by_vm_stats_and_metal_budget(self):
        stats = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 3.\nPages inactive: 5.\n"
        self.assertEqual(inference_metal.available_memory(stats, 2**20), 8 * 16384)
        self.assertEqual(inference_metal.available_memory(stats, 65536), 65536)
        with self.assertRaisesRegex(ValueError, "could not be measured"):
            inference_metal.available_memory("invalid", 65536)

    def test_rejects_the_wrong_apple_chip(self):
        with patch.object(inference_metal.platform, "system", return_value="Darwin"), \
             patch.object(inference_metal.platform, "machine", return_value="arm64"), \
             patch.object(inference_metal, "capture", return_value="Apple M5 Max\n"):
            with self.assertRaisesRegex(ValueError, "Apple M3 Ultra"):
                inference_metal.device_and_budget()

    def test_comparisons_take_any_apple_m_series_chip_with_its_own_metal_gpu(self):
        with patch.object(inference_metal.platform, "system", return_value="Darwin"), \
             patch.object(inference_metal.platform, "machine", return_value="arm64"):
            with patch.object(inference_metal, "capture", return_value="Apple M1 Ultra\n"):
                self.assertEqual(inference_metal.chip(), "Apple M1 Ultra")
            with patch.object(inference_metal, "capture", return_value="Apple Paravirtual device\n1024\n"):
                with self.assertRaisesRegex(ValueError, "Metal device"):
                    inference_metal.working_set("Apple M1 Ultra")


class PilotRequestTests(unittest.TestCase):
    def test_jy_requests_identify_the_client(self):
        pilot = Pilot()
        with patch.object(pilot.opener, "open") as send:
            send.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
            self.assertEqual(pilot.request("POST", "/v1/auth/challenge", {}), {"ok": True})
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "skippy-inference-ci/1.0")

    def test_authentication_failures_name_the_endpoint_and_redact_the_response(self):
        pilot = Pilot()
        response = HTTPError("https://pilot.jianyang.cloud/v1/auth/login", 403, "Forbidden",
                             {}, io.BytesIO(b"sensitive authentication response"))
        with patch.object(pilot.opener, "open", side_effect=response):
            with self.assertRaisesRegex(RuntimeError,
                                        r"^JY HTTP 403: /v1/auth/login authentication request failed$"):
                pilot.request("POST", "/v1/auth/login", {})


class PlacementTests(unittest.TestCase):
    def test_two_distinct_hosts_on_one_network(self):
        def agent(name, network, runs):
            return {"id": name, "node": name, "online": True, "enabled": True, "drain": False,
                    "network": network, "host": {"gpu": "NVIDIA GB10"}, "active_runs": runs}

        source = "a" * 40
        agents = {"agents": [
            agent("controller", "dan-lab", []),
            agent("first", "dan-lab", [{"job_name": "skippy-inference-standalone-" + source[:12]}]),
            agent("second", "dan-lab", []),
            agent("elsewhere", "james-lab", []),
        ]}
        self.assertEqual(placement(agents, "controller", 0, source), ("first", "second"))
        with self.assertRaises(ValueError):
            placement(agents, "controller", 0, "b" * 40)


class ReleaseAdmissionTests(unittest.TestCase):
    def test_only_the_staged_commit_from_protected_release_dispatch_is_admitted(self):
        admission = run_path(str(Path(__file__).resolve().parents[1] / "inference-plan.py"))
        admit = admission["admit_release_candidate"]
        candidate, base = "a" * 40, "b" * 40
        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": "general-inference/skippy/.github/workflows/release.yml@refs/heads/main",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": base,
        }
        ref = {"object": {"sha": candidate}}
        commit = {"parents": [{"sha": base}], "message": "Release\n\nSkippy-Release-Run: 123"}
        with patch.dict(os.environ, env):
            with patch.dict(admit.__globals__, github=lambda path: ref if "/ref/" in path else commit):
                admit(candidate)
                with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}):
                    admit(candidate)
                with self.assertRaisesRegex(ValueError, "does not belong"):
                    admit("c" * 40)
                with patch.dict(os.environ, GITHUB_REF="refs/heads/branch"):
                    with self.assertRaisesRegex(ValueError, "protected Release run"):
                        admit(candidate)


if __name__ == "__main__":
    unittest.main()
