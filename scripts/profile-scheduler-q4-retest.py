#!/usr/bin/env python3
"""Paired explicit Metal-component operator and Q35 generation retest."""
import argparse
import json
import math
from pathlib import Path
import runpy
import struct
import subprocess
import tarfile
import time
from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def guarded_run(launch,root,env):
    suite.nominal(env,soak=60);host.telemetry(root/'before.json',env)
    host.save(root/'launch.json',{'argv':launch,'environment_names':sorted(env)})
    start=time.time_ns()
    with (root/'operator.log').open('w') as log:
        child=subprocess.Popen(launch,env=env,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+300
            while child.poll() is None:
                if host.thermal_pressure() or host.competing_work(env,child.pid) or time.monotonic()>deadline:
                    raise host.HostUnavailable('operator thermal/contention/deadline gate')
                time.sleep(0.5)
            if child.returncode:raise ValueError('operator failed')
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
    end=time.time_ns();host.telemetry(root/'after.json',env)
    return start,end


def checked(path,root,sha,digest):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or digest(path)!=sha:
        raise ValueError('private input identity drift: '+str(path))
    return path


def component(cache,variant,base,digest):
    bundle=Path(variant['bundle'])
    checked(bundle/'manifest.json',cache,variant['manifest_sha256'],digest)
    manifest=json.loads((bundle/'manifest.json').read_text())
    if manifest['component_rebuild']!=variant['composition']:raise ValueError('component metadata drift')
    if manifest['build']!=base['build']:raise ValueError('primary library changed')
    old=base['runtime']['files'];new=manifest['runtime']['files']
    if set(old)!=set(new):raise ValueError('runtime inventory drift')
    for name,sha in new.items():
        checked(bundle/name,bundle,sha,digest)
        if not name.startswith('lib/libggml-metal.') and sha!=old[name]:raise ValueError('non-Metal replacement')
    return bundle


def build_caller(recipe,headers,bundle,binary,env,log):
    with log.open('w') as stream:
        subprocess.run(['just','--justfile',recipe,'build',str(headers),str(bundle),str(binary)],
                       env=env,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=180)
    identity=json.loads(subprocess.check_output([str(binary),'--identity'],env=env))
    if identity!={'abi':[0,1,87],'runtime_config_bytes':240}:raise ValueError('component ABI drift')


def operator(binary,model,arm,root,inputs,reference,digest):
    directory=root/('operator-'+arm);directory.mkdir();origin=Path(inputs['private_root']);cases=[]
    for record in inputs['records']:checked(origin/record['path'],origin,record['sha256'],digest)
    for case in inputs['cases']:
        row={'label':case['label']}
        for field in ('input','output'):
            c=case[field];row[field]=str(checked(origin/c['path'],origin,c['sha256'],digest))
        cases.append(row)
    host.save(directory/'cases.json',cases);env=host.clean_env(root)
    start,end=guarded_run([str(binary),str(model),str(directory/'cases.json'),str(directory/'reference')],directory,env)
    result=json.loads((directory/'reference/reference.json').read_text())
    if not result['complete'] or len(result['cases'])!=12:raise ValueError('operator count')
    if any(not c['repeat_exact'] or not c['replicated_lanes_exact'] for c in result['cases']):raise ValueError('operator instability')
    if arm=='baseline' and any(not c['archived_first_column_exact'] for c in result['cases']):raise ValueError('rebuilt baseline operator calibration failed')
    errors=[];refroot=Path(reference['private_root'])/'reference'
    for case in result['cases']:
        label=case['label'];name=label+'-reference.f64'
        ref=checked(refroot/name,refroot,reference['private_hashes'][name],digest)
        r=struct.unpack('<4096d',ref.read_bytes())
        path=directory/'reference'/(label+'-metal.f32');data=path.read_bytes();actual=struct.unpack('<4096f',data[:4096*4])
        if not all(math.isfinite(v) for v in (*r,*actual)):raise ValueError('nonfinite arithmetic')
        diff=[x-y for x,y in zip(actual,r)];maximum=max(abs(x) for x in diff)
        errors.append({'label':label,'reference_sha256':digest(ref),'output_sha256':digest(path),
                       'max_abs_error':maximum,'rms_error':math.sqrt(sum(x*x for x in diff)/4096),
                       'relative_l2_error':math.sqrt(sum(x*x for x in diff)/sum(x*x for x in r))})
    for case in inputs['cases']:
        for field in ('input','output'):
            c=case[field];checked(origin/c['path'],origin,c['sha256'],digest)
    return {'directory':directory.name,'start_unix_ns':start,'end_unix_ns':end,'reference':result,
            'fp64_comparison':errors,'report_sha256':digest(directory/'reference/reference.json')}


def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);root=a.cache/f'q4-component-retest-{time.time_ns()}';root.mkdir()
    base=runpy.run_path('scripts/profile-scheduler-native-probe.py');batch=runpy.run_path('scripts/profile-scheduler-batch-probe.py');digest=base['digest']
    provenance=json.loads(Path('q4-retest-inputs.json').read_text())
    build=json.loads(Path('component-build.json').read_text());prior=json.loads(Path('prior-batch.json').read_text())
    reference=json.loads(Path('gate-reference.json').read_text());inputs=json.loads(Path('gate-metal-inputs.json').read_text())
    report={'complete':False,'private_root':str(root),'provenance':provenance,'components':build,'arms':{},'cells':[],
            'scope':'Explicit paired Metal components, original non-Metal libraries retained. Operator and one Q35 prompt only; no general quality or speed claim.'}
    try:
        for name,sha in provenance['files'].items():
            if digest(Path(name))!=sha:raise ValueError('sealed input drift: '+name)
        if not build['complete'] or [v['composition']['arm'] for v in build['variants']]!=['baseline','fixed']:raise ValueError('component build incomplete')
        original=root/'original-preparation';original.mkdir()
        _,model,prompt=base['prepare'](a.cache,original,report,batch['SELECTION'])
        env=host.clean_env(root)
        for variant in build['variants']:
            arm=variant['composition']['arm'];bundle=component(a.cache,variant,report['native_manifest'],digest)
            directory=root/arm;directory.mkdir()
            caller=directory/'native-probe';op=directory/'gate-probe'
            build_caller('scripts/profile-scheduler-batch-probe.just',original/'headers',bundle,caller,env,directory/'build.log')
            build_caller('scripts/profile-scheduler-gate-metal.just',original/'headers',bundle,op,env,directory/'gate-build.log')
            report['arms'][arm]={'bundle':str(bundle),'manifest_sha256':variant['manifest_sha256'],
                'binary_sha256':digest(caller),'operator_binary_sha256':digest(op)}
            report['arms'][arm]['operator']=operator(op,model,arm,root,inputs,reference,digest)
        # Balanced fresh processes: base/fix x solo/batched; reverse both on repeat.
        for repeat in range(2):
            for arm in (('baseline','fixed') if repeat==0 else ('fixed','baseline')):
                for mode in (('solo','resident-batched') if repeat==0 else ('resident-batched','solo')):
                    directory=root/arm
                    print('Q4_RETEST',repeat,arm,mode,flush=True)
                    cell=batch['run_cell'](directory/'native-probe',model,prompt,directory,repeat,mode,digest);cell['arm']=arm
                    record=json.loads((directory/cell['directory']/'probe.json').read_text())
                    old=next(c for c in prior['cells'] if c['repeat']==repeat and c['mode']==mode)
                    archived=Path(prior['private_root'])/old['directory']/'probe.json'
                    checked(archived,Path(prior['private_root']),old['report_sha256'],digest)
                    original_record=json.loads(archived.read_text())
                    cell['archived_tokens_exact']=[r['generated_ids'] for r in record['lanes']]==[r['generated_ids'] for r in original_record['lanes']]
                    cell['archived_logits_exact']=cell['raw_sha256']==old['raw_sha256']
                    report['cells'].append(cell);host.save(a.output/'q4-retest.json',report)
                    if arm=='baseline' and not (cell['archived_tokens_exact'] and cell['archived_logits_exact']):raise ValueError('rebuilt baseline full-model calibration failed')
        report['complete']=True
    except Exception as error:
        report['error']={'type':type(error).__name__,'message':str(error)};raise
    finally:
        host.save(a.output/'q4-retest.json',report)
        with tarfile.open(a.output/'q4-retest-evidence.tar.gz','w:gz') as archive:
            for path in sorted(root.rglob('*')):
                if path.is_file() and not path.is_symlink() and path.suffix in ('.json','.log'):
                    archive.add(path,arcname=str(path.relative_to(root)))


if __name__=='__main__':main()
