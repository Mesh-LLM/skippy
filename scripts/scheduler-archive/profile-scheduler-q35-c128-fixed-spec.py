#!/usr/bin/env python3
"""Seal the registered original c128 workload with fixed Metal and frozen reference."""
import hashlib
import json
from pathlib import Path
import runpy


def main():
    root=Path('target/scheduler-fixes');out=root/'q35-c128-fixed.spec.json'
    build=root/'artifacts/q4-metal-components-v2';native=root/'artifacts/q4-component-retest';serving=root/'artifacts/q4-serving-retest-v2'
    if not json.loads((build/'audit.json').read_text())['pass']:raise ValueError('component audit missing')
    if json.loads((native/'audit.json').read_text())['model_parity']['fixed']['all_lane_tokens_equal_solo'] is not True:raise ValueError('native parity missing')
    if json.loads((serving/'audit.json').read_text())['controls']['combined-fixed']['all68_equal_fresh_serial'] is not True:raise ValueError('serving parity missing')
    ref=json.loads(Path('target/scheduler-four-models/qwen35-frozen-reference.json').read_text())
    if ref['ref_e2e_ms']!=791.173594:raise ValueError('frozen latency changed')
    select=runpy.run_path('scripts/profile-scheduler-suite-spec.py')['selected_source']
    paths=['scripts/profile-scheduler-q35-c128-fixed.py','scripts/profile-scheduler-suite.py','scripts/profile-scheduler-serving-bundle.py']
    files={p:Path(p).read_text() for p in paths}
    files['scripts/lib/profile_scheduler_suite.py']=Path('scripts/lib/profile_scheduler_suite.py').read_text()
    files['scripts/lib/profile_kv_host.py']=Path('scripts/lib/profile_kv_host.py').read_text()
    files['scripts/profile-kv-scheduler.py']=select('scripts/profile-kv-scheduler.py',{'digest','tokenize','fixtures','request','wave'})
    files['scripts/profile-kv-scheduler-qualification.py']=select('scripts/profile-kv-scheduler-qualification.py',{'file_digest','qualified_arms'})
    files['scripts/profile-scheduler-native-probe.py']=select('scripts/profile-scheduler-native-probe.py',{'digest'})
    files['scripts/profile-scheduler-q4-retest.py']=select('scripts/profile-scheduler-q4-retest.py',{'component','checked'})
    files['component-build.json']=(build/'metal-components.json').read_text()
    files['combined-build.json']=(root/'artifacts/combined-logprob-build/scheduler-fix-build.json').read_text()
    pin=json.loads(Path('ci/inference-artifacts.json').read_text())['qwen35-4b'];files['ci/inference-artifacts.json']=json.dumps({'qwen35-4b':pin})
    source_manifest=json.loads((build/'fixed-manifest.json').read_text())
    serving_sha=hashlib.sha256(runpy.run_path('scripts/profile-scheduler-serving-bundle.py')['manifest_bytes'](source_manifest)).hexdigest()
    provenance={'files':{n:hashlib.sha256(v.encode()).hexdigest() for n,v in files.items()},
        'model_sha256':pin['files'][0]['sha256'],'original_manifest_sha256':'c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481',
        'serving_manifest_sha256':serving_sha,'combined_patch_sha256':json.loads(files['combined-build.json'])['patch_sha256'],
        'frozen_reference_sha256':hashlib.sha256(Path('target/scheduler-four-models/qwen35-frozen-reference.json').read_bytes()).hexdigest(),
        'original_c128_spec_sha256':'273bc0afa9bc1d8f39ad38db95533a64bff6e6126a8879cef4611afc0b0a479d',
        'native_audit_sha256':hashlib.sha256((native/'audit.json').read_bytes()).hexdigest(),
        'serving_audit_sha256':hashlib.sha256((serving/'audit.json').read_bytes()).hexdigest()}
    files['q35-c128-fixed-inputs.json']=json.dumps(provenance,indent=2)+'\n'
    for name,data in files.items():
        if name.endswith('.py'):compile(data,name,'exec')
    runpy.run_path('scripts/profile-scheduler-q4-retest-spec.py')['seal'](files,'671c6ebbf7661b236ce44d352024c51741b08f9c',out,
        'scripts/profile-scheduler-q35-c128-fixed.py','q35-c128-fixed','Qwen3.5 fixed Metal original heterogeneous c128 four-round replay',2048,'q35-c128-fixed-inputs.json')
    job=json.loads(out.read_text());job['spec']['budget']['max_duration_s']=10800;job['spec']['name']='Qwen3.5 fixed Metal c128 original workload four-round retest'
    job['spec']['harness'].update(version='scheduler-suite-v1',request_count=2048,warmup_requests=16,concurrency=[128])
    out.write_text(json.dumps(job,indent=2)+'\n');print('final',hashlib.sha256(out.read_bytes()).hexdigest())


if __name__=='__main__':main()
