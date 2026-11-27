#!/usr/bin/env python3
"""Ledger-bound dispatch/reconciliation for the registered Q4 serving replay."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path('target/scheduler-fixes')
LEDGER=ROOT/'campaign.json'
SPEC=ROOT/'q4-serving-retest-v2.spec.json'


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def now():return datetime.now(timezone.utc).isoformat()


def submit(path,ledger):
    spec=read(path)['spec'];phase=spec['labels']['phase']
    if any(j['phase']==phase for j in ledger['jobs']):
        print('Already registered:',phase);return
    result=subprocess.run(['jy','submit',str(path)],capture_output=True,text=True,check=True)
    path.with_suffix('.submit.txt').write_text(result.stdout+result.stderr)
    ids=set(re.findall(r'job_[a-z0-9]+',result.stdout))
    if len(ids)!=1:raise ValueError('submission result requires inspection; do not resubmit')
    job=ids.pop();ledger['jobs'].append({'phase':phase,'job_id':job,'status':'queued','spec':str(path),'sha256':sha(path),
        'lease_s':spec['budget']['max_duration_s'],'budget_max_usd':0,'cost_usd':0,'runs':[],
        'artifacts':str(ROOT/'artifacts'/phase),'scope':'Sealed Q4 serving replay' if 'stage' not in phase else 'Harness stage only; no model/build/download.'})
    ledger['updated_at']=now();save(LEDGER,ledger);print(phase,job)


def reconcile(ledger):
    for j in ledger['jobs']:
        if not j['phase'].startswith('q4-serving-retest'):continue
        status=json.loads(subprocess.check_output(['jy','job',j['job_id']]))
        save(Path(j['spec']).with_suffix('.status.json'),status)
        j.update(status=status['status'],runs=status['runs'],cost_usd=status['cost_usd'])
        if j['cost_usd']!=0:raise ValueError('nonzero cost')
        hours=sum((datetime.fromisoformat(r['ended_at'].replace('Z','+00:00'))-datetime.fromisoformat(r['started_at'].replace('Z','+00:00'))).total_seconds()/3600 for r in status['runs'] if r['ended_at'] and r['started_at'])
        ledger['completed_occupancy_hours']+=hours-j.get('occupancy_hours',0);j['occupancy_hours']=hours
        if j['status']=='succeeded' and 'stage' in j['phase']:
            output=Path(j['artifacts']);subprocess.run(['python3','scripts/profile-kv-fetch.py',status['runs'][-1]['id'],'--output',str(output),'--name','stage.json'],check=True)
            stem='q4-serving-retest-v2' if '-v2' in j['phase'] else 'q4-serving-retest'
            transport=read(ROOT/(stem+'.spec.transport.json'));index=int(j['phase'].rsplit('-',1)[1])
            if read(output/'stage.json')!=transport['chunks'][index]:raise ValueError('stage receipt mismatch')
            j['artifact_hashes']={'stage.json':sha(output/'stage.json')}
            j['disposition']='Receipt verified; superseded before model launch by strict-schema serving view.' if '-v2' not in j['phase'] else 'Stage receipt matches sealed chunk.'
        print(j['phase'],j['status']);save(LEDGER,ledger)
    ledger['last_checked_at']=now();save(LEDGER,ledger)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('stage','reconcile','launch'));a=p.parse_args();ledger=read(LEDGER)
    if a.action=='stage':
        for path in read(SPEC.with_suffix('.transport.json'))['stage_specs']:submit(Path(path),ledger)
    elif a.action=='reconcile':reconcile(ledger)
    else:
        for path in read(SPEC.with_suffix('.transport.json'))['stage_specs']:
            phase=read(Path(path))['spec']['labels']['phase'];job=next(j for j in ledger['jobs'] if j['phase']==phase)
            if job['status']!='succeeded' or not job.get('artifact_hashes'):raise ValueError('stage not verified')
        submit(SPEC,ledger)


if __name__=='__main__':main()
