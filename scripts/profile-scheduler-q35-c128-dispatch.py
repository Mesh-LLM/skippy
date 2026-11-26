#!/usr/bin/env python3
"""Submit and reconcile the registered Qwen3.5 c128 retest once per phase."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path('target/scheduler-fixes');LEDGER=ROOT/'campaign.json';SPEC=ROOT/'q35-c128-fixed.spec.json'
def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def register(path,ledger):
    phase=read(path)['spec']['labels']['phase']
    if any(j['phase']==phase for j in ledger['jobs']):raise ValueError('phase already registered: '+phase)
    result=subprocess.run(['jy','submit',str(path)],capture_output=True,text=True,check=True)
    path.with_suffix('.submit.txt').write_text(result.stdout+result.stderr)
    ids=set(re.findall(r'job_[a-z0-9]+',result.stdout))
    if len(ids)!=1:raise ValueError('ambiguous submitted job; inspect submit receipt, do not resubmit')
    job=ids.pop();ledger['jobs'].append({'phase':phase,'job_id':job,'status':'queued','spec':str(path),'sha256':sha(path),
        'lease_s':read(path)['spec']['budget']['max_duration_s'],'budget_max_usd':0,'cost_usd':0,'runs':[],
        'artifacts':str(ROOT/'artifacts'/phase),'scope':'Exact Q35 c128 original workload with fixed Metal and combined scheduler; $0 cap.'})
    ledger['updated_at']=datetime.now(timezone.utc).isoformat();save(LEDGER,ledger);print(phase,job)


def reconcile(ledger,transport):
    for j in ledger['jobs']:
        if not j['phase'].startswith('q35-c128-fixed'):continue
        status=json.loads(subprocess.check_output(['jy','job',j['job_id']]))
        save(Path(j['spec']).with_suffix('.status.json'),status)
        j.update(status=status['status'],runs=status['runs'],cost_usd=status['cost_usd'])
        if status['cost_usd']!=0:raise ValueError('nonzero cost')
        hours=sum((datetime.fromisoformat(r['ended_at'].replace('Z','+00:00'))-datetime.fromisoformat(r['started_at'].replace('Z','+00:00'))).total_seconds()/3600 for r in status['runs'] if r['ended_at'] and r['started_at'])
        ledger['completed_occupancy_hours']+=hours-j.get('occupancy_hours',0);j['occupancy_hours']=hours
        if j['status']=='succeeded' and '-stage-' in j['phase'] and not j.get('artifact_hashes'):
            output=Path(j['artifacts']);index=int(j['phase'].rsplit('-',1)[1]);subprocess.run(['python3','scripts/profile-kv-fetch.py',status['runs'][-1]['id'],'--output',str(output),'--name','stage.json'],check=True)
            if read(output/'stage.json')!=transport['chunks'][index]:raise ValueError('stage receipt mismatch')
            j['artifact_hashes']={'stage.json':sha(output/'stage.json')}
        print(j['phase'],j['status']);save(LEDGER,ledger)


def main():
    action=sys.argv[1];ledger=read(LEDGER);transport=read(SPEC.with_suffix('.transport.json'))
    if action=='stage':
        for name in transport['stage_specs']:register(Path(name),ledger)
    elif action=='check':reconcile(ledger,transport)
    elif action=='launch':
        for name in transport['stage_specs']:
            phase=read(Path(name))['spec']['labels']['phase'];j=next(j for j in ledger['jobs'] if j['phase']==phase)
            if j['status']!='succeeded' or not j.get('artifact_hashes'):raise ValueError('unverified stage: '+phase)
        register(SPEC,ledger)
    else:raise ValueError(action)


if __name__=='__main__':main()
