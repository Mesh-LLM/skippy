#!/usr/bin/env python3
"""Audit paired component identities and exact synthetic arithmetic results."""
import argparse
import hashlib
import json
from pathlib import Path


def read(p):return json.loads(p.read_text())
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)


def audit(root,inputs):
    report=read(root/'metal-components.json');sealed=read(inputs)['files']
    require(report['complete'] and 'error' not in report,'build incomplete')
    require(report['builder_sha256']==sealed['scripts/profile-scheduler-metal-component.py'],'builder drift')
    require(report['recipe_sha256']==sealed['scripts/profile-scheduler-metal-component.just'],'recipe drift')
    require(report['patch_sha256']==sealed['target/q4-scale.patch'],'patch drift')
    original=read(root/'original-manifest.json')
    require(digest(root/'original-manifest.json')=='c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481','original manifest drift')
    files=original['runtime']['files'];metal={n for n in files if n.startswith('lib/libggml-metal.')}
    require(len(metal)==3,'original alias count')
    require(len(report['variants'])==2,'arm count')
    results=[];caches=[]
    for arm,source,v in zip(['baseline','fixed'],['6704435209b0fa8eabc3aa91e3c0a22f92b89863','e09c584b22b280a93903058129a76ac7b90df8d4'],report['variants']):
        manifest=read(root/(arm+'-manifest.json'));composition=v['composition']
        require(v['source']==source and composition['source']==source and composition['arm']==arm,'source/arm drift')
        require(digest(root/(arm+'-manifest.json'))==v['manifest_sha256'],'manifest hash drift')
        require(manifest['component_rebuild']==composition,'composition drift')
        require(manifest['build']==original['build'],'primary library build identity drift')
        require(composition['base_manifest_sha256']==digest(root/'original-manifest.json'),'base identity drift')
        require(composition['component']=='ggml-metal' and set(composition['changed_files'])==metal,'component scope drift')
        require(composition['retained_files']=={n:h for n,h in files.items() if n not in metal},'retained identity drift')
        actual=manifest['runtime']['files']
        require(set(actual)==set(files),'runtime inventory drift')
        require(all(actual[n]==files[n] for n in files if n not in metal),'non-Metal file changed')
        require(all(actual[n]==composition['changed_files'][n] and len(actual[n])==64 for n in metal),'component hashes drift')
        regression=read(root/(arm+'-regression.json'))
        require(digest(root/(arm+'-regression.json'))==v['regression_sha256'],'regression hash drift')
        require(regression['backend']=='MTL0' and regression['pass']==(arm=='fixed'),'regression verdict/backend')
        require(v['regression_exit']==(2 if arm=='baseline' else 0),'regression exit')
        require([c['width'] for c in regression['cases']]==[1,2,3,8,16,17,32],'widths')
        counts=[len(c['mismatches']) for c in regression['cases']]
        require(counts==([0,0,9,24,48,51,96] if arm=='baseline' else [0]*7),'exact mismatch count')
        for boundary in ['before','after']:
            require(read(root/(arm+'-'+boundary+'.json'))['native_thermal_pressure_level']==0,'thermal boundary')
        cache={}
        for line in (root/(arm+'-CMakeCache.txt')).read_text().splitlines():
            if '=' in line and ':' in line.split('=',1)[0]:
                key,value=line.split('=',1);cache[key.split(':',1)[0]]=value
        for key,value in {'GGML_NATIVE':'OFF','GGML_METAL':'ON','GGML_METAL_EMBED_LIBRARY':'ON','CMAKE_BUILD_TYPE':'Release','CMAKE_OSX_DEPLOYMENT_TARGET':'13.3'}.items():require(cache[key]==value,'CMake setting '+key)
        caches.append(cache)
        results.append({'arm':arm,'source':source,'manifest_sha256':v['manifest_sha256'],'mismatch_counts':counts,'non_metal_files_retained':len(files)-len(metal)})
    for key in ['CMAKE_CXX_COMPILER','CMAKE_C_COMPILER','CMAKE_OSX_SYSROOT']:
        require(caches[0].get(key)==caches[1].get(key),'compiler/SDK mismatch '+key)
    return {'pass':True,'arms':results,'compiler':report['compiler'],'sdk':report['sdk'],
            'scope':'Exact synthetic arithmetic regression and paired component provenance only. Rebuilt baseline operator/full-model calibration and candidate model qualification remain required.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--artifacts',type=Path,required=True);p.add_argument('--inputs',type=Path,required=True);a=p.parse_args()
    result=audit(a.artifacts,a.inputs);(a.artifacts/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
