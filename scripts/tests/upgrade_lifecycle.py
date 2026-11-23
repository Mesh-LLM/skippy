"""Offline lifecycle rehearsals using real Git, Just and subprocess boundaries."""

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from lib.ci_process import command, time_budget
from lib.upgrade_lifecycle import verify_or_repair
from lib.upgrade_recovery import checkpoint, retain
from lib.upgrade_regeneration import stage_patches, validate_patch_metadata
from lib.upgrade_repair import repair
from lib.upgrade_source import git

ROOT = Path(__file__).resolve().parents[2]


def commit(root, message):
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@skippy.local',
        '-c', 'commit.gpgsign=false', 'commit', '-m', message)
    return git(root, 'rev-parse', 'HEAD')


def fixture(root):
    checkout = root / 'checkout'
    checkout.mkdir()
    git(checkout, 'init', '--initial-branch=main')
    (checkout / '.gitignore').write_text('.deps/\ntarget/\n')
    (checkout / 'llama_cpp/patches').mkdir(parents=True)
    (checkout / 'llama_cpp/upstream.txt').write_text('a' * 40 + '\n')
    commit(checkout, 'base')
    return checkout


class Lifecycle(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Unix executable fixture exercises the native JY path')
    def test_same_session_fixes_failed_normal_preparation_and_preserves_each_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = fixture(root)
            upstream = root / 'upstream'
            upstream.mkdir()
            git(upstream, 'init', '--initial-branch=master')
            (upstream / 'native.cpp').write_text('before\n')
            base = commit(upstream, 'upstream')
            (upstream / 'native.cpp').write_text('after\n')
            commit(upstream, 'code fix')
            good = git(upstream, 'format-patch', '-1', '--stdout') + '\n'
            (upstream / '.skippy-upstream-sha').write_text(base + '\n')
            commit(upstream, 'accidental stamp')
            bad = git(upstream, 'format-patch', '-1', '--stdout') + '\n'
            pin = checkout / 'llama_cpp/upstream.txt'
            pin.write_text(base + '\n')
            mail = checkout / 'llama_cpp/patches/0001-fixture.patch'
            mail.write_text(bad)
            justfile = checkout / 'Justfile'
            justfile.write_text('llama-prepare:\n    @bash ' + shlex.quote(str(ROOT / 'scripts/prepare-llama.sh')) + '\n')
            commit(checkout, 'fixture inputs')
            agent = root / 'goose'
            state = root / 'session.json'
            agent.write_text(
                f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n'
                f'state=Path({str(state)!r})\n'
                'turns=json.loads(state.read_text()) if state.exists() else []\n'
                'assert len(turns) < 3, "unexpected extra verification failure"\nturns.append(sys.argv[1:])\nstate.write_text(json.dumps(turns))\n'
                f'assert Path({str(root / "diagnostics/last-check.json")!r}).is_file()\n'
                f'Path("llama_cpp/patches/0001-fixture.patch").write_text({(bad + chr(10))!r} if len(turns)==1 else {good!r})\n')
            agent.chmod(0o755)
            env = dict(LLAMA_WORKDIR=str(checkout / '.deps/llama.cpp'), LLAMA_PIN_FILE=str(pin),
                       LLAMA_PATCH_DIR=str(checkout / 'target/staged'), LLAMA_UPSTREAM_URL=str(upstream),
                       SKIPPY_UPGRADE_REPAIR_COMMAND=json.dumps([str(agent), 'run', '--no-session', '--text']))
            def verify():
                stage_patches(checkout / 'llama_cpp/patches', checkout / 'target/staged')
                command(['just', '--justfile', str(justfile), 'llama-prepare'])
            with patch.dict(os.environ, env):
                verify_or_repair(checkout, root / 'diagnostics', verify, repair, lambda *_: None)
            turns = json.loads(state.read_text())
            self.assertEqual(len(turns), 2)
            self.assertNotIn('--resume', turns[0])
            self.assertIn('--resume', turns[1])
            self.assertEqual(turns[0][turns[0].index('--name') + 1], turns[1][turns[1].index('--name') + 1])
            self.assertNotIn('--no-session', turns[0])
            self.assertEqual((checkout / '.deps/llama.cpp/native.cpp').read_text(), 'after\n')
            git(checkout / '.deps/llama.cpp', 'add', '-A')
            self.assertEqual(git(checkout / '.deps/llama.cpp', 'diff', '--cached', '--name-only'), '')
            self.assertEqual(len(list((root / 'diagnostics/recovery').glob('turn-*'))), 2)
            self.assertTrue(list((root / 'diagnostics/verification-2').glob('step-*.log')))

    def test_deleted_patch_disappears_and_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, dst = root / 'src', root / 'dst'
            src.mkdir()
            (src / 'series').write_text('')
            dst.mkdir()
            (dst / 'removed.patch').write_text('stale')
            stage_patches(src, dst)
            self.assertFalse((dst / 'removed.patch').exists())
            (src / 'bad.patch').write_text('diff --git a/.skippy-upstream-sha b/.skippy-upstream-sha\n'
                'new file mode 100644\n--- /dev/null\n+++ b/.skippy-upstream-sha\n@@ -0,0 +1 @@\n+stamp\n')
            with self.assertRaisesRegex(ValueError, 'preparation metadata'):
                validate_patch_metadata(src)

    def test_cancellation_saves_outer_changes_and_committed_native_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = fixture(root)
            native = checkout / '.deps/llama.cpp'
            native.mkdir(parents=True)
            git(native, 'init', '--initial-branch=main')
            (native / 'native.cpp').write_text('before\n')
            base = commit(native, 'base')
            (checkout / 'llama_cpp/upstream.txt').write_text(base + '\n')
            commit(checkout, 'pin')
            (native / 'native.cpp').write_text('after\n')
            commit(native, 'native repair not yet exported')
            (native / 'new.cpp').write_text('untracked\n')
            (checkout / 'llama_cpp/patches/new.patch').write_text('repaired queue\n')
            def cancelled():
                raise KeyboardInterrupt('cancelled during verification')
            with self.assertRaises(KeyboardInterrupt):
                verify_or_repair(checkout, root / 'diagnostics', cancelled, repair, lambda *_: None)
            saved = next((root / 'diagnostics/recovery').glob('final-*'))
            self.assertIn('+after', (saved / 'native/tracked.patch').read_text())
            with tarfile.open(saved / 'outer/untracked.tar.gz') as archive:
                self.assertEqual(archive.extractfile('llama_cpp/patches/new.patch').read(), b'repaired queue\n')
            restored = root / 'restored'
            git(root, 'clone', str(native), str(restored))
            git(restored, 'reset', '--hard', base)
            git(restored, 'apply', str(saved / 'native/tracked.patch'))
            self.assertEqual((restored / 'native.cpp').read_text(), 'after\n')
            self.assertTrue(json.loads((saved / 'manifest.json').read_text())['unverified'])

    @unittest.skipIf(os.name == 'nt', 'Exercises the native runner SIGTERM path')
    def test_sigterm_during_child_command_retains_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = fixture(root)
            (checkout / 'llama_cpp/patches/repair.patch').write_text('retained repair')
            ready = root / 'ready'
            code = (
                "from pathlib import Path\nfrom lib.ci_process import cancel_on_signal, command\n"
                "from lib.upgrade_lifecycle import verify_or_repair\n"
                f"def verify():\n Path({str(ready)!r}).touch()\n"
                " command(['python3', '-c', 'import time; time.sleep(60)'])\n"
                f"with cancel_on_signal():\n verify_or_repair(Path({str(checkout)!r}), "
                f"Path({str(root / 'diagnostics')!r}), verify, None, lambda *_: None)\n"
            )
            with (root / 'child.log').open('w') as log:
                child = subprocess.Popen([sys.executable, '-c', code], stdout=log, stderr=log,
                                         env=dict(os.environ, PYTHONPATH=str(ROOT / 'scripts')))
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists())
                    child.send_signal(signal.SIGTERM)
                    self.assertNotEqual(child.wait(timeout=10), 0)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
            saved = next((root / 'diagnostics/recovery').glob('final-*'))
            with tarfile.open(saved / 'outer/untracked.tar.gz') as archive:
                self.assertEqual(archive.extractfile('llama_cpp/patches/repair.patch').read(), b'retained repair')

    def test_missing_native_base_captures_full_tracked_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = fixture(root)
            native = checkout / '.deps/llama.cpp'
            native.mkdir(parents=True)
            git(native, 'init', '--initial-branch=main')
            (native / 'native.cpp').write_text('complete source\n')
            commit(native, 'native work with unavailable upstream')
            saved = checkpoint(checkout, root / 'recovery', 'missing-base')
            manifest = json.loads((saved / 'manifest.json').read_text())
            self.assertTrue(manifest['native']['full_tracked_tree'])
            restored = root / 'restored'
            restored.mkdir()
            git(restored, 'init', '--initial-branch=main')
            git(restored, 'apply', str(saved / 'native/tracked.patch'))
            self.assertEqual((restored / 'native.cpp').read_text(), 'complete source\n')

    def test_time_windows_bound_commands_and_recovery_survives_expiry(self):
        with time_budget(0):
            with self.assertRaises(subprocess.TimeoutExpired):
                command([sys.executable, '-c', 'raise SystemExit(99)'])
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                # Fixture Git creation is intentionally outside an expired budget.
                from lib.ci_process import DEADLINE
                token = DEADLINE.set(None)
                try:
                    checkout = fixture(root)
                finally:
                    DEADLINE.reset(token)
                self.assertIsNotNone(retain(checkout, root / 'recovery', 'expired'))

    def test_repeated_failures_share_one_coding_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = fixture(root)
            calls = []
            def broken():
                raise ValueError('existing contract still fails')
            def agent(*args, **kwargs):
                calls.append(kwargs['timeout'])
                self.assertLess(len(calls), 8, 'coding budget was reset between turns')
                time.sleep(0.03)
            with patch.dict(os.environ, SKIPPY_UPGRADE_REPAIR_COMMAND='["agent"]'), \
                 patch('lib.upgrade_lifecycle.CODING_SECONDS', 0.08):
                with self.assertRaisesRegex(TimeoutError, 'coding window exhausted'):
                    verify_or_repair(checkout, root / 'diagnostics', broken, agent, lambda *_: None)
            self.assertTrue(calls)
            self.assertEqual(calls, sorted(calls, reverse=True))


if __name__ == '__main__':
    unittest.main()
