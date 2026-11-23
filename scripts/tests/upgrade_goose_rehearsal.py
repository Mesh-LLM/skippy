"""Explicit live JY rehearsal of session resumption and verification feedback."""

import os
from pathlib import Path
import tempfile

from upgrade_lifecycle import fixture, commit
from lib.ci_artifacts import write_json
from lib.upgrade_lifecycle import verify_or_repair
from lib.upgrade_repair import repair


def main():
    output = Path('target/upgrade-goose-rehearsal').resolve()
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
        write_json(output / 'result.json', {'passed': True, 'agent_turns': len(calls),
                   'same_session': True, 'verification_feedback': checks,
                   'recovery_snapshots': len(list((diagnostics / 'recovery').glob('*/manifest.json')))})
        print('JY_GOOSE_LIFECYCLE_REHEARSAL_PASSED', flush=True)


if __name__ == '__main__':
    main()
