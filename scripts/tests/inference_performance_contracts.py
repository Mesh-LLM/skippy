"""Offline checks for the performance comparison's plan, servers, parsing and report."""

import json
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from lib import performance_arms, performance_backends, performance_plan
from lib.performance_arms import REQUIRED, capabilities, may_reject, server_args
from lib.performance_load import measurements, profile_args, rejection, request_rate, run_point
from lib import performance_report
from lib.performance_report import markdown, summarize


class ScenarioTests(unittest.TestCase):
    def test_shipped_scenario_is_valid(self):
        shape = performance_plan.scenario()
        self.assertGreater(len(shape["servers"]), 1)

    def test_scenario_rejects_impossible_servers(self):
        shape = performance_plan.read_json(performance_plan.SCENARIO)
        server = shape["servers"][0]
        for change in ({"lanes": 0}, {"pool_tokens": 1024}):
            broken = json.loads(json.dumps(shape))
            broken["servers"][0].update(change)
            with patch.object(performance_plan, "read_json", return_value=broken):
                with self.assertRaises(ValueError):
                    performance_plan.scenario()
        for change in ({"concurrency": server["lanes"] + 1}, {"depth": shape["context_per_lane"]}):
            broken = json.loads(json.dumps(shape))
            broken["servers"][0]["points"][0].update(change)
            with patch.object(performance_plan, "read_json", return_value=broken):
                with self.assertRaises(ValueError):
                    performance_plan.scenario()

    def test_named_servers_keep_only_their_points(self):
        first = performance_plan.scenario()["servers"][0]
        shape = performance_plan.scenario([first["id"]])
        self.assertEqual(shape["servers"], [first])
        for names in ([], ["no-such-server"]):
            with self.assertRaises(ValueError):
                performance_plan.scenario(names)

    def test_a_variant_server_runs_only_when_named(self):
        variant = "lanes4-pool32k-prefill2048"
        self.assertNotIn(variant, [server["id"] for server in performance_plan.scenario()["servers"]])
        named = performance_plan.scenario(["lanes4-pool32k", variant])["servers"]
        self.assertEqual([server["id"] for server in named], ["lanes4-pool32k", variant])

    def test_a_rate_point_names_a_positive_rate_per_backend_and_runs_on_its_own_schedule(self):
        rated = "lanes4-pool32k-rate"
        point = performance_plan.scenario([rated])["servers"][0]["points"][0]
        args = profile_args("http://e", point, "m@r", "/t", 2, Path("/a"), request_rate(point, "metal"))
        self.assertEqual(args[args.index("--request-rate") + 1], str(point["request_rate"]["metal"]))
        self.assertEqual(args[args.index("--arrival-pattern") + 1], "poisson")
        self.assertNotIn("--request-rate", profile_args("http://e", point, "m@r", "/t", 2, Path("/a")))
        shape = performance_plan.read_json(performance_plan.SCENARIO)
        index = next(index for index, server in enumerate(shape["servers"]) if server["id"] == rated)
        for rate in ({}, {"metal": 0}, {"metal": "fast"}, {"space-heater": 1}, 0.2):
            broken = json.loads(json.dumps(shape))
            broken["servers"][index]["points"][0]["request_rate"] = rate
            with patch.object(performance_plan, "read_json", return_value=broken):
                with self.assertRaises(ValueError):
                    performance_plan.scenario([rated])

    def test_a_variant_cannot_set_what_the_harness_sets_or_nothing(self):
        shape = performance_plan.read_json(performance_plan.SCENARIO)
        for flags in ({}, {"debug": 1}, {"kv-pool-tokens": 4096}, {"Prefill Chunk": 1},
                      {"prefill-chunk-size": 0}, {"prefill-chunk-size": "two words"}, []):
            broken = json.loads(json.dumps(shape))
            broken["servers"][0]["skippy_flags"] = flags
            with patch.object(performance_plan, "read_json", return_value=broken):
                with self.assertRaises(ValueError):
                    performance_plan.scenario()


