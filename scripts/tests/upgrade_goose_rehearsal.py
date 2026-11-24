"""Explicit live JY rehearsal of session resumption and verification feedback."""

import json
from pathlib import Path
import shutil
import tempfile

from upgrade_lifecycle import fixture, commit
from lib.ci_artifacts import write_json
from lib.upgrade_lifecycle import verify_or_repair
from lib.upgrade_repair import repair


def recovery_manifests(diagnostics, turns):
    recovery = diagnostics / 'recovery'
    for label in [*(f'turn-{turn}' for turn in range(1, turns + 1)), 'final']:
        matches = list(recovery.glob(f'{label}-*/manifest.json'))
        if len(matches) != 1:
            raise ValueError(f'rehearsal requires one completed {label} checkpoint from this run')
        if json.loads(matches[0].read_text()).get('unverified') is not True:
            raise ValueError(f'invalid rehearsal checkpoint: {matches[0]}')
    manifests = list(recovery.glob('*/manifest.json'))
    if len(manifests) != turns + 1:
        raise ValueError('rehearsal contains unexpected recovery checkpoints')
    return manifests


def main():
    output = Path('target/upgrade-goose-rehearsal').resolve()
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        checkout = fixture(Path(tmp))
        (checkout / 'AGENTS.md').write_text('This is an isolated controller rehearsal. Follow diagnostics/last-check.json from the prompt. Only modify the requested text fixture. No native compilation is required.\n')
        commit(checkout, 'rehearsal instructions')
        diagnostics = output / 'diagnostics'
        calls = []
        checks = []
        def verify():
            path = checkout / 'llama_cpp/patches/rehearsal.patch'
            text = path.read_text().strip() if path.exists() else ''
            checks.append(text)
            if not text:
                raise ValueError('Lifecycle rehearsal: create llama_cpp/patches/rehearsal.patch containing exactly phase-one. '
                                 'This is a text fixture, not a native patch; no other changes are required.')
            if text == 'phase-one':
                raise ValueError('First turn verified. Continue this same session: replace the fixture content with exactly '
                                 'phase-two. No other changes are required. This is the second verification feedback turn.')
            if text != 'phase-two':
                raise ValueError('Rehearsal requires the exact text phase-two in the fixture.')
        def agent(*args, **kwargs):
            calls.append(dict(kwargs['session']))
            repair(*args, **kwargs)
        verify_or_repair(checkout, diagnostics, verify, agent, lambda *_: None)
        assert len(calls) >= 2 and calls[0]['started'] is False and calls[1]['started'] is True
        assert len({item['name'] for item in calls}) == 1
        assert 'phase-one' in checks and checks[-1] == 'phase-two'
        manifests = recovery_manifests(diagnostics, len(calls))
        write_json(output / 'result.json', {'passed': True, 'agent_turns': len(calls),
                   'same_session': True, 'verification_feedback': checks,
                   'recovery_snapshots': len(manifests)})
        print('JY_GOOSE_LIFECYCLE_REHEARSAL_PASSED', flush=True)


if __name__ == '__main__':
    main()
