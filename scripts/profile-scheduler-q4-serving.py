#!/usr/bin/env python3
"""Replay streaming Q35 concurrency with explicit native component identities."""
import argparse
import json
from pathlib import Path
import runpy
import tarfile
import time
from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite

ARMS=('baseline','native-fixed','combined-fixed')


def run(cache,output,root,report):
    base=runpy.run_path('scripts/profile-scheduler-native-probe.py');digest=base['digest']
    components=runpy.run_path('scripts/profile-scheduler-q4-retest.py')
    serving=runpy.run_path('scripts/profile-scheduler-serving-bundle.py')
    replay=runpy.run_path('scripts/profile-kv-scheduler.py')
    prior=json.loads(Path('prior-serving.json').read_text());build=json.loads(Path('component-build.json').read_text())
    combined=json.loads(Path('combined-build.json').read_text());provenance=json.loads(Path('q4-serving-inputs.json').read_text())
    for name,sha in provenance['files'].items():
        if digest(Path(name))!=sha:raise ValueError('sealed input drift: '+name)
    original=cache/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json'
    if digest(original)!='c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481':raise ValueError('original manifest drift')
    manifest=json.loads(original.read_text());pin=json.loads(Path('ci/inference-artifacts.json').read_text())['qwen35-4b'];model=prior['model']
    if model['repo']!=pin['repo'] or model['revision']!=pin['revision'] or len(pin['files'])!=1:raise ValueError('CI pin drift')
    f=pin['files'][0];path=Path('/Users/Shared/jianyang/model-store/huggingface/hub')/('models--'+pin['repo'].replace('/','--'))/'snapshots'/pin['revision']/f['path']
    if Path(model['path'])!=path or path.stat().st_size!=f['size'] or digest(path)!=f['sha256']:raise ValueError('model shard drift')
    fixture=cache/'scheduler-suite-v1-qwen3.5-4b-q4-k-m.json'
    if digest(fixture)!='422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e':raise ValueError('fixture drift')
    subject=next(r for r in json.loads(fixture.read_text())['load'] if r['id']=='load-0190');row={'text':subject['messages'][0]['content'],'tokens':512}
    request=host.body(model,row['text'],32,stream=True)
    roots={};report.update(provenance=provenance,components=build,combined=combined,model=model,pin=pin,fixture_sha256=digest(fixture),request=request,arms={})
    for arm in ARMS:
        v=next(v for v in build['variants'] if v['composition']['arm']==('baseline' if arm=='baseline' else 'fixed'))
        bundle=components['component'](cache,v,manifest,digest)
        binary=cache/'skippy' if arm!='combined-fixed' else Path(combined['candidate_path'])/'skippy'
        expected=prior['identity']['files']['skippy'] if arm!='combined-fixed' else combined['binary_sha256']
        if digest(binary)!=expected:raise ValueError('serving binary drift')
        linked=root/('runtime-'+arm);linked.mkdir();(linked/'skippy').symlink_to(binary);(linked/'native-runtimes').mkdir()
        view_sha=provenance['serving_manifests'][v['composition']['arm']]
        view=serving['materialize'](bundle,linked/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal',view_sha)
        roots[arm]=linked;report['arms'][arm]={'binary':str(binary),'binary_sha256':expected,'bundle':str(bundle),'manifest_sha256':v['manifest_sha256'],
            'serving_bundle':str(view),'serving_manifest_sha256':view_sha}
        host.save(output/('serving-manifest-'+arm+'.json'),json.loads((view/'manifest.json').read_text()))
    for first in (1,16):
        sizes=(first,16 if first==1 else 1)*2
        for arm in ARMS if first==1 else tuple(reversed(ARMS)):
            directory=root/f'c{first}-first-{arm}';directory.mkdir();env=host.clean_env(directory)
            env['SKIPPY_TELEMETRY_STDERR']='1';suite.nominal(env,soak=60)
            argv=host.launch_args(roots[arm],model,'skippy',16)+['--kv-cache-disk','off','--telemetry-level','debug','--telemetry-queue-capacity','65536']
            cell={'arm':arm,'first':first,'complete':False,'waves':[]};report['cells'].append(cell)
            print('Q4_SERVING',first,arm,flush=True)
            with host.server(roots[arm],model,'skippy',directory,env,16,diagnostic=True,argv=argv) as child:
                cell['resolved']=suite.resolved(directory/'server.log','skippy-base',16)
                if cell['resolved']['gpu_layers']!=33:raise ValueError('offload drift')
                ids=replay['tokenize'](model,'skippy',row['text'])
                if replay['digest'](json.dumps(ids).encode())!='83de68a4172aa4dab09da1e24667392b20f8b52fcaf916593d77a40b5713281e':raise ValueError('tokenizer drift')
                for i,size in enumerate(sizes):
                    suite.nominal(env,child.pid,30);schedule=tuple((f'subject-{j:02d}',0,32) for j in range(size));rows={name:row for name,_,_ in schedule}
                    host.save(directory/f'{i}-requests.json',{name:request for name in rows})
                    reading=replay['wave'](model,rows,schedule,False,env,child.pid,capture_text=True)
                    if any(v['cached_tokens']!=0 for v in reading['requests'].values()):raise ValueError('cache counters')
                    filename=f'{i}-c{size}.json';host.save(directory/filename,reading)
                    wave={'index':i,'concurrency':size,'file':filename,'sha256':digest(directory/filename)};cell['waves'].append(wave)
                    host.save(output/'q4-serving.json',report)
                    if arm=='baseline':
                        old=prior['baseline_waves'][f'{first}/{i}']
                        if {k:v['text_sha256'] for k,v in reading['requests'].items()}!=old:raise ValueError('serving baseline calibration failed')
            cell['complete']=True;host.save(output/'q4-serving.json',report)
    report['complete']=True


def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);root=a.cache/f'q4-serving-retest-{time.time_ns()}';root.mkdir()
    report={'complete':False,'private_root':str(root),'cells':[],
        'scope':'Streaming load-0190 c1/c16 with reversed fresh-process history. Three explicit binary/component arms, no timing or full c128/cross-engine clearance.'}
    try:run(a.cache,a.output,root,report)
    except Exception as error:report['error']={'type':type(error).__name__,'message':str(error)};raise
    finally:
        host.save(a.output/'q4-serving.json',report)
        with tarfile.open(a.output/'q4-serving-evidence.tar.gz','w:gz') as tar:
            for f in sorted(root.rglob('*')):
                if f.is_file() and not f.is_symlink() and f.suffix in ('.json','.log') and not str(f.relative_to(root)).startswith('runtime-'):
                    tar.add(f,arcname=str(f.relative_to(root)))


if __name__=='__main__':main()
