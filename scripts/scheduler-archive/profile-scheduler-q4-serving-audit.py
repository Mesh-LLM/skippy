#!/usr/bin/env python3
"""Audit exact streaming requests, serving traces and retained Q4 parity failures."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import runpy
from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite

ARMS=('baseline','native-fixed','combined-fixed')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)


def audit(root,prior,build,native,inputs):
    r=read(root/'q4-serving.json');old=read(prior/'batch-history.json');sealed=read(inputs)
    require(r['complete'] and 'error' not in r,'incomplete serving replay')
    require(r['provenance']==sealed,'provenance')
    require(sealed['native_audit_sha256']==sha(native/'audit.json') and sealed['prior_report_sha256']==sha(prior/'batch-history.json'),'prior audit/report hash')
    require(r['components']==read(build/'metal-components.json') and read(build/'audit.json')['pass'],'component identity')
    combined=read(Path('target/scheduler-fixes/artifacts/combined-logprob-build/scheduler-fix-build.json'))
    require(r['combined']==combined,'combined binary provenance')
    require(r['model']==old['model'] and r['pin']==read(Path('ci/inference-artifacts.json'))['qwen35-4b'],'model pin')
    require(r['fixture_sha256']==old['fixture_sha256']=='422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e','fixture')
    fixture=read(Path('target/scheduler-four-models/artifacts/qwen35-c128-retry/fixtures.json'))
    subject=next(v for v in fixture['load'] if v['id']=='load-0190');body=host.body(r['model'],subject['messages'][0]['content'],32,stream=True)
    require(r['request']==body,'request body')
    expected=[(f,a) for f in (1,16) for a in (ARMS if f==1 else tuple(reversed(ARMS)))]
    require([(c['first'],c['arm']) for c in r['cells']]==expected,'process count/order')
    private=Path(r['private_root']);results={};traces=[];count=0
    trace=runpy.run_path('scripts/profile-scheduler-batch-trace-audit.py')['wave_trace']
    for c in r['cells']:
        first,arm=c['first'],c['arm'];name=f'c{first}-first-{arm}';directory=root/'evidence'/name;original=prior/'evidence'/f'c{first}-first-skippy-base'
        metadata=r['arms'][arm];v=next(v for v in r['components']['variants'] if v['composition']['arm']==('baseline' if arm=='baseline' else 'fixed'))
        require(metadata['manifest_sha256']==v['manifest_sha256'] and metadata['bundle']==v['bundle'],'bundle substitution')
        variant=v['composition']['arm'];source_manifest=build/(variant+'-manifest.json')
        require(sha(source_manifest)==v['manifest_sha256'],'source manifest hash')
        view=read(source_manifest);view['build']['component_rebuild']=view.pop('component_rebuild')
        view_bytes=(json.dumps(view,indent=2)+'\n').encode();view_sha=hashlib.sha256(view_bytes).hexdigest()
        require(read(root/('serving-manifest-'+arm+'.json'))==view,'serving manifest content')
        require(metadata['serving_manifest_sha256']==sealed['serving_manifests'][variant]==view_sha,'serving manifest digest')
        require(metadata['serving_bundle']==str(private/('runtime-'+arm)/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal'),'serving view path')
        expected_binary=str(Path(old['identity']['cache'])/'skippy') if arm!='combined-fixed' else str(Path(combined['candidate_path'])/'skippy')
        expected_sha=old['identity']['files']['skippy'] if arm!='combined-fixed' else combined['binary_sha256']
        require(metadata['binary']==expected_binary and metadata['binary_sha256']==expected_sha,'binary substitution')
        launch=copy.deepcopy(read(original/'launch.json'));launch['argv'][0]=str(private/('runtime-'+arm)/'skippy')
        launch['argv'][launch['argv'].index('--runtime-bundle')+1]=str(private/('runtime-'+arm)/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal')
        require(read(directory/'launch.json')==launch,'launch drift')
        resolved=suite.resolved(directory/'server.log','skippy-base',16);reference=suite.resolved(original/'server.log','skippy-base',16)
        require(resolved==c['resolved'] and resolved['gpu_layers']==33,'native report/offload')
        require({k:v for k,v in resolved.items() if k!='kv_buffers'}=={k:v for k,v in reference.items() if k!='kv_buffers'},'native settings')
        pattern=r'size =\s*([\d.]+) MiB.*?K \(f16\):\s*([\d.]+) MiB, V \(f16\):\s*([\d.]+) MiB'
        buffers=[set(re.findall(pattern,'\n'.join(v['kv_buffers']))) for v in (resolved,reference)];require(buffers[0] and buffers[0]==buffers[1],'KV allocations')
        before,after=[read(directory/(v+'.json')) for v in ('before','after')];require(before['native_thermal_pressure_level']==after['native_thermal_pressure_level']==0,'thermal')
        sizes=(first,16 if first==1 else 1)*2;require(c['complete'] and len(c['waves'])==4,'wave completion')
        lines=(directory/'server.log').read_text().splitlines()
        for i,(w,size) in enumerate(zip(c['waves'],sizes)):
            require(w['index']==i and w['concurrency']==size and w['file']==f'{i}-c{size}.json','wave registration')
            file=directory/w['file'];require(sha(file)==w['sha256'],'wave digest');wave=read(file);names={f'subject-{n:02d}' for n in range(size)}
            require(set(wave['requests'])==names and read(directory/f'{i}-requests.json')=={n:body for n in names},'requests')
            texts=[]
            for name,response in wave['requests'].items():
                require(response['usage']['prompt_tokens']==512 and response['usage']['completion_tokens']==32 and response['cached_tokens']==0 and response['finish_reason']=='length','completion/cache/count')
                require(response['offset_ms']==0 and response['send_ms']<response['end_ms'],'request window')
                start=wave['start_unix_ns']+response['send_ms']*1e6;end=wave['start_unix_ns']+response['end_ms']*1e6
                require(before['wall_ns']<=start<end<=after['wall_ns'],'server lifetime')
                text=response['text'];require(hashlib.sha256(text.encode()).hexdigest()==response['text_sha256'],'text digest');texts.append(text);count+=1
                if arm=='baseline':require(text==read(original/w['file'])['requests'][name]['text'],'baseline archived calibration')
            results[first,arm,i]=texts;traces.append({'first':first,'arm':arm,'wave':i,**trace(lines,wave,size)})
    require(count==204,'response count')
    native_text=read(native/'evidence/fixed/r0-solo/probe.json')['lanes'][0]['text'];controls={}
    for arm in ARMS:
        serial=results[1,arm,0][0];alltexts=[t for f in (1,16) for i in range(4) for t in results[f,arm,i]]
        controls[arm]={'all68_equal_fresh_serial':all(t==serial for t in alltexts),'matches_fixed_native_text':sum(t==native_text for t in alltexts),
            'waves':[{'first':f,'index':i,'unique_texts':len(set(results[f,arm,i])),'serial_matches':sum(t==serial for t in results[f,arm,i])} for f in (1,16) for i in range(4)]}
    return {'responses_verified':count,'processes':6,'baseline_calibration':True,'controls':controls,'traces':traces,
        'scope':'One streaming prompt at c1/c16 with two starting histories. Original c128, broader correctness, other-model/cross-engine and performance gates remain open.'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('artifacts','prior','build','native','inputs'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();result=audit(a.artifacts,a.prior,a.build,a.native,a.inputs);(a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['controls'],indent=2))
