#!/usr/bin/env python3
"""Seal a paired Metal component build with original non-Metal libraries."""
import base64
import hashlib
import json
import lzma
from pathlib import Path
import runpy
import subprocess


def main():
    source='671c6ebbf7661b236ce44d352024c51741b08f9c'
    select=runpy.run_path('scripts/profile-scheduler-suite-spec.py')['selected_source']
    files={p:Path(p).read_text() for p in ['scripts/profile-scheduler-metal-component.py',
        'scripts/profile-scheduler-metal-component.just','scripts/profile-scheduler-q4-scale-regression.cpp']}
    files['target/q4-scale.patch']=Path('target/scheduler-fixes/q4-scale-native.patch').read_text()
    files['scripts/lib/profile_scheduler_suite.py']=select('scripts/lib/profile_scheduler_suite.py',{'nominal'})
    files['scripts/lib/profile_kv_host.py']=select('scripts/lib/profile_kv_host.py',{
        'HostUnavailable','thermal_pressure','save','clean_env','command','telemetry','competing_work'})
    for name,data in files.items():
        if name.endswith('.py'):compile(data,name,'exec')
    hashes={n:hashlib.sha256(d.encode()).hexdigest() for n,d in files.items()}
    packed=base64.b85encode(lzma.compress(json.dumps(files).encode())).decode()
    command="set -eu\ncd skippy\npython3 - <<'INSTALL'\nimport base64,json,lzma\nfrom pathlib import Path\n"
    command+="for name,data in json.loads(lzma.decompress(base64.b85decode("+repr(packed)+"))).items():\n p=Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)\nINSTALL\n"
    command+='env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" JIANYANG_ARTIFACT_DIR="$JIANYANG_ARTIFACT_DIR" python3 scripts/profile-scheduler-metal-component.py\n'
    if len(command.encode())>16384:raise ValueError('sealed command exceeds limit')
    subprocess.run(['bash','-n'],input=command,text=True,check=True)
    replace=runpy.run_path('scripts/profile-kv-spec.py')['replace']
    spec=replace(json.loads(Path('evals/kv-profiling/jy/build.json.in').read_text()),{
        '@SOURCE_SHA@':source,'@MODEL_MANIFEST_SHA256@':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),'@COMMAND@':command})
    spec['spec']['name']='Q4 scale paired Metal-only component builds and exact regression'
    spec['spec']['labels'].update(campaign='scheduler-fixes',phase='q4-metal-components')
    spec['spec']['budget']['max_duration_s']=4200
    spec['spec']['harness']['request_count']=14
    out=Path('target/scheduler-fixes/q4-metal-components.spec.json')
    out.write_text(json.dumps(spec,indent=2)+'\n')
    out.with_suffix('.inputs.json').write_text(json.dumps({'source':source,'files':hashes},indent=2)+'\n')
    print(out,hashlib.sha256(out.read_bytes()).hexdigest(),'command bytes',len(command.encode()))


if __name__=='__main__':main()
