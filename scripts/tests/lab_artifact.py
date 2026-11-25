"""Offline contracts for the hosted-to-lab binary boundary; no hardware required."""

import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lib import ci_artifacts as artifacts, ci_smoke, inference_checks, lab_artifact as lab

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("lab_command", ROOT / "scripts/lab-artifact.py")
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)
SHA = "a" * 40


def run(**changes):
    return dict({"id": 123, "run_attempt": 2, "head_sha": SHA, "head_branch": "main",
                 "head_repository": {"full_name": lab.REPOSITORY}, "path": lab.WORKFLOW,
                 "event": "workflow_dispatch", "status": "completed", "conclusion": "success"}, **changes)


def github(path, **_kwargs):
    if path == "branches/main":
        return {"protected": True, "commit": {"sha": "d" * 40}}
    if path.startswith("compare/"):
        sha = path.removeprefix("compare/").split("...")[0]
        return {"status": "ahead", "merge_base_commit": {"sha": sha}}
    if "/jobs?" in path:
        return {"total_count": 4, "jobs": [{"name": name, "conclusion": "success"}
                for name in ("plan", "quality", "package", "handoff")]}
    if "/status?" in path:
        return [{"statuses": [{"context": "lab/metal/123.2", "state": "pending"}]}]
    if path == "check-runs":
        return {"html_url": "https://github.com/Mesh-LLM/skippy/runs/456"}
    return run()


