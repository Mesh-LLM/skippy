#!/usr/bin/env python3
"""Exercise serving evidence gates using archived controls and injected faults."""
import copy
import json
from pathlib import Path
import runpy
import shutil
import tempfile


def read(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    audit=runpy.run_path('scripts/profile-scheduler-q4-serving-audit.py')['audit'];root=Path('target/scheduler-fixes')
    prior=root/'artifacts/qwen35-batch-trace';build=root/'artifacts/q4-metal-components-v2';native=root/'artifacts/q4-component-retest';inputs=root/'q4-serving-retest-v2.spec.inputs.json'
    old=read(prior/'batch-history.json');components=read(build/'metal-components.json');combined=read(root/'artifacts/combined-logprob-build/scheduler-fix-build.json')
    private=Path('/private/synthetic-serving')
    with tempfile.TemporaryDirectory(prefix='q4-serving-audit-') as temp:
        dest=Path(temp);report={'complete':True,'private_root':str(private),'provenance':read(inputs),'components':components,'combined':combined,
            'model':old['model'],'pin':read(Path('ci/inference-artifacts.json'))['qwen35-4b'],'fixture_sha256':old['fixture_sha256'],'arms':{},'cells':[]}
        first_request=read(prior/'evidence/c1-first-skippy-base/0-requests.json');report['request']=next(iter(first_request.values()))
        arms=('baseline','native-fixed','combined-fixed')
        for arm in arms:
            v=next(v for v in components['variants'] if v['composition']['arm']==('baseline' if arm=='baseline' else 'fixed'))
            report['arms'][arm]={'bundle':v['bundle'],'manifest_sha256':v['manifest_sha256'],
                'binary':str(Path(old['identity']['cache'])/'skippy') if arm!='combined-fixed' else str(Path(combined['candidate_path'])/'skippy'),
                'binary_sha256':old['identity']['files']['skippy'] if arm!='combined-fixed' else combined['binary_sha256']}
            variant=v['composition']['arm'];view=read(build/(variant+'-manifest.json'));view['build']['component_rebuild']=view.pop('component_rebuild')
            save(dest/('serving-manifest-'+arm+'.json'),view)
            report['arms'][arm].update(serving_bundle=str(private/('runtime-'+arm)/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal'),serving_manifest_sha256=read(inputs)['serving_manifests'][variant])
        for first in (1,16):
            for arm in arms if first==1 else tuple(reversed(arms)):
                cell=copy.deepcopy(next(c for c in old['cells'] if c['first']==first));cell['arm']=arm;report['cells'].append(cell)
                directory=dest/'evidence'/f'c{first}-first-{arm}';shutil.copytree(prior/'evidence'/f'c{first}-first-skippy-base',directory)
                launch=read(directory/'launch.json');launch['argv'][0]=str(private/('runtime-'+arm)/'skippy');launch['argv'][launch['argv'].index('--runtime-bundle')+1]=str(private/('runtime-'+arm)/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal');save(directory/'launch.json',launch)
        save(dest/'q4-serving.json',report);result=audit(dest,prior,build,native,inputs);assert result['responses_verified']==204
        assert not result['controls']['native-fixed']['all68_equal_fresh_serial']
        for label,change in [('binary substitution',lambda x:x['arms']['combined-fixed'].__setitem__('binary_sha256','0'*64)),('offload',lambda x:x['cells'][0]['resolved'].__setitem__('gpu_layers',32)),('cache request',lambda x:x['request'].__setitem__('cache_prompt',True))]:
            bad=copy.deepcopy(report);change(bad);save(dest/'q4-serving.json',bad)
            try:audit(dest,prior,build,native,inputs)
            except ValueError:print('Rejected:',label)
            else:raise AssertionError('accepted '+label)
        print('PASS: archived synthetic204response/24wave trace audit; original differences retained;3mutations rejected. Not candidate hardware evidence.')


if __name__=='__main__':main()
