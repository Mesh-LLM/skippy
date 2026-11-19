"""Offline checks for inference planning and protected placement."""

import os
import unittest
from pathlib import Path
from runpy import run_path
from unittest.mock import patch

from lib import inference_ladder
from lib.jy_qualification import placement


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
                with self.assertRaisesRegex(ValueError, "does not belong"):
                    admit("c" * 40)
                with patch.dict(os.environ, GITHUB_REF="refs/heads/branch"):
                    with self.assertRaisesRegex(ValueError, "protected Release dispatch"):
                        admit(candidate)


if __name__ == "__main__":
    unittest.main()
