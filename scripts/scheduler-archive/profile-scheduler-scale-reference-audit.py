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
    report=read(root/'scale-reference.json');manifest=read(Path('target/scheduler-fixes/scale-inputs.json'))
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
    require(report['source_sha256']==digest(Path('scripts/profile-scheduler-scale-reference.cpp')),'caller drift')
    require(report['just_sha256']==digest(Path('scripts/profile-scheduler-scale-reference.just')),'recipe drift')
    require(report['abi']=={'abi':[0,1,87],'runtime_config_bytes':240},'ABI drift')
    reference=read(root/'evidence/reference/reference.json');require(reference==report['reference'] and reference['complete'],'reference export drift')
    require(reference['weight_name']=='blk.0.attn_gate.weight' and reference['shape']==[2560,4096],'tensor identity drift')
    require(reference['weight_bytes']==reference['row_bytes']*4096>0,'tensor byte size drift')
    hashes=report['private_hashes'];require(hashes['reference.json']==digest(root/'evidence/reference/reference.json'),'reference hash drift')
    require(all(len(h)==64 and all(c in '0123456789abcdef' for c in h) for h in hashes.values()),'malformed private hash')
    require(len(hashes)==62,'private reference file count drift')
    expected=[f'r{r}-{mode}-s{step}' for r in range(2) for mode in ('solo-trace','batch-trace') for step in (1,2,3)]
    require([x['label'] for x in manifest['cases']]==expected==[x['label'] for x in reference['cases']],'case order/count drift')
    for c in reference['cases']:
        require(c['elements']==4096 and 0<=c['worst_row']<4096,'reference count/index drift')
        for key in ('max_abs_error','rms_error','relative_l2_error','max_error_over_generic_fp32_bound','max_fp64_sum_bound','max_plain_vs_compensated'):
            require(math.isfinite(c[key]) and c[key]>=0,'invalid arithmetic metric')
        require(c['max_plain_vs_compensated']<=2*c['max_fp64_sum_bound'],'reference accumulation consistency')
        require(set(c['rounded_reference_errors'])=={'scalehalf','scalef32','scaleftz'},'rounded controls missing')
        for metrics in c['rounded_reference_errors'].values():
            require(all(math.isfinite(v) and v>=0 for v in metrics.values()),'rounded metrics invalid')
            require(metrics['max_abs_error']>=metrics['rms_error'],'rounded RMS invalid')
        require(c['max_abs_error']>=c['rms_error'] and 0<=c['outside_generic_fp32_bound']<=4096,'invalid error accounting')
    agreement={}
    for step in (1,2,3):
        labels=[x for x in expected if x.endswith(f'-s{step}')]
        agreement[str(step)]={suffix:len({hashes[label+'-'+suffix+'.f64'] for label in labels})==1 for suffix in ('reference','compensated','scalehalf','scalef32','scaleftz')}
        require(all(agreement[str(step)].values()),'reference differs for identical input across modes/repeats')
    before,after=[read(root/'evidence'/(name+'.json')) for name in ('before','after')]
    require(before['native_thermal_pressure_level']==after['native_thermal_pressure_level']==0,'reference host thermal boundary')
    require(before['wall_ns']<=report['start_unix_ns']<report['end_unix_ns']<=after['wall_ns'],'reference time window')
    return {'cases_verified':12,'dot_products_verified':49152,'weight_type':reference['weight_type'],'identical_input_reference_hashes':agreement,
            'measurements':reference['cases'],'scope':'Independent FP64 sum over pinned dequantized weights and archived private input/output columns. No generation occurred. Generic FP32 forward-error bound is diagnostic, not a model-quality tolerance, kernel correctness proof or resolution of original failures.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--prior',type=Path,required=True);a=p.parse_args()
    result=audit(a.artifacts,a.prior);(a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
