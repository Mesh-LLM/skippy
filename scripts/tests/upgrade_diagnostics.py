"""Failed-CI evidence collection across the JY and current GitHub CLIs."""

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('diagnostics', ROOT / 'scripts/upgrade-diagnostics.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class Diagnostics(unittest.TestCase):
    def collect(self, root, *, modern=False, fail=''):
        binary = root / 'bin'
        binary.mkdir()
        calls = root / 'calls.jsonl'
        executable = binary / 'gh'
        executable.write_text(
            f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n'
            f'with Path({str(calls)!r}).open("a") as stream: stream.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'args=sys.argv[1:]\nassert args[0]=="api"\n'
            'assert "--slurp" not in args, "unknown flag: --slurp"\n'
            f'modern={modern!r}\nfail={fail!r}\n'
            'if args==["api","--help"]:\n'
            '    print("--allow-escape-sequences" if modern else "--paginate --jq")\n'
            'elif "--paginate" in args:\n'
            '    assert args[args.index("--jq")+1]==\'.jobs[] | select(.conclusion == "failure") | {id, name, steps} | tojson\'\n'
            '    if fail=="listing": sys.exit(1)\n'
            '    for identifier in (137156371637,137362147708): print(json.dumps({"id":identifier,"name":"qualify / Quality canary","steps":[{"name":"Run just test","conclusion":"failure"}]}))\n'
            'else:\n'
            '    assert ("--allow-escape-sequences" in args)==modern\n'
            '    if fail=="first-log" and "137156371637" in args[-1]: sys.exit(1)\n'
            '    print("\\x1b[31mundefined symbol: llama_set_embeddings_nextn\\x1b[0m")\n')
        executable.chmod(0o755)
        output = root / 'evidence'
        output.mkdir()
        (output / 'quality-evidence.txt').write_text('existing downloaded artifact\n')
        with patch.dict(os.environ, GITHUB_RUN_ID='45296606108', PATH=str(binary) + os.pathsep + os.environ['PATH']):
            with redirect_stdout(io.StringIO()):
                diagnostics.collect(output)
        return output, json.loads((output / 'actions-log-collection.json').read_text())

    @unittest.skipIf(os.name == 'nt', 'Unix executable fixture matches the JY runner')
    def test_old_and_current_cli_collect_all_paginated_failure_logs(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as tmp:
                output, status = self.collect(Path(tmp), modern=modern)
                self.assertEqual(status['downloaded_jobs'], [137156371637, 137362147708])
                self.assertEqual(status['errors'], [])
                self.assertIn('\x1b[31mundefined symbol', (output / 'actions-137156371637.log').read_text())
                self.assertEqual((output / 'quality-evidence.txt').read_text(), 'existing downloaded artifact\n')

    @unittest.skipIf(os.name == 'nt', 'Unix executable fixture matches the JY runner')
    def test_missing_log_keeps_other_logs_and_evidence_for_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, status = self.collect(Path(tmp), fail='first-log')
            self.assertEqual(status['downloaded_jobs'], [137362147708])
            self.assertEqual(len(status['errors']), 1)
            self.assertIn('job 137156371637', status['errors'][0])
            self.assertTrue((output / 'quality-evidence.txt').is_file())

    @unittest.skipIf(os.name == 'nt', 'Unix executable fixture matches the JY runner')
    def test_listing_failure_records_missing_logs_and_keeps_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, status = self.collect(Path(tmp), fail='listing')
            self.assertEqual(status['downloaded_jobs'], [])
            self.assertEqual(len(status['errors']), 1)
            self.assertTrue((output / 'quality-evidence.txt').is_file())

    def test_invalid_job_identity_is_not_used_as_a_log_path(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, GITHUB_RUN_ID='123'):
            for value in ('"../escape"', 'true', '0'):
                with self.subTest(value=value), patch.object(diagnostics, 'gh', side_effect=['--jq', json.dumps({'id': json.loads(value), 'name': 'qualify / test', 'steps': []})]) as gh:
                    with self.assertRaisesRegex(ValueError, 'invalid workflow job ID'):
                        diagnostics.collect(Path(tmp))
                    self.assertEqual(gh.call_count, 2)

    def test_later_round_excludes_stale_failures_and_exports_its_own_verdict(self):
        jobs = [
            {'id': 1, 'name': 'qualify / Quality canary', 'steps': [{'name': 'Run just test', 'conclusion': 'failure'}]},
            {'id': 2, 'name': 'requalify / Graph Contracts', 'steps': [{'name': 'Run just llama-check', 'conclusion': 'failure'}]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            with patch.dict(os.environ, GITHUB_RUN_ID='123', GITHUB_OUTPUT=str(output / 'outputs')), \
                 patch.object(diagnostics, 'gh', side_effect=['--jq', '\n'.join(map(json.dumps, jobs)),
                                                            'error: undeclared LLM_FUSED_OP_LIGHTNING_INDEXER']) as gh, \
                 redirect_stdout(io.StringIO()):
                diagnostics.collect(output, 'requalify / ')
            self.assertEqual(json.loads((output / 'actions-log-collection.json').read_text())['downloaded_jobs'], [2])
            self.assertTrue(json.loads((output / 'ci-failure.json').read_text())['repairable'])
            self.assertIn('repairable=true', (output / 'outputs').read_text())
            self.assertEqual(gh.call_count, 3)
            self.assertTrue(gh.call_args.args[-1].endswith('/2/logs'))


if __name__ == '__main__':
    unittest.main()
