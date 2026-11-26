#!/usr/bin/env python3
"""Exercise exact source recovery, executable/link modes and corruption rejection."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile


def main():
    recover=runpy.run_path('scripts/profile-scheduler-recover-native-source.py')['recover']
    with tempfile.TemporaryDirectory(prefix='source-recovery-') as temp:
        root=Path(temp);source=root/'source';source.mkdir()
        env={**os.environ,'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@invalid','GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@invalid'}
        def git(*args):return subprocess.check_output(['git','-C',str(source),*args],env=env,stderr=subprocess.DEVNULL)
        git('init','--quiet');(source/'plain').write_text('fixture\n');(source/'exec').write_text('#!/bin/sh\nexit 0\n');(source/'exec').chmod(0o755);(source/'link').symlink_to('plain')
        git('add','.');git('commit','--quiet','-m','fixture');record={'revision':git('rev-parse','HEAD').decode().strip(),'commit':git('cat-file','commit','HEAD').decode()}
        # Remove this fixture's object database; index and files remain available.
        (source/'.git/objects').rename(source/'.git/objects-unavailable');(source/'.git/objects').mkdir()
        out=recover(source,root/'restored',record);assert out['files']==3
        assert (root/'restored/link').is_symlink() and (root/'restored/exec').stat().st_mode&0o111
        (source/'plain').write_text('corrupt\n')
        try:recover(source,root/'rejected',record)
        except ValueError as e:assert 'hash mismatch' in str(e)
        else:raise AssertionError('corrupt source accepted')
        assert not (root/'rejected').exists()
        print('PASS: exact Git tree/commit, executable/symlink modes recovered without source objects; corrupt bytes rejected before destination creation.')


if __name__=='__main__':main()