class PlanTests(unittest.TestCase):
    candidate, baseline, pin = "a" * 40, "b" * 40, "c" * 40

    def test_arms_are_immutable_commits_and_reference_is_optional(self):
        labels = [arm["label"] for arm in performance_plan.arms(self.candidate, self.baseline, self.pin)]
        self.assertEqual(labels, ["candidate", "baseline", "reference"])
        same = performance_plan.arms(self.candidate, self.baseline, self.pin, self.pin)
        self.assertEqual([arm["label"] for arm in same], labels)
        moved = performance_plan.arms(self.candidate, self.baseline, self.pin, "d" * 40)
        self.assertEqual([(arm["label"], arm["sha"]) for arm in moved[2:]],
                         [("reference", self.pin), ("baseline-reference", "d" * 40)])
        with self.assertRaises(ValueError):
            performance_plan.arms(self.candidate, self.baseline, None, "d" * 40)
        self.assertEqual(len(performance_plan.arms(self.candidate, self.candidate, None)), 2)
        for bad in ("main", "a" * 39):
            with self.assertRaises(ValueError):
                performance_plan.arms(bad, self.baseline, None)

    def test_an_arm_can_be_an_open_pull_request_from_this_repository_only(self):
        self.assertEqual(performance_plan.pull_request_number("pr:130"), 130)
        for ref in ("main", "pr:0", "pr:x", "a" * 40):
            self.assertIsNone(performance_plan.pull_request_number(ref))
        repo = "Mesh-LLM/skippy"
        pull = {"number": 130, "state": "open", "head": {"sha": self.candidate, "repo": {"full_name": repo}}}
        self.assertEqual(performance_plan.pull_request_head(pull, repo), self.candidate)
        fork = {**pull, "head": {"sha": self.candidate, "repo": {"full_name": "someone/skippy"}}}
        for refused in ({**pull, "state": "closed"}, fork, {**pull, "head": {"sha": self.candidate, "repo": None}}):
            with self.assertRaises(ValueError):
                performance_plan.pull_request_head(refused, repo)

    def test_native_pr_admission_requires_collaborator_write_access(self):
        resolve = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["resolve"]
        pull = {"number": 130, "state": "open", "user": {"login": "author"},
                "head": {"sha": self.candidate, "repo": {"full_name": "Mesh-LLM/skippy"}}}
        for permission in ("read", "triage", "none", "write", "maintain", "admin"):
            calls = []
            def github(path):
                calls.append(path)
                return pull if path.endswith("/pulls/130") else {"permission": permission}
            with patch.dict(resolve.__globals__, github=github):
                if permission in {"write", "maintain", "admin"}:
                    self.assertEqual(resolve("pr:130"), (self.candidate, 130, None))
                else:
                    with self.assertRaisesRegex(ValueError, "collaborator"):
                        resolve("pr:130")
            self.assertEqual(calls[-1], "repos/Mesh-LLM/skippy/collaborators/author/permission")

    def test_an_arm_can_be_a_branch_of_this_repository(self):
        resolve = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["resolve"]
        calls = []
        def github(path):
            calls.append(path)
            return {"commit": {"sha": self.candidate}}
        with patch.dict(resolve.__globals__, github=github):
            self.assertEqual(resolve("branch:team/gen-ai"), (self.candidate, None, "team/gen-ai"))
        self.assertEqual(calls, ["repos/Mesh-LLM/skippy/branches/team%2Fgen-ai"])
        for ref in ("branch:", "branch:a b", "branches:x", "pr:130"):
            self.assertIsNone(performance_plan.branch_name(ref))

    def test_every_arm_leads_one_round(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        firsts = [performance_plan.round_order(arm_list, index)[0]["label"] for index in range(3)]
        self.assertEqual(sorted(firsts), ["baseline", "candidate", "reference"])

    def test_job_runs_every_arm_from_its_own_checkout_on_a_metal_host(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        spec = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, "macstudio-2l0352", "m1")
        repos = spec["runtime"]["workspace"]["repos"]
        self.assertEqual([repo["path"] for repo in repos], ["harness", "candidate", "baseline", "reference"])
        self.assertEqual(repos[3]["url"], performance_plan.LLAMA)
        self.assertEqual(repos[3]["rev"], self.pin)
        target = spec["target"]
        self.assertEqual(target["nodes"], ["macstudio-2l0352"])
        self.assertEqual(target["selector"]["accelerator"]["backend"], "metal")
        self.assertGreaterEqual(target["selector"]["memory"]["system_gib"]["min"], 18)
        plan = json.loads(spec["runtime"]["env"]["SKIPPY_PERFORMANCE_PLAN"])
        self.assertEqual((plan["harness"], plan["rounds"], plan["arms"]), ("d" * 40, 3, arm_list))
        self.assertIs(plan["trace"], False)
        traced = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any", trace=True)
        self.assertIs(json.loads(traced["runtime"]["env"]["SKIPPY_PERFORMANCE_PLAN"])["trace"], True)
        with self.assertRaises(ValueError):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any", trace="true")
        self.assertNotIn("nodes", performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any")["target"])
        with self.assertRaises(ValueError):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], performance_plan.MAX_ROUNDS + 1, None, "x")

    def test_the_job_reports_progress_after_every_step_and_ends_at_100(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        spec = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any", trace=True)
        self.assertNotIn("build", spec["runtime"]["workspace"])
        lines = spec["runtime"]["command"].splitlines()
        self.assertEqual(lines[0], "set -euo pipefail")
        self.assertEqual((lines[4], lines[-1]), ('progress 0 "starting"', 'progress 100 "done"'))
        steps = [line for line in lines if line.startswith("just ")]
        reports = [line for line in lines if line.startswith("progress ")][1:-1]
        self.assertEqual(len(steps), len(reports))
        percents = [int(report.split()[1]) for report in reports]
        self.assertEqual(percents, sorted(percents))
        self.assertLess(percents[-1], 100)
        self.assertEqual(steps[1:4], [f"just inference-performance-build-arm {arm}"
                                      for arm in ("candidate", "baseline", "reference")])
        measured = [step.split()[5] for step in steps if " measure " in step]
        per_round = len(measured) // 3
        # every arm leads one round
        self.assertEqual([measured[index * per_round] for index in range(3)],
                         ["candidate", "baseline", "reference"])

    def test_a_cuda_plan_targets_a_dgx_spark(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        spec = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, "spark-000137", "sp",
                                         backend="cuda")
        selector = spec["target"]["selector"]
        self.assertEqual(selector["os"], {"family": "linux"})
        self.assertEqual(selector["arch"], {"in": ["aarch64"]})
        self.assertEqual(selector["accelerator"], {"backend": "cuda", "model": {"in": ["NVIDIA GB10"]}})
        self.assertIs(selector["memory"]["unified"], True)
        self.assertGreaterEqual(selector["memory"]["system_gib"]["min"], 18)
        self.assertEqual(spec["target"]["nodes"], ["spark-000137"])
        self.assertIn("Same-host CUDA comparison", spec["description"])
        self.assertEqual(json.loads(spec["runtime"]["env"]["SKIPPY_PERFORMANCE_PLAN"])["backend"], "cuda")

    def test_a_rate_point_is_refused_on_a_backend_it_has_no_rate_for(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        with self.assertRaisesRegex(ValueError, "no request rate for the rocm backend"):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, "rocm-1", "r", backend="rocm",
                                      servers=["lanes4-pool32k-rate"])

    def test_an_unknown_backend_is_refused_at_plan_time(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        with self.assertRaisesRegex(ValueError, "unknown backend"):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any",
                                      backend="space-heater")

    def test_a_cuda_x86_plan_targets_one_pinned_discrete_gpu(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        with self.assertRaisesRegex(ValueError, "must name exactly one node"):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any",
                                      backend="cuda-x86")
        spec = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, "lab-a33338", "x",
                                         backend="cuda-x86")
        selector = spec["target"]["selector"]
        self.assertEqual(selector["os"], {"family": "linux"})
        self.assertEqual(selector["arch"], {"in": ["x86_64"]})
        self.assertEqual(selector["accelerator"], {"backend": "cuda"})
        self.assertIs(selector["memory"]["unified"], False)
        self.assertGreaterEqual(selector["memory"]["dedicated_vram_gib"]["min"], 9)
        self.assertEqual(spec["target"]["nodes"], ["lab-a33338"])
        self.assertEqual(spec["runtime"]["env"].get("CUDA_VISIBLE_DEVICES"), "0")

    def test_a_rocm_plan_targets_one_pinned_unified_host(self):
        arm_list = performance_plan.arms(self.candidate, self.baseline, self.pin)
        with self.assertRaisesRegex(ValueError, "must name exactly one node"):
            performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, None, "any",
                                      backend="rocm")
        spec = performance_plan.job_spec("d" * 40, arm_list, ["qwen35-4b"], 3, "lab-003827", "r",
                                         backend="rocm")
        self.assertNotIn("selector", spec["target"])
        self.assertEqual(spec["target"]["nodes"], ["lab-003827"])
        self.assertEqual(spec["runtime"]["env"].get("HIP_VISIBLE_DEVICES"), "0")
        self.assertEqual(spec["runtime"]["env"].get("SKIPPY_AMDGPU_TARGETS"), "gfx1151")
        self.assertTrue(spec["runtime"]["env"].get("PATH", "").startswith("/opt/rocm/bin"))
        self.assertEqual(spec["runtime"]["env"].get("LD_LIBRARY_PATH"), "/opt/rocm/lib")

    def test_models_must_be_ladder_ids(self):
        with self.assertRaises(ValueError):
            performance_plan.models(["qwen35-4b", "qwen35-4b"])
        with self.assertRaises(ValueError):
            performance_plan.models(["llama-70b"])


