#!/usr/bin/env python3
"""Verify a direct batch probe, requiring server calibration for applicability."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from lib import profile_scheduler_suite as suite

MODES = ('solo', 'resident-serial', 'resident-batched', 'staggered')
TOKEN_SHA = '83de68a4172aa4dab09da1e24667392b20f8b52fcaf916593d77a40b5713281e'


def read(path):
    return json.loads(path.read_text())


def require(value, message):
    if not value: raise ValueError(message)


def verify_calls(record):
    mode = record['mode']; size = 1 if mode == 'solo' else 16
    positions = [0] * size; generated = [0] * size; expected = []
    def call(phase, lanes):
        count = 512 if phase == 'prefill' else 1
        expected.append({'phase':phase, 'lanes':lanes, 'input_positions':[positions[i] for i in lanes],
                         'tokens_per_lane':count})
        for i in lanes: positions[i] += count; generated[i] += 1
    def decode():
        active = [i for i in range(size) if 0 < generated[i] < 32]
        if mode in ('solo','resident-serial'):
            for i in active: call('decode',[i])
        elif active: call('decode',active)
        return bool(active)
    for i in range(size):
        call('prefill',[i])
        if mode=='staggered' and i>=1: decode()
    while decode(): pass
    require(record['calls']==expected, 'native call/position plan drift')
    return size


def audit(root, trace):
    report=read(root/'batch-probe.json');original=read(trace/'batch-history.json')
    require(report['complete'] and 'error' not in report,'incomplete probe')
    require(report['native_source']=='6704435209b0fa8eabc3aa91e3c0a22f92b89863','native source drift')
    require(report['native_manifest_sha256']=='c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481','native bundle drift')
    require(report['native_manifest']['build']['llama_patched_sha']==report['native_source'],'native manifest source drift')
    headers=Path('target/scheduler-fixes/native-headers/manifest.json')
    require(report['header_manifest_sha256']==hashlib.sha256(headers.read_bytes()).hexdigest(),'header manifest drift')
    require(read(root/'evidence/header-provenance.json')==read(headers),'header provenance drift')
    require(report['abi']=={'abi':[0,1,87],'runtime_config_bytes':240},'ABI drift')
    require(report['model']==read(Path('ci/inference-artifacts.json'))['qwen35-4b'],'CI model drift')
    for field,path in (('probe_source_sha256','scripts/profile-scheduler-batch-probe.cpp'),
                       ('probe_just_sha256','scripts/profile-scheduler-batch-probe.just')):
        require(report[field]==hashlib.sha256(Path(path).read_bytes()).hexdigest(),'probe source/build recipe drift')
    fixture=Path('target/scheduler-four-models/artifacts/qwen35-c128-retry/fixtures.json')
    require(report['fixture_sha256']==hashlib.sha256(fixture.read_bytes()).hexdigest()==original['fixture_sha256'],'fixture drift')
    prompt=next(r for r in read(fixture)['load'] if r['id']=='load-0190')['messages'][0]['content']
    require(report['prompt_sha256']==hashlib.sha256(prompt.encode()).hexdigest(),'prompt drift')
    require(report['selection']=={'pin':'qwen35-4b','fixture':'qwen3.5-4b-q4-k-m','prompt':'load-0190',
            'group':'load','justfile':'scripts/profile-scheduler-batch-probe.just'},'selection drift')
    expected=[(r,m) for r in range(2) for m in (MODES if r==0 else tuple(reversed(MODES)))]
    require([(c['repeat'],c['mode']) for c in report['cells']]==expected,'process order/count drift')
    reference=suite.resolved(trace/'evidence/c1-first-skippy-base/server.log','skippy-base',16)
    records={};cells={};total=0
    for cell in report['cells']:
        key=cell['repeat'],cell['mode'];name=f'r{key[0]}-{key[1]}'
        require(cell['directory']==name,'cell directory drift')
        directory=root/'evidence'/name;path=directory/'probe.json';record=read(path)
        require(hashlib.sha256(path.read_bytes()).hexdigest()==cell['report_sha256'],'report hash drift')
        require(record['complete'] and record['mode']==key[1] and record['abi']==report['abi']['abi'],'generation identity drift')
        require(hashlib.sha256(json.dumps(record['prompt_ids']).encode()).hexdigest()==TOKEN_SHA,'tokenizer drift')
        size=verify_calls(record)
        require([r['lane'] for r in record['lanes']]==list(range(size)),'lane identity/count drift')
        require(all(len(r['generated_ids'])==32 for r in record['lanes']),'generated count drift')
        total+=sum(len(r['generated_ids']) for r in record['lanes'])
        native=suite.resolved(root/'evidence'/(name+'.log'),'skippy-base',16)
        require(native==cell['resolved'] and native['gpu_layers']==33,'native offload/report drift')
        require({k:v for k,v in native.items() if k!='kv_buffers'}==
                {k:v for k,v in reference.items() if k!='kv_buffers'},'native settings drift')
        pattern=r'size =\s*([\d.]+) MiB.*?K \(f16\):\s*([\d.]+) MiB, V \(f16\):\s*([\d.]+) MiB'
        buffers=[set(re.findall(pattern,'\n'.join(v['kv_buffers']))) for v in (native,reference)]
        require(buffers[0] and buffers[0]==buffers[1],'KV type/allocation drift')
        pin=report['model'];model_path=Path('/Users/Shared/jianyang/model-store/huggingface/hub')/(
            'models--'+pin['repo'].replace('/','--'))/'snapshots'/pin['revision']/pin['files'][0]['path']
        private=Path(report['private_root'])
        require(read(directory/'launch.json')['argv']==[str(private/'native-probe'),str(model_path),
                str(private/'prompt.txt'),key[1],str(private/name)],'launch drift')
        before,after=[read(directory/(b+'.json')) for b in ('before','after')]
        require(before['native_thermal_pressure_level']==after['native_thermal_pressure_level']==0,'thermal boundary')
        require(before['wall_ns']<=cell['start_unix_ns']<cell['end_unix_ns']<=after['wall_ns'],'generation window')
        raw=read(directory/'raw-hashes.json');require(raw==cell['raw_sha256'] and len(raw)==32,'raw hash manifest drift')
        steps=record['subject_steps'];require(len(steps)==32,'subject count')
        require(record['lanes'][0]['generated_ids']==[s['token'] for s in steps],'subject token IDs differ')
        for i,step in enumerate(steps):
            require(step['step']==i and step['position']==512+i and step['batch_row']==(511 if i==0 else 0),'subject row/position drift')
            require(step['raw_file']==f'subject-logits-{i}.f32','raw path drift')
            require(len(raw[step['raw_file']])==64 and all(c in '0123456789abcdef' for c in raw[step['raw_file']]),'raw digest invalid')
            require(len(step['top'])==20 and all(math.isfinite(v['logit']) for v in step['top']),'nonfinite logits')
        records[key]=record;cells[key]=cell
    require(total==3136,'generated token total')
    serial=read(trace/'evidence/c1-first-skippy-base/0-c1.json')['requests']['subject-00']['text']
    concurrent={r['text'] for r in read(trace/'evidence/c16-first-skippy-base/0-c16.json')['requests'].values()}
    require(len(concurrent)==1,'ambiguous server concurrent reference')
    calibration={'solo_matches_server_serial':all(records[r,'solo']['lanes'][0]['text']==serial for r in range(2)),
                 'staggered_matches_server_concurrent':all(lane['text'] in concurrent for r in range(2) for lane in records[r,'staggered']['lanes'])}
    comparisons=[]
    for mode in MODES[1:]:
        a,b=records[0,'solo'],records[0,mode];prefix=[]
        for x,y in zip(a['subject_steps'],b['subject_steps']):
            prefix.append({'step':x['step'],'token_exact':x['token']==y['token'],
                'post_call_logits_exact':cells[0,'solo']['raw_sha256'][x['raw_file']]==cells[0,mode]['raw_sha256'][y['raw_file']],
                'solo_top':x['top'][:5],'other_top':y['top'][:5]})
            if x['token']!=y['token']:break
        comparisons.append({'mode':mode,'subject_text_exact':a['lanes'][0]['text']==b['lanes'][0]['text'],
                            'unique_lane_texts':len({r['text'] for r in b['lanes']}),'shared_prefix':prefix})
    return {'processes_verified':8,'generated_tokens_verified':total,'server_calibration':calibration,
            'repeat_tokens_exact':{m:[r['generated_ids'] for r in records[0,m]['lanes']]==[r['generated_ids'] for r in records[1,m]['lanes']] for m in MODES},
            'repeat_subject_logits_exact':{m:cells[0,m]['raw_sha256']==cells[1,m]['raw_sha256'] for m in MODES},
            'comparisons':comparisons,'scope':'Direct API diagnostic, not a runtime fix. Failed calibration blocks server applicability; no faulty operator or timing claim. Raw subject rows stay private.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--trace',type=Path,required=True)
    a=p.parse_args();result=audit(a.artifacts,a.trace);(a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='comparisons'}))
