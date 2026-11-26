#!/usr/bin/env python3
"""Seal the paired Q4 retest only after component build audit passes."""
import argparse
import base64
import hashlib
import json
import lzma
from pathlib import Path
import runpy
import subprocess


def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not json.loads((a.build/'audit.json').read_text())['pass']:raise ValueError('component audit not passed')
    source='671c6ebbf7661b236ce44d352024c51741b08f9c';select=runpy.run_path('scripts/profile-scheduler-suite-spec.py')['selected_source']
    paths=['scripts/profile-scheduler-q4-retest.py','scripts/profile-scheduler-batch-probe.cpp','scripts/profile-scheduler-batch-probe.just','scripts/profile-scheduler-gate-metal.cpp','scripts/profile-scheduler-gate-metal.just']
    files={p:Path(p).read_text() for p in paths}
    files['scripts/profile-scheduler-batch-probe.py']=select('scripts/profile-scheduler-batch-probe.py',{'run_cell'})
    files['scripts/profile-scheduler-native-probe.py']=select('scripts/profile-scheduler-native-probe.py',{'digest','prepare'})
    files['scripts/lib/profile_scheduler_suite.py']=select('scripts/lib/profile_scheduler_suite.py',{'nominal','resolved'})
    files['scripts/lib/profile_kv_host.py']=select('scripts/lib/profile_kv_host.py',{'HostUnavailable','thermal_pressure','save','clean_env','command','telemetry','competing_work'})
    for name,path in {'component-build.json':a.build/'metal-components.json','prior-batch.json':Path('target/scheduler-fixes/artifacts/qwen35-native-batch-probe/batch-probe.json'),
        'gate-reference.json':Path('target/scheduler-fixes/artifacts/qwen35-gate-reference/gate-reference.json'),
        'gate-metal-inputs.json':Path('target/scheduler-fixes/gate-metal-inputs.json'),'native-headers.json':Path('target/scheduler-fixes/native-headers/manifest.json')}.items():files[name]=path.read_text()
    source_reports={n:hashlib.sha256(files[n].encode()).hexdigest() for n in ('prior-batch.json','gate-reference.json','gate-metal-inputs.json')}
    prior=json.loads(files['prior-batch.json'])
    files['prior-batch.json']=json.dumps({'private_root':prior['private_root'],'cells':[
        {k:c[k] for k in ('repeat','mode','directory','report_sha256','raw_sha256')} for c in prior['cells'] if c['mode'] in ('solo','resident-batched')]})
    ref=json.loads(files['gate-reference.json'])
    files['gate-reference.json']=json.dumps({'private_root':ref['private_root'],'private_hashes':{n:h for n,h in ref['private_hashes'].items() if n.endswith('-reference.f64')}})
    gate=json.loads(files['gate-metal-inputs.json'])
    for c in gate['cases']:
        for field in ('input','output'):c[field]={k:c[field][k] for k in ('path','sha256')}
    files['gate-metal-inputs.json']=json.dumps(gate)
    files['ci/inference-artifacts.json']=json.dumps({'qwen35-4b':json.loads(Path('ci/inference-artifacts.json').read_text())['qwen35-4b']})
    for name,data in files.items():
        if name.endswith('.py'):compile(data,name,'exec')
    provenance={'source':source,'original_report_sha256':source_reports,'build_audit_sha256':hashlib.sha256((a.build/'audit.json').read_bytes()).hexdigest(),
        'files':{n:hashlib.sha256(d.encode()).hexdigest() for n,d in files.items()}}
    files['q4-retest-inputs.json']=json.dumps(provenance,indent=2)+'\n'
    seal(files,source,a.out,'scripts/profile-scheduler-q4-retest.py','q4-component-retest','Q4 scale paired Metal operator and Qwen3.5 native retest',68,'q4-retest-inputs.json')


