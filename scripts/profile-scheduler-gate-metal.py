#!/usr/bin/env python3
"""Read-only private-capture arithmetic reference; no model generation."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import tarfile
import time
from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite

SELECTION={'pin':'qwen35-4b','fixture':'qwen3.5-4b-q4-k-m','prompt':'load-0190','group':'load',
           'justfile':'scripts/profile-scheduler-gate-metal.just'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);root=a.cache/f'qwen35-gate-metal-{time.time_ns()}';root.mkdir()
    base=runpy.run_path('scripts/profile-scheduler-native-probe.py');digest=base['digest']
    manifest=json.loads(Path('gate-metal-inputs.json').read_text())
    report={'complete':False,'private_root':str(root),'inputs':manifest,'scope':'Isolated original Metal gate projection; repeated captured input columns. No model generation or runtime edit.'}
    try:
        binary,model,_=base['prepare'](a.cache,root,report,SELECTION)
        report['source_sha256']=digest(Path('scripts/profile-scheduler-gate-metal.cpp'))
        report['just_sha256']=digest(Path(SELECTION['justfile']))
        origin=Path(manifest['private_root']);cases=[]
        if not origin.resolve().is_relative_to(a.cache.resolve()):raise ValueError('private input root escaped cache')
        for record in manifest['records']:
            path=origin/record['path']
            if path.is_symlink() or not path.resolve().is_relative_to(origin.resolve()) or digest(path)!=record['sha256']:
                raise ValueError('private record identity drift')
        for c in manifest['cases']:
            case={'label':c['label']}
            for field in ('input','output'):
                path=origin/c[field]['path']
                if path.is_symlink() or not path.resolve().is_relative_to(origin.resolve()) or digest(path)!=c[field]['sha256']:
                    raise ValueError('private array identity drift')
                case[field]=str(path)
            cases.append(case)
        host.save(root/'cases.json',cases);env=host.clean_env(root);suite.nominal(env,soak=60)
        host.telemetry(root/'before.json',env)
        launch=[str(binary),str(model),str(root/'cases.json'),str(root/'reference')];host.save(root/'launch.json',{'argv':launch,'environment_names':sorted(env)})
        report['start_unix_ns']=time.time_ns()
        with (root/'reference.log').open('w') as log:
            child=subprocess.Popen(launch,env=env,stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+300
                while child.poll() is None:
                    if host.thermal_pressure() or host.competing_work(env,child.pid) or time.monotonic()>deadline:
                        raise host.HostUnavailable('reference host/deadline gate')
                    time.sleep(0.5)
                if child.returncode:raise ValueError('reference calculation failed')
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=10)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
        report['end_unix_ns']=time.time_ns();host.telemetry(root/'after.json',env)
        # Recheck immutable captures after the read-only calculation.
        for c in manifest['cases']:
            for field in ('input','output'):
                if digest(origin/c[field]['path'])!=c[field]['sha256']:raise ValueError('private array changed')
        reference=root/'reference';report['reference']=json.loads((reference/'reference.json').read_text())
        report['private_hashes']={f.name:digest(f) for f in reference.iterdir() if f.is_file()}
        if not report['reference']['complete'] or len(report['reference']['cases'])!=12:raise ValueError('reference incomplete')
        report['complete']=True
    except Exception as error:
        report['error']={'type':type(error).__name__,'message':str(error)};raise
    finally:
        host.save(a.output/'gate-metal.json',report)
        with tarfile.open(a.output/'gate-metal-evidence.tar.gz','w:gz') as tar:
            for f in sorted(root.rglob('*')):
                if f.is_file() and not f.is_symlink() and f.suffix in ('.json','.log'):tar.add(f,arcname=str(f.relative_to(root)))


if __name__=='__main__':main()
