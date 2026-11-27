#!/usr/bin/env python3
"""Audit paired Q4 component replay; retain any candidate model differences."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy
from lib import profile_scheduler_suite as suite


def read(p):return json.loads(p.read_text())
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)


def audit(root,build_root,prior_root,inputs):
    r=read(root/'q4-retest.json');sealed=read(inputs)
    require(r['complete'] and 'error' not in r,'retest incomplete')
    require(r['provenance']==sealed,'sealed provenance drift')
    require(r['components']==read(build_root/'metal-components.json'),'component build drift')
    require(read(build_root/'audit.json')['pass'],'component qualification missing')
    require(r['model']==read(Path('ci/inference-artifacts.json'))['qwen35-4b'],'model pin')
    require(r['native_manifest_sha256']=='c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481','original preparation drift')
    require(r['abi']=={'abi':[0,1,87],'runtime_config_bytes':240},'ABI')
    old=read(prior_root/'batch-probe.json')
    require(r['fixture_sha256']==old['fixture_sha256'] and r['prompt_sha256']==old['prompt_sha256'],'fixture/prompt drift')
    require(r['header_manifest_sha256']==old['header_manifest_sha256'],'header provenance drift')
    records={};cells={};total=0
    expected=[(rep,arm,mode) for rep in range(2) for arm in (('baseline','fixed') if rep==0 else ('fixed','baseline')) for mode in (('solo','resident-batched') if rep==0 else ('resident-batched','solo'))]
    require([(c['repeat'],c['arm'],c['mode']) for c in r['cells']]==expected,'process order/count')
    calls=runpy.run_path('scripts/profile-scheduler-batch-probe-audit.py')['verify_calls']
    for c in r['cells']:
        arm=c['arm'];name=f"r{c['repeat']}-{c['mode']}";directory=root/'evidence'/arm/name
        require(c['directory']==name,'directory')
        record=read(directory/'probe.json');require(digest(directory/'probe.json')==c['report_sha256'],'cell hash')
        require(record['complete'] and record['mode']==c['mode'],'cell completion')
        require(hashlib.sha256(json.dumps(record['prompt_ids']).encode()).hexdigest()=='83de68a4172aa4dab09da1e24667392b20f8b52fcaf916593d77a40b5713281e','tokens')
        size=calls(record);require(len(record['lanes'])==size and [v['lane'] for v in record['lanes']]==list(range(size)),'lanes')
        require(all(len(v['generated_ids'])==32 for v in record['lanes']),'completion counts');total+=size*32
        native=suite.resolved(root/'evidence'/arm/(name+'.log'),'skippy-base',16)
        require(native==c['resolved'] and native['gpu_layers']==33,'native offload')
        original=next(v for v in old['cells'] if v['repeat']==c['repeat'] and v['mode']==c['mode'])
        require(native==original['resolved'],'native settings/allocations')
        oldrecord=read(prior_root/'evidence'/original['directory']/'probe.json')
        exact=[v['generated_ids'] for v in record['lanes']]==[v['generated_ids'] for v in oldrecord['lanes']]
        raw=read(directory/'raw-hashes.json');require(raw==c['raw_sha256'] and len(raw)==32,'raw hashes')
        require(c['archived_tokens_exact']==exact and c['archived_logits_exact']==(raw==original['raw_sha256']),'calibration reporting')
        if arm=='baseline':require(exact and raw==original['raw_sha256'],'baseline calibration')
        require(record['lanes'][0]['generated_ids']==[s['token'] for s in record['subject_steps']],'subject tokens')
        require(len(record['subject_steps'])==32,'subject count')
        for i,s in enumerate(record['subject_steps']):
            require(s['step']==i and s['position']==512+i and s['batch_row']==(511 if i==0 else 0),'subject row')
            require(s['raw_file']==f'subject-logits-{i}.f32' and len(raw[s['raw_file']])==64,'subject raw')
            require(len(s['top'])==20 and all(math.isfinite(v['logit']) for v in s['top']),'finite logits')
        private=Path(r['private_root']);launch=read(directory/'launch.json')['argv']
        pin=r['model'];model=Path('/Users/Shared/jianyang/model-store/huggingface/hub')/('models--'+pin['repo'].replace('/','--'))/'snapshots'/pin['revision']/pin['files'][0]['path']
        require(launch==[str(private/arm/'native-probe'),str(model),str(private/'original-preparation/prompt.txt'),c['mode'],str(private/arm/name)],'launch')
        before,after=[read(directory/(b+'.json')) for b in ('before','after')]
        require(before['native_thermal_pressure_level']==after['native_thermal_pressure_level']==0,'thermal')
        require(before['wall_ns']<=c['start_unix_ns']<c['end_unix_ns']<=after['wall_ns'],'time window')
        records[c['repeat'],arm,c['mode']]=record;cells[c['repeat'],arm,c['mode']]=c
    require(total==2176,'total tokens')
    operators={}
    for arm in ('baseline','fixed'):
        data=r['arms'][arm];variant=next(v for v in r['components']['variants'] if v['composition']['arm']==arm)
        require(data['bundle']==variant['bundle'] and data['manifest_sha256']==variant['manifest_sha256'],'arm bundle')
        op=data['operator'];directory=root/'evidence'/('operator-'+arm)
        require(op['report_sha256']==digest(directory/'reference/reference.json'),'operator hash')
        require(op['reference']==read(directory/'reference/reference.json'),'operator export')
        require(op['reference']['backend']=='MTL0' and op['reference']['shape']==[2560,4096],'operator shape/backend')
        require(len(op['reference']['cases'])==len(op['fp64_comparison'])==12,'operator count')
        before,after=[read(directory/(b+'.json')) for b in ('before','after')]
        require(before['native_thermal_pressure_level']==after['native_thermal_pressure_level']==0,'operator thermal')
        require(before['wall_ns']<=op['start_unix_ns']<op['end_unix_ns']<=after['wall_ns'],'operator window')
        labels=[f'r{repeat}-{mode}-s{step}' for repeat in range(2) for mode in ('solo-trace','batch-trace') for step in (1,2,3)]
        require([c['label'] for c in op['reference']['cases']]==labels==[c['label'] for c in op['fp64_comparison']],'operator labels/order')
        for case in op['reference']['cases']:
            width=16 if 'batch-trace' in case['label'] else 1
            require(case['width']==width and case['elements']==4096*width,'operator dimensions')
            require(case['repeat_exact'] and case['replicated_lanes_exact'],'operator repeat')
            if arm=='baseline':require(case['archived_first_column_exact'],'operator calibration')
        for v in op['fp64_comparison']:
            require(all(math.isfinite(v[k]) and v[k]>=0 for k in ('max_abs_error','rms_error','relative_l2_error')),'operator finite errors')
        operators[arm]=op
    repeat={};parity={}
    for arm in ('baseline','fixed'):
        repeat[arm]={mode: {'tokens':records[0,arm,mode]['lanes']==records[1,arm,mode]['lanes'],
            'logits':cells[0,arm,mode]['raw_sha256']==cells[1,arm,mode]['raw_sha256']} for mode in ('solo','resident-batched')}
        require(all(all(v.values()) for v in repeat[arm].values()),'repeat instability')
        a,b=records[0,arm,'solo'],records[0,arm,'resident-batched']
        parity[arm]={'all_lane_tokens_equal_solo':all(v['generated_ids']==a['lanes'][0]['generated_ids'] for v in b['lanes']),
            'subject_logits_equal':cells[0,arm,'solo']['raw_sha256']==cells[0,arm,'resident-batched']['raw_sha256'],
            'first_subject_token_difference':next((i for i,(x,y) in enumerate(zip(a['lanes'][0]['generated_ids'],b['lanes'][0]['generated_ids'])) if x!=y),None)}
    changes=[]
    for base,fixed in zip(operators['baseline']['fp64_comparison'],operators['fixed']['fp64_comparison']):
        changes.append({'label':base['label'],'baseline_max_abs_error':base['max_abs_error'],'fixed_max_abs_error':fixed['max_abs_error'],'max_error_reduced':fixed['max_abs_error']<base['max_abs_error']})
    return {'operator_error_changes':changes,'processes':8,'generated_tokens':total,'baseline_calibration':True,'repeat':repeat,'model_parity':parity,
            'operator_fp64':{arm:op['fp64_comparison'] for arm,op in operators.items()},
            'scope':'One Q35 prompt, native solo/grouped paths and saved gate inputs only. Exact synthetic regression separately qualified. Model differences retained; no broad quality, other-model, serving-load or performance clearance.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--build',type=Path,required=True);p.add_argument('--prior',type=Path,required=True);p.add_argument('--inputs',type=Path,required=True);a=p.parse_args()
    result=audit(a.artifacts,a.build,a.prior,a.inputs);(a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
