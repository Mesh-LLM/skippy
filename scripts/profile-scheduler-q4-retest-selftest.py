#!/usr/bin/env python3
"""Replay archived evidence through new audit; inject provenance/calibration faults."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tempfile


def read(p):return json.loads(p.read_text())
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    audit=runpy.run_path('scripts/profile-scheduler-q4-retest-audit.py')['audit']
    prior=Path('target/scheduler-fixes/artifacts/qwen35-native-batch-probe');old=read(prior/'batch-probe.json')
    gate=Path('target/scheduler-fixes/artifacts/qwen35-gate-metal');g=read(gate/'gate-metal.json')
    ref=read(Path('target/scheduler-fixes/artifacts/qwen35-gate-reference/gate-reference.json'))
    private=Path('/private/synthetic-q4-fixture')
    with tempfile.TemporaryDirectory(prefix='q4-audit-') as temp:
        root=Path(temp);build=root/'build';build.mkdir();artifact=root/'artifact';artifact.mkdir();inputs=root/'inputs.json';save(inputs,{'synthetic':True})
        report={k:old[k] for k in ['model','native_manifest_sha256','abi','fixture_sha256','prompt_sha256','header_manifest_sha256']}
        report.update(complete=True,private_root=str(private),provenance=read(inputs),components={'variants':[]},arms={},cells=[])
        for arm in ('baseline','fixed'):
            variant={'bundle':'/private/component-'+arm,'manifest_sha256':'a'*64,'composition':{'arm':arm}}
            report['components']['variants'].append(variant)
            directory=artifact/'evidence'/('operator-'+arm);shutil.copytree(gate/'evidence/reference',directory/'reference')
            for name in ['before.json','after.json']:shutil.copyfile(gate/'evidence'/name,directory/name)
            report['arms'][arm]={'bundle':variant['bundle'],'manifest_sha256':variant['manifest_sha256'],
                'operator':{'report_sha256':sha(directory/'reference/reference.json'),'reference':g['reference'],
                    'fp64_comparison':[{k:c[k] for k in ('label','max_abs_error','rms_error','relative_l2_error')} for c in ref['reference']['cases']],
                    'start_unix_ns':g['start_unix_ns'],'end_unix_ns':g['end_unix_ns']}}
        for rep in range(2):
            for arm in (('baseline','fixed') if rep==0 else ('fixed','baseline')):
                for mode in (('solo','resident-batched') if rep==0 else ('resident-batched','solo')):
                    c=copy.deepcopy(next(c for c in old['cells'] if c['repeat']==rep and c['mode']==mode));name=c['directory'];c.update(arm=arm,archived_tokens_exact=True,archived_logits_exact=True)
                    dest=artifact/'evidence'/arm/name;shutil.copytree(prior/'evidence'/name,dest)
                    shutil.copyfile(prior/'evidence'/(name+'.log'),dest.parent/(name+'.log'))
                    launch=read(dest/'launch.json');argv=launch['argv'];argv[0]=str(private/arm/'native-probe');argv[2]=str(private/'original-preparation/prompt.txt');argv[4]=str(private/arm/name);save(dest/'launch.json',launch)
                    report['cells'].append(c)
        save(build/'metal-components.json',report['components']);save(build/'audit.json',{'pass':True});save(artifact/'q4-retest.json',report)
        result=audit(artifact,build,prior,inputs);assert result['generated_tokens']==2176 and result['baseline_calibration']
        for label,mutate in [('native offload',lambda x:x['cells'][0]['resolved'].__setitem__('gpu_layers',32)),
                             ('false baseline calibration',lambda x:x['cells'][0].__setitem__('archived_logits_exact',False)),
                             ('bundle substitution',lambda x:x['arms']['fixed'].__setitem__('manifest_sha256','b'*64))]:
            bad=copy.deepcopy(report);mutate(bad);save(artifact/'q4-retest.json',bad)
            try:audit(artifact,build,prior,inputs)
            except ValueError:print('Rejected:',label)
            else:raise AssertionError('accepted '+label)
        print('PASS: synthetic archived evidence replay8processes/2176tokens and three gate mutations; not candidate hardware qualification.')


if __name__=='__main__':main()