class CleanupTests(unittest.TestCase):
    def test_all_jobs_are_attempted_despite_individual_cancel_failures(self):
        from tempfile import TemporaryDirectory
        cancel = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["cancel"]
        with TemporaryDirectory() as directory:
            ledger = Path(directory) / "jobs.json"
            ledger.write_text(json.dumps(["job_1", "job_2", "job_3"]))
            pilot = Mock()
            pilot.api.side_effect = [RuntimeError("GET failed"), {"status": "running"},
                                     RuntimeError("POST failed"), {"status": "queued"}, {}]
            with self.assertRaisesRegex(RuntimeError, "job_1.*job_2"):
                cancel(pilot, ledger)
            self.assertEqual(pilot.api.call_args.args, ("POST", "/v1/jobs/job_3/cancel"))

    def test_dispatch_retains_original_error_when_cleanup_fails(self):
        from tempfile import TemporaryDirectory
        dispatch = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["dispatch"]
        with TemporaryDirectory() as directory:
            evidence = Path(directory)
            (evidence / "plan.json").write_text(json.dumps({"jobs": [{"spec": {}, "label": "test"}]}))
            pilot = Mock()
            pilot.api.side_effect = RuntimeError("original dispatch failure")
            with patch.dict(dispatch.__globals__, Pilot=lambda: pilot,
                            cancel=Mock(side_effect=RuntimeError("cleanup failure"))), patch("sys.stderr") as stderr:
                with self.assertRaisesRegex(RuntimeError, "original dispatch failure"):
                    dispatch(evidence)
                self.assertIn("cleanup failure", "".join(str(call) for call in stderr.write.call_args_list))


