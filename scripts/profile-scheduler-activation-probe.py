#!/usr/bin/env python3
"""Bounded Q35 direct decode batching controls; raw subject logits stay private."""
import argparse
import array
import math
import sys
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import tarfile
import time
from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite

MODES = ('solo-plain', 'batch-plain', 'solo-trace', 'batch-trace')
TOKEN_SHA = '83de68a4172aa4dab09da1e24667392b20f8b52fcaf916593d77a40b5713281e'
SELECTION = {'pin': 'qwen35-4b', 'fixture': 'qwen3.5-4b-q4-k-m', 'prompt': 'load-0190',
             'group': 'load', 'justfile': 'scripts/profile-scheduler-activation-probe.just'}


def run_cell(binary, model, prompt, root, repeat, mode, digest):
    name = f'r{repeat}-{mode}'; directory = root/name
    env = host.clean_env(root)
    suite.nominal(env, soak=60)
    launch = [str(binary), str(model), str(prompt), mode, str(directory)]
    log_path = root/(name+'.log')
    with log_path.open('w') as log:
        child = subprocess.Popen(launch, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
        try:
            deadline = time.monotonic()+300
            while not (directory/'ready.json').exists():
                if child.poll() is not None or time.monotonic()>deadline:
                    raise ValueError('native readiness failed: '+name)
                time.sleep(0.5)
            ready = json.loads((directory/'ready.json').read_text())
            if hashlib.sha256(json.dumps(ready['prompt_ids']).encode()).hexdigest()!=TOKEN_SHA:
                raise ValueError('native prompt identity drift')
            native = suite.resolved(log_path, 'skippy-base', 16)
            if native['gpu_layers']!=33 or native['n_seq_max']!=['32'] or native['n_rs_seq']!=['0']:
                raise ValueError('native state/offload drift')
            suite.nominal(env, child.pid, 30)
            host.telemetry(directory/'before.json', env)
            host.save(directory/'launch.json', {'argv':launch, 'environment_names':sorted(env)})
            start = time.time_ns();child.stdin.write('GO\n');child.stdin.flush()
            deadline = time.monotonic()+300
            while child.poll() is None:
                if host.thermal_pressure() or host.competing_work(env,child.pid) or time.monotonic()>deadline:
                    raise host.HostUnavailable('batch probe thermal/contention/deadline gate')
                time.sleep(0.5)
            end = time.time_ns()
            if child.returncode!=0:raise ValueError('native probe failed: '+name)
            host.telemetry(directory/'after.json',env)
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            child.stdin.close()
    record=json.loads((directory/'probe.json').read_text())
    lanes=1 if mode.startswith('solo') else 16
    if not record.get('complete') or len(record['subject_steps'])!=32 or len(record['lanes'])!=lanes:
        raise ValueError('incomplete native generation')
    if any(len(row['generated_ids'])!=32 for row in record['lanes']):raise ValueError('lane count mismatch')
    raw={step['raw_file']:digest(directory/step['raw_file']) for step in record['subject_steps']}
    host.save(directory/'raw-hashes.json',raw)
    return {'directory':name,'mode':mode,'repeat':repeat,'resolved':native,'start_unix_ns':start,
            'end_unix_ns':end,'report_sha256':digest(directory/'probe.json'),'raw_sha256':raw}



def compare_activations(root, report, digest):
    if sys.byteorder != 'little': raise ValueError('unregistered byte order')
    records = {}
    for cell in report['cells']:
        directory=root/cell['directory']; record=json.loads((directory/'probe.json').read_text())
        rows=record['activations']; cell['activation_sha256']={r['raw_file']:digest(directory/r['raw_file']) for r in rows}
        host.save(directory/'activation-hashes.json',cell['activation_sha256'])
        records[cell['repeat'],cell['mode']] = (directory,record)
    result=[]
    for repeat in range(2):
        left, a=records[repeat,'solo-trace'];right,b=records[repeat,'batch-trace']
        def keyed(rows):
            seen={};out={}
            for row in rows:
                base=(row['step'],row['name'],row['op'],row['elements']);n=seen.get(base,0);seen[base]=n+1
                out[base+(n,)]=row
            return out
        aa=keyed(a['activations']);bb=keyed(b['activations']);comparisons=[]
        for key,x in aa.items():
            if key not in bb:continue
            y=bb[key];av=array.array('f');bv=array.array('f')
            av.frombytes((left/x['raw_file']).read_bytes());bv.frombytes((right/y['raw_file']).read_bytes())
            if len(av)!=x['elements'] or len(bv)!=len(av):raise ValueError('activation byte count')
            if not all(math.isfinite(v) for v in av) or not all(math.isfinite(v) for v in bv):raise ValueError('nonfinite activation')
            delta=[float(u)-float(v) for u,v in zip(av,bv)]
            comparisons.append({'key':key,'solo_file':x['raw_file'],'batch_file':y['raw_file'],
                'exact':(left/x['raw_file']).read_bytes()==(right/y['raw_file']).read_bytes(),
                'max_abs':max(map(abs,delta)), 'rms':math.sqrt(sum(v*v for v in delta)/len(delta))})
        result.append({'repeat':repeat,'rows':comparisons,'solo_unmatched':[list(k) for k in aa.keys()-bb.keys()],
                       'batch_unmatched':[list(k) for k in bb.keys()-aa.keys()]})
    host.save(root/'activation-comparison.json',result)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    root=args.cache/f'scheduler-qwen35-activation-probe-{time.time_ns()}';root.mkdir()
    base=runpy.run_path('scripts/profile-scheduler-native-probe.py')
    report={'complete':False,'private_root':str(root),'cells':[],'selection':SELECTION,
            'scope':'Public llama caller with optional activation observation; require original Skippy and callback-off calibration. Not timing or fault proof.'}
    try:
        binary,model,prompt=base['prepare'](args.cache,root,report,SELECTION)
        report['probe_source_sha256']=base['digest'](Path('scripts/profile-scheduler-activation-probe.cpp'))
        report['probe_just_sha256']=base['digest'](Path('scripts/profile-scheduler-activation-probe.just'))
        for repeat in range(2):
            for mode in MODES if repeat==0 else tuple(reversed(MODES)):
                print('Q35_ACTIVATION_PROBE',repeat,mode,flush=True)
                report['cells'].append(run_cell(binary,model,prompt,root,repeat,mode,base['digest']))
                host.save(args.output/'activation-probe.json',report)
        report['activation_comparison']=compare_activations(root,report,base['digest'])
        report['complete']=True
    except Exception as error:
        report['error']={'type':type(error).__name__,'message':str(error)};raise
    finally:
        host.save(args.output/'activation-probe.json',report)
        with tarfile.open(args.output/'activation-probe-evidence.tar.gz','w:gz') as archive:
            for path in sorted(root.rglob('*')):
                if path.is_file() and not path.is_symlink() and path.suffix in ('.json','.log'):
                    archive.add(path,arcname=str(path.relative_to(root)))


if __name__=='__main__':main()
