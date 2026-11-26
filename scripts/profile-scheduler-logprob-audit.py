#!/usr/bin/env python3
"""Audit mode parity without clearing unrelated model or concurrency failures."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import runpy
from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def require(value, message):
    if not value:
        raise ValueError(message)


def audit(root, prior, controls, fixture_path, build, expected_patch=None, expected_binary=None):
    runner = runpy.run_path(str(Path(__file__).with_name('profile-scheduler-logprob-retest.py')))
    trace = runpy.run_path(str(Path(__file__).with_name('profile-scheduler-path-isolation-audit.py')))['driver_trace']
    tokens = runpy.run_path(str(Path(__file__).with_name('profile-scheduler-correctness-audit.py')))['tokens']
    record, original, built, fixture = read(root/'prefill-retest.json'), read(prior/'path-isolation.json'), read(build), read(fixture_path)
    require(record['complete'] and 'error' not in record, 'incomplete replay')
    require(record['patch_sha256'] == (expected_patch or runner['FIX']) == built['patch_sha256'], 'patch drift')
    require(record['fixed_candidate'] == built and built['source'] == suite.SOURCE and built['binary_sha256'] == (expected_binary or runner['BINARY_SHA']), 'build drift')
    require(record['identity'] == original['identity'] and record['original_candidate'] == original['candidate'], 'original identities drift')
    require(record['model'] == original['model'] and record['model']['key'] == runner['MODEL'], 'model drift')
    require(hashlib.sha256(fixture_path.read_bytes()).hexdigest() == record['fixture_sha256'] == runner['FIXTURE_SHA'], 'fixture drift')
    order = [(b, first, arm) for b in (4096,512) for first in ('normal','logprobs')
             for arm in (('skippy-base','skippy-patched') if first=='normal' else ('skippy-patched','skippy-base'))]
    require([(c['batch_size'],c['first'],c['arm']) for c in record['cells']] == order, 'process order/count drift')
    old = prior/'evidence/normal-first-skippy-base-long-2'
    choices, failures, traces = [], [], []
    for cell in record['cells']:
        batch, first, arm = cell['batch_size'],cell['first'],cell['arm']
        name = f'b{batch}-{first}-first-{arm}'
        require(cell['directory'] == name and cell['complete'], 'cell incomplete/path drift')
        directory = root/'evidence'/name
        launch = copy.deepcopy(read(old/'launch.json'))
        argv = launch['argv'];argv[argv.index('--batch-size')+1] = str(batch)
        if arm == 'skippy-patched':
            linked = Path(record['private_root'])/'fixed-links/candidate'
            argv[0] = str(linked/'skippy')
            argv[argv.index('--runtime-bundle')+1] = str(linked/'native-runtimes'/Path(argv[argv.index('--runtime-bundle')+1]).name)
        require(read(directory/'launch.json') == launch, 'launch drift')
        native = suite.resolved(directory/'server.log',arm,16,batch)
        require(native == cell['resolved'], 'native evidence differs')
        baseline = suite.resolved(old/'server.log','skippy-base',16);baseline['n_batch'] = [str(batch)]
        require({k:v for k,v in native.items() if k!='kv_buffers'} == {k:v for k,v in baseline.items() if k!='kv_buffers'}, 'native settings drift')
        pattern = r'size =\s*([\d.]+) MiB.*?K \(f16\):\s*([\d.]+) MiB, V \(f16\):\s*([\d.]+) MiB'
        buffers = [set(re.findall(pattern,'\n'.join(v['kv_buffers']))) for v in (native,baseline)]
        require(buffers[0] and buffers[0] == buffers[1], 'KV memory drift')
        before,after = [read(directory/(b+'.json')) for b in ('before','after')]
        require(before['native_thermal_pressure_level'] == after['native_thermal_pressure_level'] == 0, 'thermal boundary')
        modes = (first,'logprobs' if first=='normal' else 'normal')*2
        cases = [('long-2',24,m) for m in modes]+[('short-0',128,m) for m in modes[:2]]
        require(len(cell['attempts']) == 6,'attempt count')
        for index,(item,(prompt,count,mode)) in enumerate(zip(cell['attempts'],cases)):
            require((item['prompt'],item['output_tokens'],item['mode']) == (prompt,count,mode), 'request order')
            require(item['file'] == f'{index}-{mode}.json','response path')
            require(before['wall_ns'] <= item['start_unix_ns'] < item['end_unix_ns'] <= after['wall_ns'],'request window')
            path = directory/item['file'];require(hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'],'response hash')
            request = host.body(record['model'],fixture['mixed'][prompt]['text'],count)
            if mode=='logprobs': request['logprobs']=5
            require(read(directory/f'{index}-{mode}-request.json') == request,'request content')
            response = read(path)
            if prompt=='long-2' and batch==4096 and arm=='skippy-base' and mode=='logprobs':
                require(item['status'] == response['http_status'] == 400 and '2048-token configured iteration limit' in response['body'],'baseline rejection')
                failures.append(response);continue
            require(item['status']==200,'unexpected failure')
            choice,usage=response['choices'][0],response['usage']
            require(usage['prompt_tokens']==fixture['mixed'][prompt]['tokens'] and usage['completion_tokens']==count and choice['finish_reason']=='length','completion')
            cached=(usage.get('prompt_tokens_details') or {}).get('cached_tokens')
            if cached is None: cached=(response.get('timings') or {}).get('cache_n')
            require(cached==0,'cache hit/missing counter')
            if mode=='logprobs':
                rows=tokens(choice)
                require(len(rows)==count and all(math.isfinite(r['logprob']) and all(math.isfinite(v) for v in r['top'].values()) for r in rows),'invalid logprobs')
            archived=read(controls/'evidence/r0-skippy-base'/f'{prompt}-{mode}.json')['choices'][0]
            ordinary=read(controls/'evidence/r0-skippy-base'/f'{prompt}-normal.json')['choices'][0]
            if arm=='skippy-base':
                require(choice['text']==archived['text'],'baseline output changed')
                if mode=='logprobs': require(tokens(choice)==tokens(archived),'baseline logprobs changed')
            else:
                require(choice['text']==ordinary['text'],'fixed output differs from archived ordinary')
            observed=trace(directory,item)
            require(observed['driver']==('feature' if mode=='logprobs' else 'built-in'),'route drift')
            if arm=='skippy-patched' and mode=='logprobs':
                prefills=[e['attributes'] for e in observed['events'] if e['event']=='stage.openai_prefill']
                require(len(prefills)==1 and prefills[0]['llama_stage.prefill_token_count']==fixture['mixed'][prompt]['tokens']
                        and prefills[0]['llama_stage.prefill_chunk_count']==(8 if prompt=='long-2' else 1),'fixed chunk trace')
            traces.append({'batch':batch,'first':first,'arm':arm,'index':index,**observed})
            choices.append({'batch':batch,'first':first,'arm':arm,'prompt':prompt,'mode':mode,'choice':choice})
    require(len(choices)==44 and len(failures)==4,'accounting mismatch')
    checks=[]
    for prompt in ('long-2','short-0'):
        fixed=[c for c in choices if c['arm']=='skippy-patched' and c['prompt']==prompt]
        logged=[c['choice'] for c in fixed if c['mode']=='logprobs']
        text_exact=all(c['choice']['text']==fixed[0]['choice']['text'] for c in fixed)
        logs_exact=all(tokens(c)==tokens(logged[0]) for c in logged)
        require(text_exact and logs_exact,'fixed mode/repeat/batch discrepancy')
        checks.append({'prompt':prompt,'fixed_responses':len(fixed),'text_exact_across_modes_repeats_batches':text_exact,'logprobs_exact_across_repeats_batches':logs_exact})
    return {'responses_verified':44,'fixed_responses_verified':24,'expected_baseline_errors':4,
            'controls':checks,'baseline_errors':failures,'driver_traces':traces,
            'scope':'Registered cache-off serial mode parity only; no cross-engine, concurrent, other-model or performance clearance.'}


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('artifacts','prior','controls','fixture','build'): p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--expected-patch', help='Explicit preregistered candidate SHA256')
    p.add_argument('--expected-binary', help='Explicit preregistered candidate SHA256')
    a=p.parse_args()
    if bool(a.expected_patch) != bool(a.expected_binary): p.error('candidate patch and binary must be supplied together')
    result=audit(a.artifacts,a.prior,a.controls,a.fixture,a.build,a.expected_patch,a.expected_binary)
    (a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('driver_traces','baseline_errors')}))
