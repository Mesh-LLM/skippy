"""Offline checks for inference planning and protected placement."""

import io
import os
import unittest
from pathlib import Path
from runpy import run_path
from urllib.error import HTTPError
from unittest.mock import patch

from lib import inference_host, inference_ladder
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

    def test_metal_runs_every_ladder_model_standalone(self):
        pr = inference_ladder.plan("pr", self.sha, "metal")
        nightly = inference_ladder.plan("nightly", self.sha, "metal")
        self.assertEqual(pr["hardware"], "apple-silicon-metal")
        self.assertEqual(len(pr["rows"]), 4)
        self.assertEqual(len(nightly["rows"]), 8)
        for row in pr["rows"] + nightly["rows"]:
            self.assertEqual((row["mode"], row["machines"], row["platform"]), ("standalone", 1, "metal"))
            self.assertEqual(row["id"], f'{row["model_key"]}-standalone-metal')

    def test_all_platforms_keep_spark_ids_and_add_distinct_metal_ids(self):
        rows = inference_ladder.matrix("pr", self.sha, platform="all")
        spark = inference_ladder.plan("pr", self.sha)["rows"]
        self.assertEqual([row["id"] for row in rows[:len(spark)]], [row["id"] for row in spark])
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        with self.assertRaises(ValueError):
            inference_ladder.matrix("pr", self.sha, platform="rocm")

    def test_every_planned_row_has_a_template_for_its_platform(self):
        root = Path(__file__).resolve().parents[2]
        templates = {template["key"]: template for template in
                     inference_ladder.read_json(root / ".gi/templates.yaml")["templates"]}
        for profile in ("pr", "nightly"):
            for row in inference_ladder.matrix(profile, self.sha, platform="all"):
                key = inference_ladder.template_key(profile, row["mode"], row["platform"])
                self.assertIn(key, templates)
                command = templates[key].get("command") or ""
                if row["platform"] == "metal":
                    self.assertTrue(command.endswith(" standalone metal"), key)
                    self.assertEqual(templates[key]["target"]["selector"]["accelerator"]["backend"], "metal")
                    self.assertNotIn("env", templates[key])

    def test_invalid_source_and_duplicate_model(self):
        for sha in ("main", "pr:1", "", "a" * 39 + "z"):
            with self.assertRaises(ValueError):
                inference_ladder.plan("pr", sha)
        ladder = inference_ladder.read_json(inference_ladder.LADDER)
        ladder["models"].append(ladder["models"][0])
        with patch.object(inference_ladder, "read_json", return_value=ladder):
            with self.assertRaises(ValueError):
                inference_ladder.plan("pr", self.sha)


class HostTests(unittest.TestCase):
    VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               100.
Pages active:                             900.
Pages inactive:                            50.
Pages speculative:                         10.
Pages wired down:                         500.
Pages purgeable:                            5.
"""

    def test_macos_admission_counts_pages_reclaimable_without_swap(self):
        self.assertEqual(inference_host.reclaimable_bytes(self.VM_STAT), 165 * 16384)
        with self.assertRaises(ValueError):
            inference_host.reclaimable_bytes(self.VM_STAT.replace("Pages purgeable", "Pages gone"))

    def test_unknown_platform_is_rejected(self):
        with self.assertRaises(ValueError):
            inference_host.device("rocm")


class PlatformAdmissionTests(unittest.TestCase):
    def test_labels_and_dispatch_input_choose_platforms(self):
        platforms = run_path(str(Path(__file__).resolve().parents[1] / "inference-plan.py"))["platforms"]

        def pr(*labels):
            return {"pull_request": {"labels": [{"name": label} for label in labels]}}

        self.assertEqual(platforms(pr("qualify"), "pull_request_target"), "spark")
        self.assertEqual(platforms(pr("qualify-metal"), "pull_request_target"), "metal")
        self.assertEqual(platforms(pr("qualify", "qualify-metal"), "pull_request_target"), "all")
        self.assertEqual(platforms({}, "schedule"), "spark")
        with patch.dict(os.environ, QUALIFICATION_PLATFORM="metal"):
            self.assertEqual(platforms({}, "workflow_dispatch"), "metal")
        with patch.dict(os.environ, QUALIFICATION_PLATFORM="tpu"):
            with self.assertRaises(ValueError):
                platforms({}, "workflow_dispatch")


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
                with self.assertRaisesRegex(ValueError, "does not belong"):
                    admit("c" * 40)
                with patch.dict(os.environ, GITHUB_REF="refs/heads/branch"):
                    with self.assertRaisesRegex(ValueError, "protected Release dispatch"):
                        admit(candidate)


if __name__ == "__main__":
    unittest.main()
