#!/usr/bin/env python3
"""Audit the original c128 workload against explicitly fixed Skippy components."""
import argparse
import base64
import hashlib
import json
import lzma
from pathlib import Path
import re
import runpy

from lib import profile_scheduler_suite as suite


def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value,message):
    if not value:raise ValueError(message)


def staged_inputs():
    prefix=Path('target/scheduler-fixes/q35-c128-fixed.spec')
    transport=read(Path(str(prefix)+'.transport.json'))
    chunks=[]
    for index,chunk in enumerate(transport['chunks']):
        stage=read(Path(transport['stage_specs'][index]))
        encoded=re.search(r"data=base64\.b85decode\('([^']+)'\)",stage['spec']['runtime']['command'])
        require(encoded is not None,f'missing staged chunk {index}')
        data=base64.b85decode(encoded.group(1))
        require(hashlib.sha256(data).hexdigest()==chunk['sha256'],f'staged chunk {index} digest')
        require(read(Path(str(prefix)+f'.stage-{index}.status.json'))['status']=='succeeded',
                f'staging job {index} failed')
        chunks.append(data)
    payload=b''.join(chunks)
    require(hashlib.sha256(payload).hexdigest()==transport['payload_sha256'],'sealed payload digest')
    files=json.loads(lzma.decompress(payload))
    return json.loads(files['q35-c128-fixed-inputs.json']),files


