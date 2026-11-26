#!/usr/bin/env python3
"""Audit provenance and arithmetic-reference evidence without a quality verdict."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def read(path): return json.loads(path.read_text())
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value,message):
    if not value: raise ValueError(message)


def audit(root,prior):
    report=read(root/'gate-metal.json');manifest=read(Path('target/scheduler-fixes/gate-metal-inputs.json'))
    require(report['complete'] and 'error' not in report,'reference incomplete')
    require(report['inputs']==manifest,'input manifest drift')
    require(manifest['activation_report_sha256']==digest(prior/'activation-probe.json'),'prior report drift')
    require(manifest['activation_audit_sha256']==digest(prior/'audit.json'),'prior audit drift')
    require(all(all(c.values()) for c in read(prior/'audit.json')['native_calibration'].values()),'prior calibration failed')
    require(report['model']==read(Path('ci/inference-artifacts.json'))['qwen35-4b'],'model pin drift')
    require(report['native_source']=='6704435209b0fa8eabc3aa91e3c0a22f92b89863','native source drift')
    require(report['native_manifest_sha256']=='c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481','library drift')
    require(report['header_manifest_sha256']==digest(Path('target/scheduler-fixes/native-headers/manifest.json')),'headers drift')
    require(read(root/'evidence/header-provenance.json')==read(Path('target/scheduler-fixes/native-headers/manifest.json')),'header evidence drift')
    require(report['source_sha256']==digest(Path('scripts/profile-scheduler-gate-metal.cpp')),'caller drift')
    require(report['just_sha256']==digest(Path('scripts/profile-scheduler-gate-metal.just')),'recipe drift')
    require(report['abi']=={'abi':[0,1,87],'runtime_config_bytes':240},'ABI drift')
    reference=read(root/'evidence/reference/reference.json');require(reference==report['reference'] and reference['complete'],'reference export drift')
    require(reference['weight_name']=='blk.0.attn_gate.weight' and reference['shape']==[2560,4096],'tensor identity drift')
    require(reference['backend']=='MTL0','Metal backend missing')
    hashes=report['private_hashes'];require(hashes['reference.json']==digest(root/'evidence/reference/reference.json'),'reference hash drift')
    require(len(hashes)==13 and all(len(h)==64 for h in hashes.values()),'private file hashes')
    expected=[f'r{r}-{mode}-s{step}' for r in range(2) for mode in ('solo-trace','batch-trace') for step in (1,2,3)]
    require([x['label'] for x in manifest['cases']]==expected==[x['label'] for x in reference['cases']],'case order/count')
    for c in reference['cases']:
        width=16 if 'batch-trace' in c['label'] else 1
        require(c['width']==width and c['elements']==4096*width,'operator shape')
        require(math.isfinite(c['max_abs_vs_archived']) and c['max_abs_vs_archived']>=0,'nonfinite metric')
        require(c['repeat_exact'] and c['replicated_lanes_exact'],'operator repeat/lane failure')
    calibration={c['label']:c['archived_first_column_exact'] for c in reference['cases']}
    repeat_hashes={f'{mode}-s{step}':hashes[f'r0-{mode}-s{step}-metal.f32']==hashes[f'r1-{mode}-s{step}-metal.f32'] for mode in ('solo-trace','batch-trace') for step in (1,2,3)}
    require(all(repeat_hashes.values()),'cross-repeat mismatch')
    before,after=[read(root/'evidence'/(name+'.json')) for name in ('before','after')]
    require(before['native_thermal_pressure_level']==after['native_thermal_pressure_level']==0,'reference host thermal boundary')
    require(before['wall_ns']<=report['start_unix_ns']<report['end_unix_ns']<=after['wall_ns'],'reference time window')
    return {'cases_verified':12,'graph_executions':24,'backend':reference['backend'],
            'archived_calibration':calibration,'all_archived_exact':all(calibration.values()),'repeat_hashes':repeat_hashes,
            'measurements':reference['cases'],
            'scope':'Original Metal isolated Q4_K MUL_MAT, widths1/16 with replicated saved input. Require archived output byte calibration before linking operator results to full model. No new generation, runtime change or quality clearance.'}



if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--prior',type=Path,required=True);a=p.parse_args()
    result=audit(a.artifacts,a.prior);(a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
