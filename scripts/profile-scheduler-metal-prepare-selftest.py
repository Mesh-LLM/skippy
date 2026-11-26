#!/usr/bin/env python3
"""Verify offline recovered objects can replay the authoritative native queues."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile


def main():
    recover=runpy.run_path('scripts/profile-scheduler-recover-native-source.py')['recover']
    record=json.loads(Path('target/scheduler-fixes/q4-llama-source-record.json').read_text())
    candidate=Path('target/scheduler-fixes/q4-scale-source').resolve()
    objects=subprocess.check_output(['git','-C',str(candidate/'.deps/llama.cpp'),'rev-parse','--git-path','objects'],text=True).strip()
    objects=(candidate/'.deps/llama.cpp'/objects).resolve()
    with tempfile.TemporaryDirectory(prefix='q4-prepare-') as temp:
        root=Path(temp);indexed=root/'indexed';indexed.mkdir()
        def git(*args):return subprocess.run(['git','-C',str(indexed),*args],check=True)
        git('init','--quiet');(indexed/'.git/objects/info/alternates').write_text(str(objects)+'\n')
        git('read-tree',record['revision']);git('checkout-index','--all','--force')
        recovered=root/'recovered';recover(indexed,recovered,record)
        subprocess.run(['git','-C',str(recovered),'remote','add','origin',str(indexed)],check=True)
        env={**os.environ,'LLAMA_WORKDIR':str(recovered),'LLAMA_UPSTREAM_URL':str(indexed),
             'GIT_ALLOW_PROTOCOL':'file','GIT_NO_LAZY_FETCH':'1','LLAMA_GIT_MAX_ATTEMPTS':'1'}
        for checkout,expected in [(Path.cwd(),'6704435209b0fa8eabc3aa91e3c0a22f92b89863'),(candidate,'e09c584b22b280a93903058129a76ac7b90df8d4')]:
            subprocess.run(['just','llama-prepare'],cwd=checkout,env=env,check=True,timeout=300)
            actual=subprocess.check_output(['git','-C',str(recovered),'rev-parse','HEAD'],text=True).strip()
            assert actual==expected,(actual,expected)
        print('PASS: recovered exact upstream objects, original and candidate Just preparation identities; no network fetch.')


if __name__=='__main__':main()