def seal(files,source,out,runner,phase,name,count,input_name):
    payload=lzma.compress(json.dumps(files).encode())
    payload_sha=hashlib.sha256(payload).hexdigest()
    cache='.cache/skippy/kv-profile/'+source+'/q4-retest-payload-'+payload_sha
    chunks=[payload[i:i+9000] for i in range(0,len(payload),9000)]
    chunk_records=[{'name':f'chunk-{i}.xz','sha256':hashlib.sha256(data).hexdigest()} for i,data in enumerate(chunks)]
    replace=runpy.run_path('scripts/profile-kv-spec.py')['replace']
    template=json.loads(Path('evals/kv-profiling/jy/measure.json.in').read_text())
    stages=[]
    for i,(data,record) in enumerate(zip(chunks,chunk_records)):
        command="set -eu\npython3 - <<'STAGE'\nimport base64,hashlib,json,os\nfrom pathlib import Path\n"
        command+="root=Path.home()/"+repr(cache)+";root.mkdir(parents=True,exist_ok=True,mode=0o700)\n"
        command+="data=base64.b85decode("+repr(base64.b85encode(data).decode())+")\n"
        command+="assert hashlib.sha256(data).hexdigest()=="+repr(record['sha256'])+"\np=root/"+repr(record['name'])+"\n"
        command+="if p.exists():\n assert p.read_bytes()==data\nelse:\n with p.open('xb') as f:f.write(data)\n"
        command+="(Path(os.environ['JIANYANG_ARTIFACT_DIR'])/'stage.json').write_text(json.dumps("+repr(record)+"))\nSTAGE\n"
        if len(command.encode())>16384:raise ValueError('stage too large')
        subprocess.run(['bash','-n'],input=command,text=True,check=True)
        spec=replace(template,{'@SOURCE_SHA@':source,'@MODEL_MANIFEST_SHA256@':payload_sha,'@COMMAND@':command,'@PHASE@':f'{phase}-stage-{i}','@DURATION@':300})
        spec['spec']['labels'].update(campaign='scheduler-fixes',phase=f'{phase}-stage-{i}')
        spec['spec']['name']=f'Stage sealed Q4 retest harness chunk {i}'
        path=out.with_name(out.stem+f'.stage-{i}.json');path.write_text(json.dumps(spec,indent=2)+'\n');stages.append(str(path))
    command="set -eu\npython3 - <<'INSTALL'\nimport hashlib,json,lzma\nfrom pathlib import Path\n"
    command+="root=Path.home()/"+repr(cache)+";parts=[]\n"
    command+="for item in "+repr(chunk_records)+":\n p=root/item['name'];data=p.read_bytes();assert not p.is_symlink() and hashlib.sha256(data).hexdigest()==item['sha256'];parts.append(data)\n"
    command+="packed=b''.join(parts);assert hashlib.sha256(packed).hexdigest()=="+repr(payload_sha)+"\n"
    command+="for name,data in json.loads(lzma.decompress(packed)).items():\n p=Path(name);assert not p.is_absolute() and '..' not in p.parts;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)\nINSTALL\n"
    command+='env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" python3 '+runner+' --cache "$HOME/.cache/skippy/kv-profile/'+source+'" --output "$JIANYANG_ARTIFACT_DIR"\n'
    subprocess.run(['bash','-n'],input=command,text=True,check=True)
    spec=replace(template,{'@SOURCE_SHA@':source,'@MODEL_MANIFEST_SHA256@':payload_sha,'@COMMAND@':command,'@PHASE@':phase,'@DURATION@':3600})
    out.with_suffix('.transport.json').write_text(json.dumps({'payload_sha256':payload_sha,'cache':cache,'chunks':chunk_records,'stage_specs':stages},indent=2)+'\n')
    spec['spec']['name']=name
    spec['spec']['labels'].update(campaign='scheduler-fixes',phase=phase)
    spec['spec']['harness'].update(warmup_requests=0,request_count=count,concurrency=[1,16])
    out.write_text(json.dumps(spec,indent=2)+'\n');out.with_suffix('.inputs.json').write_text(files[input_name])
    print(out,hashlib.sha256(out.read_bytes()).hexdigest(),'command bytes',len(command.encode()))


if __name__=='__main__':main()