class Admission(unittest.TestCase):
    def test_only_successful_main_dispatches(self):
        self.assertEqual(lab.admitted_run(run()), {"run_id": 123, "attempt": 2, "source": SHA})
        for key, values in {
            "head_branch": ["topic", "refs/heads/main"], "head_sha": ["main", "a" * 39],
            "head_repository": [{"full_name": "fork/skippy"}, {}], "path": ["ci.yml"],
            "event": ["pull_request", "pull_request_target", "push"],
            "status": ["in_progress"], "conclusion": ["failure", "cancelled", None],
            "id": [0, True, "123"], "run_attempt": [0, True, "2"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    lab.admitted_run(run(**{key: value}))

    def test_rerun_invalidates_old_receipt(self):
        with patch.object(lab, "ready", return_value=lab.admitted_run(run(run_attempt=3))):
            with self.assertRaisesRegex(ValueError, "superseded"):
                lab.current(lab.admitted_run(run()))

    def test_tag_named_main_and_missing_handoff_cannot_authorize_lab(self):
        for status in ("behind", "diverged"):
            with (patch.object(lab, "api", side_effect=[{"protected": True, "commit": {"sha": "d" * 40}},
                                                       {"status": status, "merge_base_commit": {"sha": SHA}}]),
                  self.assertRaisesRegex(ValueError, "not on protected main")):
                lab.main_ancestor(SHA)
        jobs = github("/jobs?")
        jobs["jobs"][-1]["conclusion"] = "skipped"
        with (patch.object(lab, "main_ancestor"), patch.object(lab, "api", side_effect=[run(), jobs]),
              self.assertRaisesRegex(ValueError, "handoff")):
            lab.ready(123)

    def test_candidate_receipt_does_not_require_candidate_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "skippy").write_bytes(b"binary")
            with patch.object(artifacts, "source", return_value=SHA):
                artifacts.receipt(root, "product", "target", lab.VARIANT)
            with patch.object(artifacts, "source", side_effect=AssertionError("must not read candidate git")):
                artifacts.verify(root, "product", "target", lab.VARIANT, expected_source=SHA)
                for sha in ("b" * 40, "main"):
                    with self.assertRaises(ValueError):
                        artifacts.verify(root, "product", "target", lab.VARIANT, expected_source=sha)
                (root / "skippy").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    artifacts.verify(root, "product", "target", lab.VARIANT, expected_source=SHA)


class Downloads(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.product = self.root / f"skippy-{lab.VARIANT}.tar.gz"
        self.product.write_bytes(b"test product bytes")
        artifacts.write_checksum(self.product)
        self.zip = self.root / "download.zip"
        with zipfile.ZipFile(self.zip, "w") as zipped:
            for path in (self.product, self.product.with_name(self.product.name + ".sha256")):
                zipped.write(path, path.name)
        self.name = "hosted-lab-123-2-input"
        self.item = {"id": 55, "name": self.name, "expired": False,
                     "digest": "sha256:" + artifacts.digest(self.zip)}
        self.destination = self.root / "downloaded"

    def api(self, path):
        if "/artifacts?" in path:
            return {"total_count": 1, "artifacts": [self.item]}
        return github(path)

    def download(self, args, **kwargs):
        self.assertEqual(args[-1], f"repos/{lab.REPOSITORY}/actions/artifacts/55/zip")
        kwargs["stdout"].write(self.zip.read_bytes())

    def fetch(self):
        with (patch.object(lab, "api", side_effect=self.api),
              patch.object(lab.subprocess, "run", side_effect=self.download)):
            return lab.fetch(123, self.destination)

    def test_exact_artifact_and_archive_identity(self):
        identity = self.fetch()
        self.assertEqual(identity["archive_sha256"], artifacts.digest(self.product))
        self.assertEqual(lab.verify_input(self.destination), identity)
        with self.assertRaisesRegex(ValueError, "must not exist"):
            self.fetch()
        (self.destination / self.product.name).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            lab.verify_input(self.destination)

    def test_bad_github_digest_leaves_no_admitted_directory(self):
        self.item["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "download digest"):
            self.fetch()
        self.assertFalse(self.destination.exists())

    def test_missing_expired_or_ambiguous_artifact_is_rejected(self):
        for listing in ({"total_count": 0, "artifacts": []},
                        {"total_count": 2, "artifacts": [self.item, self.item]},
                        {"total_count": 1, "artifacts": [dict(self.item, expired=True)]}):
            with (patch.object(lab, "ready", return_value=lab.admitted_run(run())),
                  patch.object(lab, "api", return_value=listing),
                  self.assertRaises(ValueError)):
                lab.fetch(123, self.destination)

    def test_producer_cannot_inject_lab_evidence(self):
        with zipfile.ZipFile(self.zip, "a") as zipped:
            zipped.writestr("evidence/lab-result.json", '{"status":"passed"}')
        self.item["digest"] = "sha256:" + artifacts.digest(self.zip)
        with self.assertRaisesRegex(ValueError, "only the product"):
            self.fetch()
        self.assertFalse(self.destination.exists())

    def test_unsafe_zip_entries_are_rejected(self):
        for name in ("../outside", "/outside", "a\\b", "C:/outside"):
            with self.subTest(name=name):
                with zipfile.ZipFile(self.zip, "w") as zipped:
                    zipped.writestr(name, b"bad")
                with self.assertRaises(ValueError):
                    lab.unpack_download(self.zip, self.root / "unpacked")
        link = zipfile.ZipInfo("link")
        link.external_attr = 0o120777 << 16
        with zipfile.ZipFile(self.zip, "w") as zipped:
            zipped.writestr(link, "outside")
        with self.assertRaises(ValueError):
            lab.unpack_download(self.zip, self.root / "unpacked")

    def report_fixture(self):
        identity = self.fetch()
        output = self.destination / "evidence"
        output.mkdir()
        artifacts.write_json(output / "quality.json", {"response": "x" * 70000})
        artifacts.write_json(output / "lab-result.json", {
            "input": identity, "status": "failed", "harness_source": SHA,
            "evidence": artifacts.files(output),
        })

    def test_large_evidence_is_readable_and_status_search_paginates(self):
        self.report_fixture()

        def pages(path, **kwargs):
            if "/status?" in path:
                self.assertTrue(kwargs["paginate"])
                return [{"statuses": []}, *github(path)]
            return github(path, **kwargs)

        with patch.object(lab, "api", side_effect=pages) as api:
            lab.report(self.destination)
            check = next(call.kwargs["body"] for call in api.call_args_list if call.args == ("check-runs",))
            summary = check["output"]["summary"]
            self.assertLess(len(summary.encode()), 60000)
            self.assertIn('"truncated": true', summary)

    def test_completed_status_or_failed_evidence_upload_cannot_publish_green(self):
        self.report_fixture()
        for terminal in (True, False):
            def reject(path, **kwargs):
                if terminal and "/status?" in path:
                    return [{"statuses": [{"context": "lab/metal/123.2", "state": "failure"}]}]
                if path == "check-runs":
                    raise RuntimeError("publication unavailable")
                return github(path, **kwargs)

            with patch.object(lab, "api", side_effect=reject) as api:
                with self.assertRaises((ValueError, RuntimeError)):
                    lab.report(self.destination)
                self.assertFalse(any(call.args[0].startswith("statuses/") for call in api.call_args_list))

    def test_failed_and_successful_results_are_bound_to_inputs_and_evidence(self):
        identity = self.fetch()
        for failure in (True, False):
            with self.subTest(failure=failure):
                output = self.destination / "evidence"
                output.mkdir(exist_ok=True)
                (output / "lab-result.json").unlink(missing_ok=True)

                def smoke(*args, **kwargs):
                    self.assertEqual(kwargs["expected_source"], SHA)
                    self.assertIs(kwargs["probe"], CLI.probe)
                    self.assertEqual(set(kwargs["child_env"]), {"PATH", "HOME", "USERPROFILE", "LANG"})
                    if failure:
                        raise RuntimeError("device unavailable")
                    artifacts.write_json(output / "smoke.json", {"model_sha256": "c" * 64})
                    artifacts.write_json(output / "performance.json", {
                        "samples": [{"end_to_end_tokens_per_second": n} for n in (10, 20, 30)]})

                with (patch.object(CLI.artifacts, "source", return_value="b" * 40),
                      patch.object(CLI, "capture", return_value=""),
                      patch.object(CLI, "hardware", return_value={"chip": "fixture"}),
                      patch.object(CLI, "targets", return_value=({"host": {}}, {lab.VARIANT: {"host": "host"}})),
                      patch.object(CLI, "smoke", side_effect=smoke),
                      patch("sys.stdout", new=io.StringIO())):
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, "device unavailable"):
                            CLI.qualify(self.destination)
                    else:
                        CLI.qualify(self.destination)
                result = artifacts.read_json(output / "lab-result.json")
                self.assertEqual(result["input"], identity)
                self.assertEqual(result["status"], "failed" if failure else "passed")
                self.assertEqual(result["harness_source"], "b" * 40)
                if not failure:
                    self.assertEqual(result["median_end_to_end_tokens_per_second"], 20)
                with patch.object(lab, "api", side_effect=github) as api:
                    lab.report(self.destination)
                    body = api.call_args.kwargs["body"]
                    self.assertEqual(body["context"], "lab/metal/123.2")
                    self.assertEqual(body["state"], "failure" if failure else "success")
                    self.assertEqual(body["target_url"], "https://github.com/Mesh-LLM/skippy/runs/456")
                    published = next(call.kwargs["body"] for call in api.call_args_list if call.args == ("check-runs",))
                    self.assertIn(identity["archive_sha256"], published["output"]["summary"])
                (output / "extra.log").write_text("changed after qualification")
                with patch.object(lab, "api", side_effect=github), self.assertRaisesRegex(ValueError, "evidence changed"):
                    lab.report(self.destination)
                (output / "extra.log").unlink()


class Harness(unittest.TestCase):
    def test_version_probe_receives_only_explicit_child_environment(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(ci_smoke, "require_host"), patch.object(artifacts, "unpack"),
              patch.object(artifacts, "verify"), patch.object(ci_smoke, "command") as command,
              patch.dict("os.environ", {"GH_TOKEN": "fixture-secret", "GITHUB_TOKEN": "fixture-secret"})):
            root = Path(directory)
            env = {"PATH": "/usr/bin:/bin", "HOME": str(root)}
            ci_smoke.verify_product({"target": "target", "os": "macos"}, {"id": lab.VARIANT},
                                    root, root, expected_source=SHA, child_env=env)
            self.assertEqual(command.call_args.kwargs["overrides"], env)
            self.assertIs(command.call_args.kwargs["inherit_env"], False)

    def test_score_uses_explicit_endpoint(self):
        reply = json.dumps({"choices": [{"message": {"content": "42"}}], "usage": {"completion_tokens": 1}})
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(inference_checks, "read_json", return_value=[{"prompt": "17+25", "expected": "42"}]),
              patch.object(inference_checks, "curl", return_value=reply) as curl):
            inference_checks.score("qwen3-ci", Path(directory), "pr", endpoint="http://127.0.0.1:9000")
            self.assertEqual(curl.call_count, 2)
            self.assertEqual(curl.call_args.args[0][-1], "http://127.0.0.1:9000/v1/chat/completions")

    def test_performance_is_bounded_and_excludes_warmup(self):
        reply = json.dumps({"usage": {"completion_tokens": 8}, "choices": [{"message": {"content": "1,2,3"}}]})
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(CLI, "score") as score,
              patch.object(CLI, "request", return_value=reply) as request,
              patch.object(CLI.time, "monotonic", side_effect=range(8))):
            samples = CLI.probe("http://127.0.0.1:9000/v1", Path(directory))
            self.assertEqual(len(samples), 3)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(score.call_args.kwargs["endpoint"], "http://127.0.0.1:9000")
            self.assertTrue(all(row["end_to_end_tokens_per_second"] == 8 for row in samples))

    def test_workflow_has_no_lab_runner_or_dispatch_credentials(self):
        workflow = (ROOT / lab.WORKFLOW).read_text()
        self.assertNotIn("self-hosted", workflow)
        self.assertNotIn("JIANYANG", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("runs-on: macos-15", workflow)
        self.assertIn("run: just test", workflow)
        self.assertIn("run: just llama-check", workflow)
        self.assertIn("smoke: artifact", workflow)
        self.assertIn("state: 'pending'", workflow)
        self.assertNotIn("state: 'success'", workflow)


if __name__ == "__main__":
    unittest.main()
