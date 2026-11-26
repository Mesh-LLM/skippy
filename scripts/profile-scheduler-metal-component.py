#!/usr/bin/env python3
"""Build paired Metal components; retain all non-Metal runtime files exactly."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite

SOURCE='671c6ebbf7661b236ce44d352024c51741b08f9c'
ORIGINAL='6704435209b0fa8eabc3aa91e3c0a22f92b89863'
FIXED='e09c584b22b280a93903058129a76ac7b90df8d4'
MANIFEST='c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481'
PATCH='7b9ff3fd8f0f5e9dec6d2800e6f95e5de449e178d4e570fbedfa443b71d733f0'
RECIPE='scripts/profile-scheduler-metal-component.just'


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def output(args):return subprocess.check_output(args,text=True).strip()


def relocate(path,bundle):
    subprocess.run(['install_name_tool','-id','@rpath/'+path.name,str(path)],check=True)
    lines=output(['otool','-l',str(path)]).splitlines();rpaths=[]
    for i,line in enumerate(lines):
        if line.strip()=='cmd LC_RPATH':rpaths.append(lines[i+2].strip().split(' (offset',1)[0].removeprefix('path '))
    for value in rpaths:
        if value!='@loader_path':subprocess.run(['install_name_tool','-delete_rpath',value,str(path)],check=True)
    if '@loader_path' not in rpaths:subprocess.run(['install_name_tool','-add_rpath','@loader_path',str(path)],check=True)
    deps=[line.strip().split(' (compatibility',1)[0] for line in output(['otool','-L',str(path)]).splitlines()[1:]]
    for dep in deps:
        name=Path(dep).name
        if (bundle/'lib'/name).exists() and dep!='@rpath/'+name:
            subprocess.run(['install_name_tool','-change',dep,'@rpath/'+name,str(path)],check=True)
        elif not ((bundle/'lib'/name).exists() or dep.startswith(('/System/Library/','/usr/lib/'))):
            raise ValueError('unclosed library dependency: '+dep)
    subprocess.run(['codesign','--force','--sign','-',str(path)],check=True)


def compose(original,build,destination,source,arm,expected_manifest=MANIFEST):
    manifest=json.loads((original/'manifest.json').read_text());files=manifest['runtime']['files']
    if digest(original/'manifest.json')!=expected_manifest:raise ValueError('original manifest drift')
    for name,sha in files.items():
        p=original/name
        if not p.resolve().is_relative_to(original.resolve()) or digest(p)!=sha:raise ValueError('original file drift: '+name)
    shutil.copytree(original,destination,symlinks=True)
    candidates=list(build.rglob('libggml-metal.0.29.0.dylib'))
    if len(candidates)!=1:raise ValueError('ambiguous built Metal component')
    changed=[]
    for name in files:
        if name.startswith('lib/libggml-metal.'):
            p=destination/name;p.unlink();shutil.copyfile(candidates[0],p);relocate(p,destination);changed.append(name)
    if len(changed)!=3:raise ValueError('Metal library alias count')
    for name,sha in files.items():
        actual=digest(destination/name)
        if name not in changed and actual!=sha:raise ValueError('non-Metal runtime changed: '+name)
        if name in changed:manifest['runtime']['files'][name]=actual
    composition={'arm':arm,'component':'ggml-metal','source':source,'base_manifest_sha256':expected_manifest,
                 'retained_files':{n:h for n,h in files.items() if n not in changed},
                 'changed_files':{n:digest(destination/n) for n in changed},
                 'scope':'Explicit Metal-only component rebuild. Primary llama, CPU, speech and other original libraries retained byte-for-byte.'}
    manifest['component_rebuild']=composition
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return {'bundle':str(destination),'manifest_sha256':digest(destination/'manifest.json'),'composition':composition}


def main():
    artifact=Path(os.environ['JIANYANG_ARTIFACT_DIR']);artifact.mkdir(parents=True,exist_ok=True)
    if output(['git','rev-parse','HEAD'])!=SOURCE:raise ValueError('workspace source drift')
    cache=Path.home()/'.cache/skippy/kv-profile'/SOURCE;root=cache/f'q4-metal-components-{time.time_ns()}';root.mkdir()
    env=host.clean_env(root);env.update(GIT_ALLOW_PROTOCOL='file',GIT_NO_LAZY_FETCH='1',LLAMA_GIT_MAX_ATTEMPTS='1')
    report={'complete':False,'private_root':str(root),'scope':'Paired component rebuild, no model generation; all non-Metal runtime files preserved.','variants':[]}
    try:
        recovered=cache/'q4-scale-recovered-llama-upstream'
        if output(['git','-C',str(recovered),'rev-parse','HEAD'])!='8954f194837d2ec4d5f67f20c81b84abe63883ba':raise ValueError('recovered source pin')
        subprocess.run(['git','-C',str(recovered),'diff-index','--quiet','HEAD','--'],check=True,env=env)
        patch=Path('target/q4-scale.patch')
        if digest(patch)!=PATCH:raise ValueError('candidate patch drift')
        report['compiler']=output(['c++','--version']);report['sdk']=output(['xcrun','--show-sdk-version']);report['recipe_sha256']=digest(Path(RECIPE))
        original=cache/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal'
        queue_patch=Path('llama_cpp/patches/0046-ggml-metal-preserve-q4-k-scales-before-nibble-adjustment.patch')
        if queue_patch.exists():raise ValueError('unexpected existing candidate queue patch')
        report['patch_sha256']=digest(patch)
        report['builder_sha256']=digest(Path(__file__))
        shutil.copyfile(original/'manifest.json',artifact/'original-manifest.json')
        for arm,revision in [('baseline',ORIGINAL),('fixed',FIXED)]:
            source=root/('source-'+arm);shutil.copytree(recovered,source,symlinks=True)
            subprocess.run(['git','-C',str(source),'remote','add','origin',str(recovered)],check=True,env=env)
            if arm=='fixed':shutil.copyfile(patch,queue_patch)
            with (artifact/(arm+'-prepare.log')).open('w') as log:
                subprocess.run(['just','llama-prepare'],env={**env,'LLAMA_WORKDIR':str(source),'LLAMA_UPSTREAM_URL':str(recovered)},stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
            if output(['git','-C',str(source),'rev-parse','HEAD'])!=revision:raise ValueError('prepared native identity drift')
            build=root/('build-'+arm)
            with (artifact/(arm+'-build.log')).open('w') as log:
                subprocess.run(['just','--justfile',RECIPE,'build',str(source),str(build)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1200)
            composed=compose(original,build,root/('bundle-'+arm),revision,arm)
            shutil.copyfile(build/'CMakeCache.txt',artifact/(arm+'-CMakeCache.txt'))
            shutil.copyfile(Path(composed['bundle'])/'manifest.json',artifact/(arm+'-manifest.json'))
            executable=root/('regression-'+arm)
            with (artifact/(arm+'-regression-build.log')).open('w') as log:
                subprocess.run(['just','--justfile',RECIPE,'regression',str(source),composed['bundle'],str(executable)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
            suite.nominal(env,soak=30);host.telemetry(artifact/(arm+'-before.json'),env)
            with (artifact/(arm+'-regression.json')).open('w') as out,(artifact/(arm+'-regression.log')).open('w') as err:
                result=subprocess.run([str(executable)],env=env,stdout=out,stderr=err,timeout=120)
            host.telemetry(artifact/(arm+'-after.json'),env)
            data=json.loads((artifact/(arm+'-regression.json')).read_text());expected=2 if arm=='baseline' else 0
            if result.returncode!=expected or data['pass']!=(arm=='fixed'):raise ValueError('regression outcome unexpected: '+arm)
            if [c['width'] for c in data['cases']]!=[1,2,3,8,16,17,32]:raise ValueError('regression width drift')
            counts=[len(c['mismatches']) for c in data['cases']]
            if counts!=([0,0,9,24,48,51,96] if arm=='baseline' else [0]*7):raise ValueError('regression mismatch counts drift')
            if not data['backend'].startswith('MTL'):raise ValueError('non-Metal regression')
            composed.update(source=revision,regression_exit=result.returncode,regression_sha256=digest(artifact/(arm+'-regression.json')),binary_sha256=digest(executable))
            report['variants'].append(composed)
        report['complete']=True
    except Exception as error:
        report['error']={'type':type(error).__name__,'message':str(error)};raise
    finally:
        (artifact/'metal-components.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