def audit(root,prior,build,inputs):
    report=read(root/'suite.json');sealed=read(inputs);component=read(build/'metal-components.json')
    combined_path=Path('target/scheduler-fixes/artifacts/combined-logprob-build/scheduler-fix-build.json')
    combined=read(combined_path)
    require(report['complete'] and 'error' not in report,'incomplete job')
    staged,files=staged_inputs()
    require(staged==sealed,'sealed input manifest')
    for name,digest in sealed['files'].items():
        require(name in files and hashlib.sha256(files[name].encode()).hexdigest()==digest,
                f'sealed input digest: {name}')
    require(sha(build/'metal-components.json')==sealed['files']['component-build.json']
            and sha(combined_path)==sealed['files']['combined-build.json'],'sealed build inputs')
    pin=json.loads(files['ci/inference-artifacts.json'])['qwen35-4b']
    require(report['model']['repo']==pin['repo'] and report['model']['revision']==pin['revision']
            and len(report['model']['files'])==1
            and report['model']['files'][0]['name']==pin['files'][0]['path']
            and report['model']['files'][0]['sha256']==pin['files'][0]['sha256']
            and report['model']['size_bytes']==pin['files'][0]['size'],'exact CI model pin')
    require(report['model']['key']=='qwen3.5-4b-q4-k-m' and report['model']['source_model_sha256']==sealed['model_sha256'],'model identity')
    require(report['candidate']==combined and combined['patch_sha256']==sealed['combined_patch_sha256'],'candidate identity')
    require(report['identity']==read(prior/'suite.json')['identity'],'original binary/native identity')
    require(report['rounds']==[0,1,2,3] and report['ref_e2e_ms']==791.173594 and report['offered_concurrency']==128,'registered workload')
    require(read(root/'fixtures.json')==read(prior/'fixtures.json'),'original heterogeneous fixtures')
    require(report['lanes']==16 and report['cooldown_s']==120 and report['prefix_cache'] is False,'registered native/thermal settings')
    require(sha(Path('target/scheduler-four-models/qwen35-frozen-reference.json'))==sealed['frozen_reference_sha256'],'frozen reference')
    require(sha(Path('target/scheduler-fixes/artifacts/q4-component-retest/audit.json'))==sealed['native_audit_sha256'],'native audit')
    require(sha(Path('target/scheduler-fixes/artifacts/q4-serving-retest-v2/audit.json'))==sealed['serving_audit_sha256'],'serving audit')
    require(sha(Path('target/scheduler-four-models/specs/qwen35-load-c128-thermal-retry.json'))==sealed['original_c128_spec_sha256'],'original workload spec')
    original=read(build/'original-manifest.json');variant=next(v for v in component['variants'] if v['composition']['arm']=='fixed')
    require(sha(build/'original-manifest.json')==sealed['original_manifest_sha256'],'original manifest')
    view=read(build/'fixed-manifest.json');require(sha(build/'fixed-manifest.json')==variant['manifest_sha256'],'component manifest')
    view['build']['component_rebuild']=view.pop('component_rebuild')
    require(hashlib.sha256((json.dumps(view,indent=2)+'\n').encode()).hexdigest()==sealed['serving_manifest_sha256'],'serving view hash')
    require({name:old for name,old in original['runtime']['files'].items() if not name.startswith('lib/libggml-metal.')}
            =={name:old for name,old in view['runtime']['files'].items() if not name.startswith('lib/libggml-metal.')},'non-Metal library drift')
    private=Path(report['private_root']);identity=report['identity']
    for arm in ('skippy-base','skippy-patched'):
        directory=root/'evidence'/('fixed-'+arm)/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal'
        require(read(directory/'manifest.json')==view,'fixed serving manifest')
        require(read(directory/'component-source-manifest.json')==read(build/'fixed-manifest.json'),'component provenance')
        argv=report['preflight'][arm]['argv'];require(argv[0]==str(private/('fixed-'+arm)/'skippy'),'binary route')
        require(argv[argv.index('--runtime-bundle')+1]==str(private/('fixed-'+arm)/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal'),'bundle route')
    for arm,preflight in report['preflight'].items():
        native=preflight['resolved'];argv=preflight['argv']
        require(native['gpu_layers']==33 and '131072' in native['n_ctx']
                and native['n_batch']==['512'] and native['n_ubatch']==['512']
                and any('f16' in line for line in native['kv_buffers']),f'native settings: {arm}')
        if arm.startswith('skippy'):
            for flag,value in (('--ctx-size','8192'),('--generation-concurrency','16'),
                               ('--batch-size','512'),('--microbatch-size','512'),
                               ('--kv-cache-type-k','f16'),('--kv-cache-type-v','f16')):
                require(argv[argv.index(flag)+1]==value,f'launch setting {arm}: {flag}')
        else:
            for flag,value in (('-np','16'),('-c','131072'),('-b','512'),('-ub','512'),
                               ('-ctk','f16'),('-ctv','f16')):
                require(argv[argv.index(flag)+1]==value,f'launch setting {arm}: {flag}')
    suite.PATCH=combined['patch_sha256']
    result=runpy.run_path('scripts/profile-scheduler-suite-audit.py')['audit'](report,root/'evidence',read(root/'fixtures.json'))
    prior_report=read(prior/'suite.json');require(prior_report['rounds']==[0,1,2,3] and prior_report['offered_concurrency']==128,'prior comparison workload')
    changed={};round_drift={}
    for arm in suite.ARMS:
        rows=[];fixed_rounds=[]
        for round_index in range(4):
            directory=f'r{round_index}-{arm}'
            def outputs(base):
                files=(base/'evidence'/directory/'outputs'/arm).rglob('*.json')
                return {record['id']:hashlib.sha256(record['text'].encode()).hexdigest()
                        for file in files if file.name!='index.json' for record in [read(file)]}
            before,after=outputs(prior),outputs(root)
            require(set(before)==set(after) and len(before)==128,'original/new prompt coverage')
            fixed_rounds.append(after)
            rows.extend({'round':round_index,'prompt':key,'original_sha256':before[key],'fixed_sha256':after[key]}
                        for key in sorted(before) if before[key]!=after[key])
        changed[arm]={'changed_count':len(rows),'changed':rows}
        drift=[key for key in fixed_rounds[0]
               if len({round_outputs[key] for round_outputs in fixed_rounds})!=1]
        round_drift[arm]=drift
    result['versus_original_c128']=changed
    result['cross_round_output_drift']=round_drift
    result['source_job']='job_txcg43nzentnsd4';result['fixed_component_manifest_sha256']=sealed['serving_manifest_sha256']
    result['scope']='Exact four-round c128 replay with fixed Skippy Metal components; cross-engine and performance claims depend on observed mismatches/SLOs.'
    statistics=runpy.run_path('scripts/profile-scheduler-statistics.py')
    sources=[];enriched=statistics['cells_for'](root,runpy.run_path('scripts/profile-scheduler-suite-audit.py'),sources)
    detailed=statistics['summarize']('Qwen3.5 fixed Metal c128',[enriched])
    (root/'statistics.json').write_text(json.dumps({'group':detailed,'sources':sources},indent=2)+'\n')
    lines=['# Qwen3.5 fixed Metal c128 retest','',
           'Four fresh-process rounds, original heterogeneous 128-request arrival workload. '
           'Medians are across rounds, not pooled requests. SLO: TTFT p95 ≤30 s and TPOT p95 ≤1 s.','',
           '| Arm | Complete | TTFT p95 ms | E2E p95 ms | TPOT p95 ms | Output tok/s | SLO rounds |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for arm,row in detailed['summary'].items():
        lines.append(f"| {arm} | {row['completed']} | {row['ttft_p95_ms']:.1f} | {row['e2e_p95_ms']:.1f} | {row['tpot_p95_ms']:.1f} | {row['output_tok_s_over_client_span']:.2f} | {row['latency_slo_pass_rounds']}/4 |")
    lines.extend(['', 'Paired four-round ratios use 256 empirical bootstrap samples. '
                  'Latency ratios below 1 and throughput ratios above 1 favor the comparison arm.', '',
                  '| Comparison / baseline | TTFT p95 ratio (95% interval) | E2E p95 ratio (95% interval) | Output tok/s ratio (95% interval) |',
                  '|---|---:|---:|---:|'])
    for comparison in detailed['comparisons']:
        def ratio(metric):
            value=comparison['metrics'][metric];lo,hi=value['paired_percentile_95']
            return f"{value['median_round_ratio']:.3f} ({lo:.3f}–{hi:.3f})"
        lines.append(f"| {comparison['comparison']} / {comparison['baseline']} | {ratio('ttft_p95_ms')} | {ratio('e2e_p95_ms')} | {ratio('output_tok_s_over_client_span')} |")
    mismatch_prompts=sorted({row['prompt'] for row in result['exact_output_mismatches']})
    change_counts=', '.join(f"{arm} {row['changed_count']}" for arm,row in changed.items())
    lines.extend(['',f"Cross-arm exact-output mismatches: {len(result['exact_output_mismatches'])} comparisons across "
                  f"{', '.join(mismatch_prompts)}; all four rounds. ",
                  f"Cross-round output drift: {sum(map(len,round_drift.values()))} prompts. "
                  f"Changes from the original failed c128 run: {change_counts}.",
                  'Every arm misses the registered 30-second TTFT p95 SLO, although TPOT p95 is below 1 second. '
                  'The cross-engine output differences and SLO failures block an overall winner claim. '
                  'Full per-round values, output hashes, and paired intervals are in audit.json and statistics.json.'])
    (root/'statistics.md').write_text('\n'.join(lines)+'\n')
    result['statistics']='statistics.json'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('artifacts','prior','build','inputs'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();result=audit(a.artifacts,a.prior,a.build,a.inputs)
    (a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(len(result['cells']),'cells',len(result['exact_output_mismatches']),'cross-arm output mismatches')
