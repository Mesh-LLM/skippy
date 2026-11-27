#!/usr/bin/env python3
"""Seal serving replay after the paired native retest has been audited."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    root=Path('target/scheduler-fixes');native=root/'artifacts/q4-component-retest';build=root/'artifacts/q4-metal-components-v2';prior=root/'artifacts/qwen35-batch-trace'
    result=json.loads((native/'audit.json').read_text())
    if not result['baseline_calibration']:raise ValueError('native baseline calibration missing')
    if not result['model_parity']['fixed']['all_lane_tokens_equal_solo']:raise ValueError('native token parity not qualified')
    select=runpy.run_path('scripts/profile-scheduler-suite-spec.py')['selected_source'];files={}
    files['scripts/profile-scheduler-q4-serving.py']=Path('scripts/profile-scheduler-q4-serving.py').read_text()
    files['scripts/profile-scheduler-serving-bundle.py']=Path('scripts/profile-scheduler-serving-bundle.py').read_text()
    files['scripts/profile-scheduler-native-probe.py']=select('scripts/profile-scheduler-native-probe.py',{'digest'})
    files['scripts/profile-scheduler-q4-retest.py']=select('scripts/profile-scheduler-q4-retest.py',{'component','checked'})
    files['scripts/lib/profile_scheduler_suite.py']=select('scripts/lib/profile_scheduler_suite.py',{'nominal','resolved'})
    files['scripts/lib/profile_kv_host.py']=select('scripts/lib/profile_kv_host.py',{'HostUnavailable','thermal_pressure','save','clean_env','command','telemetry','competing_work','require_idle','stop','launch_args','server','post','body'})
    files['scripts/profile-kv-scheduler.py']=select('scripts/profile-kv-scheduler.py',{'digest','tokenize','request','wave'})
    files['component-build.json']=(build/'metal-components.json').read_text()
    files['combined-build.json']=(root/'artifacts/combined-logprob-build/scheduler-fix-build.json').read_text()
    old=json.loads((prior/'batch-history.json').read_text());waves={}
    for cell in old['cells']:
        for w in cell['waves']:
            data=json.loads((prior/'evidence'/f"c{cell['first']}-first-skippy-base"/w['file']).read_text())
            waves[f"{cell['first']}/{w['index']}"]={k:v['text_sha256'] for k,v in data['requests'].items()}
    files['prior-serving.json']=json.dumps({'identity':old['identity'],'model':old['model'],'baseline_waves':waves})
    files['ci/inference-artifacts.json']=json.dumps({'qwen35-4b':json.loads(Path('ci/inference-artifacts.json').read_text())['qwen35-4b']})
    for name,data in files.items():
        if name.endswith('.py'):compile(data,name,'exec')
    source='671c6ebbf7661b236ce44d352024c51741b08f9c'
    provenance={'source':source,'native_audit_sha256':hashlib.sha256((native/'audit.json').read_bytes()).hexdigest(),
        'prior_report_sha256':hashlib.sha256((prior/'batch-history.json').read_bytes()).hexdigest(),
        'files':{n:hashlib.sha256(d.encode()).hexdigest() for n,d in files.items()}}
    encode=runpy.run_path('scripts/profile-scheduler-serving-bundle.py')['manifest_bytes']
    provenance['serving_manifests']={arm:hashlib.sha256(encode(json.loads((build/(arm+'-manifest.json')).read_text()))).hexdigest() for arm in ('baseline','fixed')}
    files['q4-serving-inputs.json']=json.dumps(provenance,indent=2)+'\n'
    runpy.run_path('scripts/profile-scheduler-q4-retest-spec.py')['seal'](files,source,a.out,
        'scripts/profile-scheduler-q4-serving.py','q4-serving-retest-v2','Q4 fix streaming concurrency replay including combined scheduler',204,'q4-serving-inputs.json')


if __name__=='__main__':main()
