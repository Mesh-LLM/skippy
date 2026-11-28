"""Exercise complete offline gates, repeated feedback and bounded CI selection."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from upgrade_lifecycle import fixture, commit
from upgrade_abi import seed_abi_fixture
from lib.upgrade_ci_failure import classify
from lib.upgrade_lifecycle import verify_or_repair
from lib.upgrade_repair import repair
from lib.upgrade_verification import verify_offline

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('upgrade_result', ROOT / 'scripts/upgrade-result.py')
result = importlib.util.module_from_spec(spec)
spec.loader.exec_module(result)


class CompleteGates(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'JY Unix subprocess and Just fixture')
    def test_ffi_then_native_compilation_failure_return_to_same_session_before_freezing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = fixture(root)
            seed_abi_fixture(checkout)
            calls = root / 'calls.json'
            gate = checkout / 'gate.py'
            gate.write_text(
                'import os,sys\nfrom pathlib import Path\n'
                'assert os.environ["LLAMA_STAGE_BACKEND"]=="cpu"\n'
                'assert os.environ["LLAMA_STAGE_LINK_MODE"]=="static"\n'
                'assert Path(os.environ["LLAMA_WORKDIR"]).resolve()==Path.cwd()/".deps/llama.cpp"\n'
                'assert Path(os.environ["LLAMA_STAGE_BUILD_DIR"]).resolve()==Path.cwd()/"target/llama-regeneration/native"\n'
                'if sys.argv[1]=="test" and not Path("crates/skippy-ffi/src/static_bindings.rs").exists():\n'
                '    sys.exit("undefined symbol: llama_set_embeddings_nextn")\n'
                'if sys.argv[1]=="llama-check" and not Path("llama_cpp/patches/graph.patch").exists():\n'
                '    sys.exit("error: use of undeclared identifier LLM_FUSED_OP_LIGHTNING_INDEXER")\n')
            (checkout / 'Justfile').write_text(''.join(
                f'{recipe}:\n    @{shlex.quote(sys.executable)} gate.py {recipe}\n'
                for recipe in ('lint', 'test', 'llama-check')))
            commit(checkout, 'offline check fixture')
            diagnostics = root / 'diagnostics'
            agent = root / 'goose'
            agent.write_text(
                f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n'
                f'calls=Path({str(calls)!r})\n'
                'turns=json.loads(calls.read_text()) if calls.exists() else []\n'
                'assert len(turns)<2, "unexpected extra repair turn"\n'
                'turns.append(sys.argv[1:])\ncalls.write_text(json.dumps(turns))\n'
                f'feedback=json.loads(Path({str(diagnostics / "last-check.json")!r}).read_text())\n'
                'logs="".join(path.read_text() for path in Path(feedback["logs"]).glob("step-*.log"))\n'
                'if len(turns)==1:\n'
                '    assert "undefined symbol: llama_set_embeddings_nextn" in logs\n'
                '    path=Path("crates/skippy-ffi/src/static_bindings.rs")\n'
                '    path.parent.mkdir(parents=True, exist_ok=True)\n    path.write_text("FFI repair\\n")\n'
                'else:\n'
                '    assert "LLM_FUSED_OP_LIGHTNING_INDEXER" in logs\n'
                '    Path("llama_cpp/patches/graph.patch").write_text("native test repair\\n")\n')
            agent.chmod(0o755)
            with patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND=json.dumps([str(agent), 'run', '--text'])):
                verify_or_repair(checkout, diagnostics, lambda: verify_offline(checkout), repair, lambda *_: None)
            turns = json.loads(calls.read_text())
            self.assertEqual(len(turns), 2)
            self.assertNotIn('--resume', turns[0])
            self.assertIn('--resume', turns[1])
            self.assertEqual(turns[0][turns[0].index('--name') + 1], turns[1][turns[1].index('--name') + 1])
            for turn, expected in ((0, 2), (1, 3), (2, 3)):
                self.assertEqual(len(list((diagnostics / f'verification-{turn}').glob('step-*.log'))), expected)


class CIFailure(unittest.TestCase):
    def job(self, identifier=1, step='Run just llama-check', name='requalify / Graph Contracts'):
        return {'id': identifier, 'name': name, 'steps': [{'name': step, 'conclusion': 'failure'}]}

    def test_native_compile_failure_is_repairable(self):
        self.assertTrue(classify([self.job()], {1: 'error: undeclared LLM_FUSED_OP_LIGHTNING_INDEXER'})['repairable'])

    def test_missing_nvml_stops_even_when_another_source_failure_is_repairable(self):
        jobs = [self.job(), self.job(2, 'Run ./.github/actions/ci-package', 'requalify / ARM64 CUDA 13')]
        for error in ('fatal error: nvml.h: No such file or directory',
                      "fatal error C1083: Cannot open include file: 'nvml.h': No such file or directory"):
            with self.subTest(error=error):
                verdict = classify(jobs, {1: 'undeclared identifier', 2: error})
                self.assertFalse(verdict['repairable'])
                self.assertEqual(verdict['failure_class'], 'infrastructure')

    def test_setup_failure_is_not_sent_to_source_repair_and_summary_gate_is_ignored(self):
        self.assertFalse(classify([self.job(step='Initialize containers')], {})['repairable'])
        self.assertTrue(classify([self.job(), self.job(2, 'Run actions/github-script', 'requalify / Full CI gate')], {})['repairable'])
        self.assertFalse(classify([], {})['repairable'])


class CISelection(unittest.TestCase):
    def states(self):
        return {name: {'result': 'skipped', 'outputs': {}} for producer, ci, _ in result.ROUNDS for name in (producer, ci)}

    def test_each_success_selects_only_its_bound_candidate_artifact(self):
        for index, (producer, ci, artifact) in enumerate(result.ROUNDS):
            with self.subTest(ci=ci):
                states = self.states()
                for previous_producer, previous_ci, _ in result.ROUNDS[:index]:
                    states[previous_producer] = {'result': 'success', 'outputs': {'candidate_sha': 'a' * 40}}
                    states[previous_ci]['result'] = 'failure'
                sha = str(index + 1) * 40
                states[producer] = {'result': 'success', 'outputs': {'candidate_sha': sha}}
                states[ci]['result'] = 'success'
                self.assertEqual(result.select(states), (sha, artifact))

    def test_exhausted_budget_cancellation_missing_source_and_ambiguous_success_cannot_publish(self):
        states = self.states()
        for producer, ci, _ in result.ROUNDS:
            states[producer] = {'result': 'success', 'outputs': {'candidate_sha': 'a' * 40}}
            states[ci]['result'] = 'failure'
        with self.assertRaisesRegex(ValueError, 'three full-CI rounds'):
            result.select(states)
        states['requalify_2']['result'] = 'cancelled'
        with self.assertRaises(ValueError):
            result.select(states)
        states['requalify_2']['result'] = 'success'
        states['repair_2']['result'] = 'failure'
        with self.assertRaisesRegex(ValueError, 'producer'):
            result.select(states)
        states['repair_2']['result'] = 'success'
        states['qualify']['result'] = 'success'
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            result.select(states)


if __name__ == '__main__':
    unittest.main()
