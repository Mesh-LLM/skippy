#!/usr/bin/env python3
"""Run both source pins sequentially inside one exclusive native JY lease."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
from lib.ci_process import cancel_on_signal, stop
from lib.inference_inputs import fixture
from lib.inference_prefill_diagnostic import require_cached
from lib.inference_probe import validate_evidence

PINS = {'main':'ed894d67104925b759dbf54fb203e40cc5d89eb8',
        'upgrade':'73b6f910260b681e082f5ebc929777e4ab3d04f8'}
TREES = {'main':'8ec4d1baddaa7f9639094379023a76dbaf47594b',
         'upgrade':'abde3e9936ef3eb8e9c3146b5ea11d2cc202b07d'}
ORDER = ['main','upgrade','upgrade','main','upgrade','main','main','upgrade']


def run(argv, cwd, env, log, timeout):
    with log.open('wb') as stream:
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        try:
            while child.poll() is None:
                if time.monotonic() - started > timeout:
                    raise TimeoutError('subprocess exceeded its bounded timeout')
                time.sleep(2)
            if child.returncode:
                raise RuntimeError(f'{argv[0]} exited {child.returncode}; inspect {log.name}')
        finally:
            stop(child)


def execute(key):
    root = Path.cwd()
    out = Path(os.environ['JIANYANG_ARTIFACT_DIR']).resolve()
    out.mkdir(parents=True, exist_ok=True)
    require_cached(fixture(key))
    sources = root / 'target/matched-prefill-sources'
    sources.mkdir(parents=True, exist_ok=True)
    checkouts = {}
    for tag, sha in PINS.items():
        found = subprocess.run(['git','cat-file','-e',sha+'^{commit}'], capture_output=True)
        if found.returncode:
            print('Fetching the admitted source ref', tag, sha, flush=True)
            subprocess.run(['git','fetch','--no-tags','origin',sha], check=True, timeout=120)
        path = sources / tag
        subprocess.run(['git','worktree','add','--detach',str(path),sha], check=True)
        checkouts[tag] = path
        env = dict(os.environ, JIANYANG_COMMIT=sha)
        print(json.dumps({'phase':'build','pin':tag,'source':sha}), flush=True)
        run(['just','inference-build'],path,env,out/('build-'+tag+'.log'),1800)
        identity = json.loads((path/'target/inference-build.json').read_text())
        assert identity['stack_commit']==sha and identity['native_tree']==TREES[tag]
        (out/('build-'+tag+'-provenance.json')).write_text(json.dumps(identity,indent=2))
    rows=[]
    schedule=[('warm-only',i,tag) for i,tag in enumerate(PINS)]+[('measured',i,tag) for i,tag in enumerate(ORDER)]
    for serial,(phase,index,tag) in enumerate(schedule):
        name=f'{serial:02d}-{phase}-{tag}'
        target=out/name;target.mkdir()
        env=dict(os.environ,JIANYANG_COMMIT=PINS[tag],JIANYANG_ARTIFACT_DIR=str(target))
        print(json.dumps({'phase':phase,'index':index,'pin':tag,'source':PINS[tag]}),flush=True)
        run(['just','inference-execute','pr',key,'standalone'],checkouts[tag],env,out/(name+'.log'),600)
        values={}
        for kind in ['qualification','provenance','prefill-diagnostics','throughput-probe']:
            source=target/(kind+'.json')
            values[kind]=json.loads(source.read_text())
            shutil.copyfile(source,out/(name+'-'+kind+'.json'))
        assert values['qualification']['status']=='passed'
        assert values['qualification']['source_sha']==values['provenance']['stack_commit']==PINS[tag]
        assert values['provenance']['native_tree']==TREES[tag]
        probe=values['throughput-probe'];coordinate=probe['samples'][0]['measured']['payload']['model']
        responses=validate_evidence(probe,coordinate)
        rates={kind:sum(r['timings'][count] for r in responses)*1000/sum(r['timings'][ms] for r in responses)
               for kind,count,ms in [('prefill','prompt_n','prompt_ms'),('decode','predicted_n','predicted_ms')]}
        row={'phase':phase,'index':index,'pin':tag,'source':PINS[tag],
             'rates':rates,'prefix':name,
             'prompt_tokens':[r['timings']['prompt_n'] for r in responses],
             'prefill_ms':[r['timings']['prompt_ms'] for r in responses],
             'decode_ms':[r['timings']['predicted_ms'] for r in responses],
             'client_ms':[s['measured']['client_ms'] for s in probe['samples']]}
        rows.append(row)
        (out/'matched-prefill.json').write_text(json.dumps({'status':'running','case':key,
                 'pins':PINS,'native_trees':TREES,'rows':rows},indent=2))
        print(json.dumps({'event':'result',**row}),flush=True)
    measured=[r for r in rows if r['phase']=='measured']
    assert len(measured)==8
    assert len({tuple(r['prompt_tokens']) for r in measured})==1
    (out/'matched-prefill.json').write_text(json.dumps({'status':'passed','case':key,
             'pins':PINS,'native_trees':TREES,'rows':rows},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('model',choices=['qwen35-4b','qwen36-35b-a3b','qwen3-coder-30b-a3b'])
    args=parser.parse_args()
    with cancel_on_signal():
        execute(args.model)
