#!/usr/bin/env python3
"""Verify a direct batch probe, requiring server calibration for applicability."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from lib import profile_scheduler_suite as suite

MODES = ('solo-plain', 'batch-plain', 'solo-trace', 'batch-trace')
TOKEN_SHA = '83de68a4172aa4dab09da1e24667392b20f8b52fcaf916593d77a40b5713281e'


def read(path):
    return json.loads(path.read_text())


def require(value, message):
    if not value: raise ValueError(message)


def verify_calls(record):
    size=1 if record['mode'].startswith('solo') else 16
    expected=[{'phase':'prefill','lane':i,'step':0,'batch_tokens':512} for i in range(size)]
    expected += [{'phase':'decode','lane':-1,'step':i,'batch_tokens':size} for i in range(1,32)]
    require(record['calls']==expected,'native call plan drift')
    require(record['sampling']=='greedy non-EOG; calibration required','sampling drift')
    return size


def audit(root, trace, prior):
    report=read(root/'activation-probe.json');original=read(trace/'batch-history.json')
    require(report['complete'] and 'error' not in report,'incomplete probe')
    require(report['native_source']=='6704435209b0fa8eabc3aa91e3c0a22f92b89863','native source drift')
    require(report['native_manifest_sha256']=='c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481','native bundle drift')
    require(report['native_manifest']['build']['llama_patched_sha']==report['native_source'],'native manifest source drift')
    headers=Path('target/scheduler-fixes/native-headers/manifest.json')
    require(report['header_manifest_sha256']==hashlib.sha256(headers.read_bytes()).hexdigest(),'header manifest drift')
    require(read(root/'evidence/header-provenance.json')==read(headers),'header provenance drift')
    require(report['abi']=={'abi':[0,1,87],'runtime_config_bytes':240},'ABI drift')
    require(report['model']==read(Path('ci/inference-artifacts.json'))['qwen35-4b'],'CI model drift')
    for field,path in (('probe_source_sha256','scripts/profile-scheduler-activation-probe.cpp'),
                       ('probe_just_sha256','scripts/profile-scheduler-activation-probe.just')):
        require(report[field]==hashlib.sha256(Path(path).read_bytes()).hexdigest(),'probe source/build recipe drift')
    fixture=Path('target/scheduler-four-models/artifacts/qwen35-c128-retry/fixtures.json')
    require(report['fixture_sha256']==hashlib.sha256(fixture.read_bytes()).hexdigest()==original['fixture_sha256'],'fixture drift')
    prompt=next(r for r in read(fixture)['load'] if r['id']=='load-0190')['messages'][0]['content']
    require(report['prompt_sha256']==hashlib.sha256(prompt.encode()).hexdigest(),'prompt drift')
    require(report['selection']=={'pin':'qwen35-4b','fixture':'qwen3.5-4b-q4-k-m','prompt':'load-0190',
            'group':'load','justfile':'scripts/profile-scheduler-activation-probe.just'},'selection drift')
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
            require(step['step']==i,'subject row/position drift')
            require(step['raw_file']==f'subject-logits-{i}.f32','raw path drift')
            require(len(raw[step['raw_file']])==64 and all(c in '0123456789abcdef' for c in raw[step['raw_file']]),'raw digest invalid')
            require(len(step['top'])==20 and all(math.isfinite(v['logit']) for v in step['top']),'nonfinite logits')
        records[key]=record;cells[key]=cell
    require(total==2176,'generated token total')
    calibration={};repeat_rows={};activation_repeats={}
    for mode in MODES:
        archived='solo' if mode.startswith('solo') else 'resident-batched'
        reference=read(prior/'evidence'/('r0-'+archived)/'probe.json')
        reference_hash=read(prior/'evidence'/('r0-'+archived)/'raw-hashes.json')
        calibration[mode]={'tokens_and_text':all(records[r,mode]['lanes']==reference['lanes'] for r in range(2)),
                           'subject_logits':all(cells[r,mode]['raw_sha256']==reference_hash for r in range(2))}
        repeat_rows[mode]=records[0,mode]['lanes']==records[1,mode]['lanes'] and cells[0,mode]['raw_sha256']==cells[1,mode]['raw_sha256']
        activation_repeats[mode]=cells[0,mode]['activation_sha256']==cells[1,mode]['activation_sha256']
        for r in range(2):
            record=records[r,mode];rows=record['activations'];hashes=cells[r,mode]['activation_sha256']
            require(read(root/'evidence'/f'r{r}-{mode}'/'activation-hashes.json')==hashes,'activation hashes drift')
            require(set(hashes)=={x['raw_file'] for x in rows},'activation hash count')
            for x in rows:
                require(x['step'] in (1,2,3) and x['shape']==[x['elements'],1 if mode.startswith('solo') else 16,1,1],'activation column/shape drift')
                require(x['strides'][0]==4 and 0<x['elements']<=300000,'activation stride/count drift')
                require(len(hashes[x['raw_file']])==64 and all(c in '0123456789abcdef' for c in hashes[x['raw_file']]),'activation digest invalid')
            if mode.endswith('plain'):require(not rows,'plain run has activations')
            else:
                for step in (1,2,3):
                    require(any(x['step']==step and x['name']=='attn_norm-0' for x in rows),'missing first-layer coverage')
    comparisons=read(root/'evidence/activation-comparison.json')
    require(comparisons==report['activation_comparison'] and len(comparisons)==2,'comparison export drift')
    first=[]
    for block in comparisons:
        r=block['repeat'];require(r in (0,1),'comparison repeat')
        for row in block['rows']:
            x=cells[r,'solo-trace']['activation_sha256'][row['solo_file']]
            y=cells[r,'batch-trace']['activation_sha256'][row['batch_file']]
            require(row['exact']==(x==y),'comparison hash/exact drift')
            require(math.isfinite(row['max_abs']) and math.isfinite(row['rms']) and row['max_abs']>=row['rms']>=0,'invalid metrics')
        for step in (1,2,3):
            rows=[x for x in block['rows'] if x['key'][0]==step and not x['exact']]
            first.append({'repeat':r,'step':step,'first_captured_difference':rows[0] if rows else None})
    return {'processes_verified':8,'generated_tokens_verified':total,'native_calibration':calibration,
            'repeat_tokens_and_subject_logits_exact':repeat_rows,'repeat_activation_hashes_exact':activation_repeats,
            'first_captured_differences':first,
            'scope':'Selected 2D first-column activation localization only. Requires all calibration true before attribution. Shapes, views, graph fusion and unobserved 3D/4D nodes limit operator claims. Metrics computed privately; raw arrays not downloaded. No runtime fix or timing claim.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--trace',type=Path,required=True);p.add_argument('--prior',type=Path,required=True)
    a=p.parse_args();result=audit(a.artifacts,a.trace,a.prior);(a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
