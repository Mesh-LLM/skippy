#!/usr/bin/env python3
"""Synthetic replay and negative mutations; not model qualification evidence."""
import copy
import hashlib
import json
from pathlib import Path
import re
import runpy
import tempfile
from lib import profile_scheduler_suite as suite
from lib import profile_kv_host as host


def save(p,x):
    p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    prior=Path('target/scheduler-fixes/artifacts/qwen36-driver-trace')
    controls=Path('target/scheduler-four-models/artifacts/qwen36-correctness')
    fixture_path=Path('target/scheduler-four-models/artifacts/qwen36-r0/fixtures.json')
    read=lambda p: json.loads(p.read_text())
    original,fixture=read(prior/'path-isolation.json'),read(fixture_path)
    audit=runpy.run_path('scripts/profile-scheduler-logprob-audit.py')['audit']
    fix=runpy.run_path('scripts/profile-scheduler-logprob-retest.py')['FIX']
    old=prior/'evidence/normal-first-skippy-base-long-2'
    with tempfile.TemporaryDirectory(prefix='logprob-audit-synthetic-') as temp:
        root=Path(temp);built={'source':suite.SOURCE,'patch_sha256':fix,'binary_sha256':runpy.run_path('scripts/profile-scheduler-logprob-retest.py')['BINARY_SHA']};save(root/'build.json',built)
        record={'complete':True,'private_root':'/synthetic','patch_sha256':fix,'fixed_candidate':built,
                'identity':original['identity'],'original_candidate':original['candidate'],'model':original['model'],
                'fixture_sha256':original['fixture_sha256'],'cells':[]}
        for batch in (4096,512):
            for first in ('normal','logprobs'):
                for arm in (('skippy-base','skippy-patched') if first=='normal' else ('skippy-patched','skippy-base')):
                    name=f'b{batch}-{first}-first-{arm}';d=root/'evidence'/name;d.mkdir(parents=True)
                    launch=read(old/'launch.json');argv=launch['argv'];argv[argv.index('--batch-size')+1]=str(batch)
                    if arm=='skippy-patched':
                        argv[0]='/synthetic/fixed-links/candidate/skippy';at=argv.index('--runtime-bundle')+1
                        argv[at]='/synthetic/fixed-links/candidate/native-runtimes/'+Path(argv[at]).name
                    save(d/'launch.json',launch)
                    for b,t in [('before',0),('after',100000)]: save(d/(b+'.json'),{'native_thermal_pressure_level':0,'wall_ns':t})
                    log='\n'.join(line for line in (old/'server.log').read_text().splitlines() if not line.startswith('{') or '"type":"native_log"' in line)+'\n'
                    log=re.sub(r'(\bn_batch\s*=\s*)512\b',rf'\g<1>{batch}',log)
                    modes=(first,'logprobs' if first=='normal' else 'normal')*2
                    cases=[('long-2',24,m) for m in modes]+[('short-0',128,m) for m in modes[:2]]
                    cell={'batch_size':batch,'first':first,'arm':arm,'directory':name,'complete':True,'attempts':[]}
                    for i,(prompt,count,mode) in enumerate(cases):
                        filename=f'{i}-{mode}.json';response=read(controls/'evidence/r0-skippy-base'/f'{prompt}-{mode}.json');status=200
                        if prompt=='long-2' and batch==4096 and arm=='skippy-base' and mode=='logprobs':
                            status=400;response={'http_status':400,'body':'scheduler iteration exceeds the 2048-token configured iteration limit'}
                        elif arm=='skippy-patched':
                            response['choices'][0]['text']=read(controls/'evidence/r0-skippy-base'/f'{prompt}-normal.json')['choices'][0]['text']
                        request=host.body(record['model'],fixture['mixed'][prompt]['text'],count)
                        if mode=='logprobs':request['logprobs']=5
                        save(d/f'{i}-{mode}-request.json',request);save(d/filename,response)
                        item={'mode':mode,'prompt':prompt,'output_tokens':count,'file':filename,'status':status,'sha256':hashlib.sha256((d/filename).read_bytes()).hexdigest(),'start_unix_ns':i*100+10,'end_unix_ns':i*100+90};cell['attempts'].append(item)
                        events=[('stage.scheduler_route',{'skippy.scheduler.generation_driver':'feature' if mode=='logprobs' else 'built-in'})]
                        if arm=='skippy-patched' and mode=='logprobs':events.append(('stage.openai_prefill',{'llama_stage.prefill_token_count':fixture['mixed'][prompt]['tokens'],'llama_stage.prefill_chunk_count':8 if prompt=='long-2' else 1}))
                        for event,attrs in events:log+=json.dumps({'event':event,'attributes':attrs,'start_time_unix_nanos':item['start_unix_ns'],'end_time_unix_nanos':item['end_unix_ns']})+'\n'
                    (d/'server.log').write_text(log);cell['resolved']=suite.resolved(d/'server.log',arm,16,batch);record['cells'].append(cell)
        save(root/'prefill-retest.json',record)
        result=audit(root,prior,controls,fixture_path,root/'build.json');assert result['responses_verified']==44 and result['fixed_responses_verified']==24
        target=root/'evidence/b512-normal-first-skippy-patched/0-normal-request.json';request=read(target);request['cache_prompt']=True;save(target,request)
        try:audit(root,prior,controls,fixture_path,root/'build.json')
        except ValueError as e:assert str(e)=='request content',e
        else:raise AssertionError('cache mutation accepted')
        request['cache_prompt']=False;save(target,request)
        target=root/'evidence/b512-normal-first-skippy-patched/server.log';target.write_text(target.read_text().replace('"llama_stage.prefill_chunk_count": 8','"llama_stage.prefill_chunk_count": 9'))
        try:audit(root,prior,controls,fixture_path,root/'build.json')
        except ValueError as e:assert str(e)=='fixed chunk trace',e
        else:raise AssertionError('wrong chunk trace accepted')
        print('PASS synthetic44 responses/4errors; cache mutation and false chunk trace rejected. Not native evidence.')


if __name__=='__main__':main()