class ServerTests(unittest.TestCase):
    skippy = {"label": "baseline", "kind": "skippy", "binary": "/b/skippy"}
    server = {"id": "lanes4-pool16k", "lanes": 4, "pool_tokens": 16384}

    def test_an_older_skippy_runs_without_settings_it_lacks_and_says_so(self):
        help_text = " ".join(REQUIRED) + "  --generation-concurrency <N>\n  --adaptive-generation-concurrency\n"
        args, applied, missing = server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertIn("--generation-concurrency", args)
        self.assertEqual(applied["generation_concurrency"], 4)
        self.assertEqual(missing, ["--speculative-strategy", "--kv-pool-tokens", "--debug"])

    def test_measured_servers_run_at_debug_verbosity_for_offload_evidence(self):
        help_text = (" ".join(REQUIRED) + " --debug --generation-concurrency <N>"
                     " --speculative-strategy <S> --kv-pool-tokens <N>\n")
        args, applied, missing = server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertEqual(args.count("--debug"), 1)
        self.assertNotIn("None", args)
        self.assertIs(applied["debug"], True)
        self.assertEqual(missing, [])

    def test_required_options_are_probed_before_any_measured_round(self):
        for missing in REQUIRED:
            help_text = " ".join(flag for flag in REQUIRED if flag != missing)
            with patch("lib.performance_arms.command", return_value=help_text):
                with self.assertRaisesRegex(ValueError, "unsupported.*" + missing):
                    capabilities(self.skippy)
            with self.assertRaisesRegex(ValueError, "unsupported"):
                server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, self.server)

    def test_a_variant_passes_its_flags_to_skippy_only_and_needs_them(self):
        variant = dict(self.server, id="v", skippy_flags={"prefill-chunk-size": 2048, "microbatch-size": 2048})
        help_text = " ".join(REQUIRED) + " --prefill-chunk-size <N> --microbatch-size <VALUE>\n"
        args, applied, _ = server_args(self.skippy, help_text, "/m.gguf", "org/m@rev", 8192, variant)
        self.assertEqual(args[args.index("--prefill-chunk-size") + 1], "2048")
        self.assertEqual(args[args.index("--microbatch-size") + 1], "2048")
        self.assertEqual(applied["microbatch_size"], 2048)
        reference = {"label": "reference", "kind": "llama-server", "binary": "/b/llama-server"}
        args, _, _ = server_args(reference, "", "/m.gguf", "org/m@rev", 8192, variant)
        self.assertNotIn("--prefill-chunk-size", args)
        with self.assertRaisesRegex(ValueError, "unsupported.*--microbatch-size"):
            server_args(self.skippy, " ".join(REQUIRED) + " --prefill-chunk-size <N>", "/m.gguf",
                        "org/m@rev", 8192, variant)

    def test_llama_server_gets_the_same_cells(self):
        built = {"label": "reference", "kind": "llama-server", "binary": "/b/llama-server"}
        args, applied, _ = server_args(built, "", "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertEqual(args[args.index("-c") + 1], "16384")
        unpooled = dict(self.server, pool_tokens=None)
        _, applied, _ = server_args(built, "", "/m.gguf", "org/m@rev", 8192, unpooled)
        self.assertEqual(applied["total_context"], 4 * 8192)

    def test_llama_server_starts_at_trace_verbosity_and_records_it(self):
        built = {"label": "reference", "kind": "llama-server", "binary": "/b/llama-server"}
        args, applied, _ = server_args(built, "", "/m.gguf", "org/m@rev", 8192, self.server)
        self.assertEqual(args[args.index("-lv") + 1], "4")
        self.assertEqual(applied["log_verbosity"], 4)

    def test_only_llama_server_may_reject_a_point_and_only_past_its_pool(self):
        reference = {"label": "reference", "kind": "llama-server", "binary": "/b/llama-server"}
        cells = [(server, point) for server in performance_plan.scenario()["servers"]
                 for point in server["points"]]
        self.assertEqual([point["id"] for server, point in cells if may_reject(reference, server, point)],
                         ["c16-depth4096", "c4-depth4096-pool16k"])
        self.assertFalse(any(may_reject(self.skippy, server, point) for server, point in cells))


class BackendTests(unittest.TestCase):
    """The backend table drives every arm's build; one entry changes no other entry's commands."""
    skippy = {"label": "candidate", "kind": "skippy", "sha": "a" * 40, "path": "candidate"}
    reference = {"label": "reference", "kind": "llama-server", "sha": "c" * 40, "path": "reference"}

    def build(self, arm, backend):
        calls = []
        with TemporaryDirectory() as directory:
            with patch("lib.performance_arms.checkout", return_value=Path(directory)), \
                 patch("lib.performance_arms.command",
                       side_effect=lambda args, **kwargs: calls.append((args, kwargs)) or None), \
                 patch("lib.performance_arms.toolkit_overrides", return_value={"CUDACXX": "/c/bin/nvcc"}), \
                 patch("lib.performance_arms.digest", return_value="0" * 64):
                built = performance_arms.build(arm, Path(directory) / "build.log", backend)
        return calls, built

    def test_the_cuda_entry_builds_both_arms_for_a_gb10(self):
        calls, _ = self.build(self.skippy, "cuda")
        just, cargo = calls[0], calls[1]
        self.assertEqual(just[0][:2], ["just", "llama-build"])
        self.assertEqual((just[1]["overrides"]["LLAMA_STAGE_BACKEND"], just[1]["overrides"]["LLAMA_STAGE_LINK_MODE"],
                          just[1]["overrides"]["LLAMA_STAGE_CUDA_ARCHITECTURES"]), ("cuda", "static", "121"))
        self.assertEqual(cargo[0][:3], ["cargo", "build", "--locked"])
        self.assertEqual(cargo[1]["overrides"], just[1]["overrides"])
        calls, _ = self.build(self.reference, "cuda")
        configure, make = calls[0], calls[1]
        self.assertIn("-DGGML_CUDA=ON", configure[0])
        self.assertIn("-DCMAKE_CUDA_ARCHITECTURES=121", configure[0])
        self.assertNotIn("-DGGML_METAL=ON", configure[0])
        self.assertEqual(configure[1]["overrides"], {"CUDACXX": "/c/bin/nvcc"})
        self.assertEqual(make[1]["overrides"], {"CUDACXX": "/c/bin/nvcc"})

    def test_the_metal_entry_builds_the_reference_without_a_toolkit_environment(self):
        calls, _ = self.build(self.reference, "metal")
        self.assertIn("-DGGML_METAL=ON", calls[0][0])
        self.assertNotIn("-DGGML_CUDA=ON", calls[0][0])
        self.assertIsNone(calls[0][1]["overrides"])

    def test_the_cuda_x86_entry_builds_for_the_hosts_own_architecture(self):
        calls = []
        with patch("lib.performance_backends.cuda_x86_architecture", return_value="89"):
            with TemporaryDirectory() as directory:
                with patch("lib.performance_arms.checkout", return_value=Path(directory)), \
                     patch("lib.performance_arms.command",
                           side_effect=lambda args, **kwargs: calls.append((args, kwargs)) or None), \
                     patch("lib.performance_arms.toolkit_overrides",
                           return_value={"CUDACXX": "/c/bin/nvcc"}), \
                     patch("lib.performance_arms.digest", return_value="0" * 64):
                    built = performance_arms.build(self.skippy, Path(directory) / "b.log", "cuda-x86")
                    self.assertEqual(calls[0][1]["overrides"]["LLAMA_STAGE_CUDA_ARCHITECTURES"], "89")
                    self.assertEqual(built["gpu_architecture"], "89")
                    del calls[:]
                    built = performance_arms.build(self.reference, Path(directory) / "r.log", "cuda-x86")
            self.assertIn("-DCMAKE_CUDA_ARCHITECTURES=89", calls[0][0])
            self.assertEqual(built["gpu_architecture"], "89")

    def test_cuda_x86_admission_takes_the_lesser_of_gpu0_free_vram_and_host_memory(self):
        meminfo = "MemAvailable: 2097152 kB\n"
        for free_vram, expected in ((10 * 1024**3, 2 * 1024**3), (1 * 1024**3, 1 * 1024**3)):
            with patch("lib.performance_backends.Path") as path:
                path.return_value.read_text.return_value = meminfo
                self.assertEqual(performance_backends.cuda_x86_free(
                    {"gpu_memory_free_bytes": free_vram}), expected)

    def test_the_rocm_entry_builds_both_arms_for_the_pinned_gfx_target(self):
        calls = []
        with TemporaryDirectory() as directory:
            with patch("lib.performance_arms.checkout", return_value=Path(directory)), \
                 patch("lib.performance_arms.command",
                       side_effect=lambda args, **kwargs: calls.append((args, kwargs)) or None), \
                 patch("lib.performance_arms.digest", return_value="0" * 64):
                built = performance_arms.build(self.skippy, Path(directory) / "b.log", "rocm")
                self.assertEqual(calls[0][1]["overrides"]["LLAMA_STAGE_BACKEND"], "rocm")
                self.assertEqual(calls[0][1]["overrides"]["LLAMA_STAGE_AMDGPU_TARGETS"], "gfx1151")
                self.assertEqual(built["gpu_architecture"], "gfx1151")
                del calls[:]
                performance_arms.build(self.reference, Path(directory) / "r.log", "rocm")
        self.assertIn("-DGGML_HIP=ON", calls[0][0])
        self.assertIn("-DAMDGPU_TARGETS=gfx1151", calls[0][0])
        self.assertNotIn("-DGGML_CUDA=ON", calls[0][0])

    def test_rocm_admission_takes_the_lesser_of_memory_and_free_vram_plus_gtt(self):
        gib = 1024**3
        host = {"vram_total_bytes": 90 * gib, "vram_used_bytes": 10 * gib,
                "gtt_total_bytes": 80 * gib, "gtt_used_bytes": 20 * gib}
        with patch("lib.performance_backends.Path") as path:
            path.return_value.read_text.return_value = "MemAvailable: 104857600 kB\n"  # 100 GiB
            self.assertEqual(performance_backends.rocm_free(host), 100 * gib)
        with patch("lib.performance_backends.Path") as path:
            path.return_value.read_text.return_value = "MemAvailable: 209715200 kB\n"  # 200 GiB
            self.assertEqual(performance_backends.rocm_free(host), 140 * gib)


class RejectionTests(unittest.TestCase):
    point = {"id": "c4-depth4096-pool16k", "concurrency": 4, "depth": 4096, "input_tokens": 512,
             "output_tokens": 128, "requests": 4}
    refused = {"code": 500, "type": "Internal Server Error", "message": "Context size has been exceeded."}
    summary = {"request_count": {"avg": 4}, "request_error_rate": {"avg": 0}, "error_summary": [],
               "is_complete": True, "output_token_throughput": {"avg": 120.5}}

    def write(self, directory, failed=(), served=0, cut_short=0):
        """AIPerf's per-request export: two warmup requests, then the measured ones."""
        def request(phase, error, metrics):
            return {"metadata": {"benchmark_phase": phase}, "error": error, "metrics": metrics}

        whole = {"output_sequence_length": {"value": 128, "unit": "tokens"}}
        records = ([request("warmup", None, whole)] * 2
                   + [request("profiling", error, {}) for error in failed]
                   + [request("profiling", None, whole)] * served
                   + [request("profiling", None, {"request_latency": {"value": 9.5, "unit": "ms"}})] * cut_short)
        (Path(directory) / "profile_export.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def drive(self, directory, may_reject):
        return run_point("http://127.0.0.1:9337", self.point, "org/m@rev", "/t", 2, Path(directory),
                         may_reject=may_reject)

    def test_rejection_counts_failed_and_cut_short_requests_among_the_measured_ones(self):
        from tempfile import TemporaryDirectory
        other = dict(self.refused, code=503)
        with TemporaryDirectory() as directory:
            self.write(directory, failed=[self.refused, other, self.refused], cut_short=1)
            self.assertEqual(rejection(Path(directory), 4), {
                "requests": 4, "failed": 3, "cut_short": 1,
                "errors": [{"error_details": self.refused, "count": 2}, {"error_details": other, "count": 1}]})
            # A load run that broke off before every request is not the server's answer.
            self.assertIsNone(rejection(Path(directory), 5))
        with TemporaryDirectory() as directory:
            self.assertIsNone(rejection(Path(directory), 4))
            self.write(directory, served=4)
            self.assertIsNone(rejection(Path(directory), 4))

    def test_a_point_the_server_may_reject_keeps_what_it_rejected_in_place_of_metrics(self):
        from subprocess import CalledProcessError
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            self.write(directory, failed=[self.refused] * 4)
            # AIPerf exits non-zero, with no summary export, when every measured request failed.
            with patch("lib.performance_load.command", side_effect=CalledProcessError(1, ["aiperf"])):
                metrics, rejected = self.drive(directory, may_reject=True)
                self.assertEqual((metrics["output_tok_s"], set(metrics.values())), (None, {None}))
                self.assertEqual((rejected["failed"], rejected["cut_short"]), (4, 0))
                with self.assertRaises(CalledProcessError):
                    self.drive(directory, may_reject=False)
        with TemporaryDirectory() as directory:
            self.write(directory, failed=[self.refused], served=2, cut_short=1)
            (Path(directory) / "profile_export_aiperf.json").write_text(json.dumps(self.summary))
            with patch("lib.performance_load.command"):
                self.assertEqual(self.drive(directory, may_reject=True)[1]["cut_short"], 1)

    def test_a_point_the_server_served_is_measured_and_a_broken_load_run_still_fails(self):
        from subprocess import CalledProcessError
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            self.write(directory, served=4)
            (Path(directory) / "profile_export_aiperf.json").write_text(json.dumps(self.summary))
            with patch("lib.performance_load.command"):
                metrics, rejected = self.drive(directory, may_reject=True)
            self.assertEqual((metrics["output_tok_s"], rejected), (120.5, None))
            with patch("lib.performance_load.command", side_effect=CalledProcessError(1, ["aiperf"])):
                with self.assertRaises(CalledProcessError):
                    self.drive(directory, may_reject=True)

    def test_a_rejected_point_is_a_sample_only_while_its_server_stays_up(self):
        from tempfile import TemporaryDirectory
        measure = run_path(str(Path(__file__).resolve().parents[1] / "inference-performance.py"))["measure"]
        built = {"label": "reference", "kind": "llama-server", "binary": "/b/llama-server"}
        server = {"id": "lanes4-pool16k", "lanes": 4, "pool_tokens": 16384, "points": [self.point]}
        inputs = {"repo": "org/m", "revision": "rev", "tokenizer_path": "/t"}
        shape = {"context_per_lane": 8192, "warmup_requests": 2}
        rejected = {"requests": 4, "failed": 4, "cut_short": 0, "errors": []}
        process, load = Mock(), Mock(return_value=({"output_tok_s": None}, rejected))
        with TemporaryDirectory() as directory, patch.dict(
                measure.__globals__, start=Mock(return_value=process), ready=Mock(), offload=Mock(),
                stop=Mock(), run_point=load):
            process.poll.return_value = None
            sample, = measure(built, "", "/m.gguf", inputs, "m", 0, server, shape, Path(directory))
            self.assertEqual((sample["rejected"], sample["metrics"]), (rejected, {"output_tok_s": None}))
            self.assertTrue(load.call_args.kwargs["may_reject"])
            process.poll.return_value = 1
            with self.assertRaisesRegex(RuntimeError, "exited while it was rejecting"):
                measure(built, "", "/m.gguf", inputs, "m", 0, server, shape, Path(directory))


class ReportTests(unittest.TestCase):
    record = {"started_at": "2026-11-21T10:00:00Z", "arms": [{"label": "candidate", "sha": "a" * 40}]}

    def export(self, **overrides):
        value = {"request_count": {"avg": 16}, "request_error_rate": {"avg": 0}, "error_summary": [],
                 "is_complete": True, "output_token_throughput": {"avg": 120.5},
                 "time_to_first_token": {"p50": 80, "p95": 95}}
        value.update(overrides)
        return value

    def test_measurements_reject_failed_requests_and_keep_missing_metrics_null(self):
        metrics = measurements(self.export())
        self.assertEqual(metrics["output_tok_s"], 120.5)
        self.assertIsNone(metrics["itl_ms_p50"])
        with self.assertRaises(ValueError):
            measurements(self.export(request_error_rate={"avg": 0.25}))

    def test_medians_spreads_and_verdicts(self):
        def sample(arm, value):
            return {"model": "m", "server": "s", "point": "p", "arm": arm, "metrics": {"output_tok_s": value},
                    "rejected": None}

        rows = summarize([sample("baseline", v) for v in (100, 102, 98)]
                         + [sample("candidate", v) for v in (120, 118, 121)])
        delta = rows[0]["candidate_vs_baseline"]
        self.assertAlmostEqual(delta["percent"], 20.0)
        self.assertFalse(delta["ranges_overlap"])
        self.assertIn("+20.00% · Improved", markdown("t", "h", rows, self.record))
        close = summarize([sample("baseline", v) for v in (100, 110)] + [sample("candidate", v) for v in (105, 108)])
        self.assertIn("round ranges overlap", markdown("t", "h", close, self.record))
        self.assertNotIn("llama.cpp change", markdown("t", "h", rows, self.record))

    def test_a_moved_pin_says_whether_llama_cpp_moved_too(self):
        def sample(arm, value):
            return {"model": "m", "server": "s", "point": "p", "arm": arm, "metrics": {"output_tok_s": value},
                    "rejected": None}

        rows = summarize([sample(arm, v) for arm, values in (("baseline", (100, 101, 99)),
                                                            ("candidate", (90, 91, 89)),
                                                            ("baseline-reference", (200, 201, 199)),
                                                            ("reference", (180, 181, 179)))
                          for v in values])
        self.assertAlmostEqual(rows[0]["reference_vs_baseline_reference"]["percent"], -10.0)
        report = markdown("t", "h", rows, self.record)
        self.assertIn("| Baseline reference | Change | llama.cpp change |", report)
        self.assertIn("-10.00% · Regressed", performance_report.llama_change(rows[0]))

    def test_one_percent_rule_keeps_range_uncertainty_and_actual_p50_labels(self):
        def row(metric, candidate):
            return summarize([{"model": "m", "server": "s", "point": "p", "arm": arm,
                               "metrics": {metric: value}, "rejected": None}
                              for arm, values in (("baseline", (90, 100, 110)),
                                                  ("candidate", (candidate - 10, candidate, candidate + 10)))
                              for value in values])[0]
        for metric, within, improved, regressed in (("output_tok_s", 101, 102, 98),
                                                   ("ttft_ms_p50", 99, 98, 102)):
            for value, expected in ((within, "Within tolerance"), (improved, "Improved"), (regressed, "Regressed")):
                result = performance_report.verdict(row(metric, value))
                self.assertIn(expected, result)
                self.assertIn("round ranges overlap", result)
        report_rows = [row("ttft_ms_p50", 102), row("output_tok_s", 102)]
        extra = {**report_rows[0], "server": "s2", "point": "p2"}
        extra["arms"] = {**extra["arms"], "reference": {"median": 70, "min": 70, "max": 70, "n": 3}}
        report = markdown("t", "Apple M1 Ultra", [*report_rows, extra], self.record)
        for text in ("2 regressions", "1 improvements", "Tolerance: ±1%", "Time to first token (p50)",
                     "m · s2 · p2", "Apple M1 Ultra", "Reference", "```mermaid", "## Latest numbers"):
            self.assertIn(text, report)
        for text in ("p95", "Two trials", "One Spark", "Performance over time", "M3 Ultra"):
            self.assertNotIn(text, report)
        self.assertEqual(report.count("xychart-beta"), 3)
        self.assertIn('bar [100.00000000, 102.00000000, 70.00000000]', report)
        with self.assertRaises(ValueError):
            summarize([{"model": "m", "server": "s", "point": "p", "arm": "candidate",
                        "metrics": {"output_tok_s": float("nan")}, "rejected": None}])

    def test_rejected_rounds_are_counted_beside_the_arms_that_measured_the_point(self):
        rejected = {"requests": 16, "failed": 14, "cut_short": 2, "errors": []}

        def sample(arm, value, rejected=None):
            return {"model": "m", "server": "s", "point": "p", "arm": arm, "metrics": {"output_tok_s": value},
                    "rejected": rejected}

        rows = summarize([sample("baseline", v) for v in (100, 102, 98)]
                         + [sample("candidate", v) for v in (120, 118, 121)]
                         + [sample("reference", None, rejected)] * 3)
        self.assertEqual((rows[0]["rejected"], sorted(rows[0]["arms"])),
                         ({"reference": 3}, ["baseline", "candidate"]))
        self.assertIn("| rejected (n=3) |", markdown("t", "h", rows, self.record))
        self.assertEqual(performance_report.cell({"median": 70, "min": 68, "max": 72, "n": 2}, 1),
                         "70.0 (68.0–72.0, n=2); rejected n=1")
        self.assertEqual(performance_report.cell(None, 0), "—")

    def test_stored_rows_carry_their_arm_point_and_host_but_not_the_trace_pass(self):
        shape = performance_plan.scenario()
        server = shape["servers"][0]
        point = server["points"][0]
        measured = {"model": "qwen35-4b", "round": 2, "arm": "baseline", "server": server["id"],
                    "point": point["id"], "applied": {}, "unsupported": [],
                    "metrics": {"output_tok_s": 99.5, "ttft_ms_p50": None}, "rejected": None}
        record = {"harness": "d" * 40, "scenario_sha256": "e" * 64, "started_at": "2026-11-21T10:00:00+00:00",
                  "host": {"chip": "Apple M1 Ultra", "node": "n1"},
                  "arms": [{"label": "baseline", "kind": "skippy", "sha": "b" * 40, "binary_sha256": "f" * 64},
                           {"label": "candidate", "kind": "skippy", "sha": "a" * 40, "binary_sha256": "f" * 64}],
                  "models": {"qwen35-4b": {"revision": "r1"}}, "samples": [measured, {**measured, "arm": "candidate"}],
                  "diagnostic": {"samples": [{**measured, "round": None}]}}
        row, pulled = performance_report.rows(record, shape, {"b" * 40: "2026-11-20T10:16:30Z"}, "job_1",
                                              {"candidate": 130, "baseline": None})
        branched, _ = performance_report.rows(record, shape, {}, "job_1", {"candidate": None, "baseline": None},
                                              {"baseline": "team/gen-ai", "candidate": None})
        self.assertEqual((branched["on_main"], branched["branch"], branched["pull_request"]),
                         (False, "team/gen-ai", None))
        self.assertEqual((row["sha"], row["committed_at"], row["round"]), ("b" * 40, "2026-11-20T10:16:30Z", 2))
        self.assertEqual((row["on_main"], row["pull_request"], row["branch"]), (True, None, None))
        self.assertEqual((pulled["sha"], pulled["on_main"], pulled["pull_request"]), ("a" * 40, False, 130))
        self.assertEqual((row["lanes"], row["concurrency"], row["depth"]),
                         (server["lanes"], point["concurrency"], point["depth"]))
        self.assertEqual((row["output_tok_s"], row["ttft_ms_p50"], row["chip"]), (99.5, None, "Apple M1 Ultra"))
        self.assertIsNone(row["rejected"])
        json.dumps(row)
        rejected = {"requests": 8, "failed": 8, "cut_short": 0, "errors": []}
        record["samples"] = [{**measured, "metrics": {"output_tok_s": None}, "rejected": rejected}]
        stored, = performance_report.rows(record, shape, {}, "job_1", {})
        self.assertEqual((stored["output_tok_s"], json.loads(stored["rejected"])), (None, rejected))


if __name__ == "__main__":
    unittest.main()
