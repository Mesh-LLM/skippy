#!/usr/bin/env python3
"""Compare the existing single-token GDN CUDA benchmark on both pinned backends."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from lib.ci_process import cancel_on_signal,stop
PINS={'main':'ed894d67104925b759dbf54fb203e40cc5d89eb8','upgrade':'73b6f910260b681e082f5ebc929777e4ab3d04f8'}
TREES={'main':'8ec4d1baddaa7f9639094379023a76dbaf47594b','upgrade':'abde3e9936ef3eb8e9c3146b5ea11d2cc202b07d'}
ORDER=['main','upgrade','upgrade','main','upgrade','main','main','upgrade']*2
PARAMS='head_count=32,head_size=128,n_seq_tokens=1,n_seqs=1,v_repeat=1,permuted=0,kda=0'

def call(args,cwd,env,log,timeout):
 with log.open('wb') as stream:
  child=subprocess.Popen(args,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
  begin=time.monotonic()
  try:
   while child.poll() is None:
    if time.monotonic()-begin>timeout:raise TimeoutError(log.name)
    time.sleep(1)
   if child.returncode:raise RuntimeError(f'{log.name} exited {child.returncode}')
  finally:
   try:stop(child)
   finally:
    captured=log.read_text(errors='replace')
    log.with_suffix('.json').write_text(json.dumps({'command':args,'cwd':str(cwd),'returncode':child.returncode,'log':captured},indent=2))
    if child.returncode:
     print(captured[-18000:],flush=True)
 return log.read_text(errors='replace')

def main():
 root=Path.cwd();out=Path(os.environ['JIANYANG_ARTIFACT_DIR']).resolve();out.mkdir(parents=True,exist_ok=True)
 sources=root/'target/gdn-sources';sources.mkdir(parents=True,exist_ok=True);builds={};records={}
 for tag,sha in PINS.items():
  path=sources/tag
  if subprocess.run(['git','cat-file','-e',sha+'^{commit}'],capture_output=True).returncode:
   subprocess.run(['git','fetch','--no-tags','origin',sha],check=True,timeout=120)
  subprocess.run(['git','worktree','add','--detach',str(path),sha],check=True)
  env=dict(os.environ,JIANYANG_COMMIT=sha,SCCACHE_DIR=str(Path.home()/'.cache/skippy/compiler'),SCCACHE_BASEDIRS=str(path))
  recipe=path/'crates/skippy-api/src/native-recipe.json'
  env['SCCACHE_EXTRAFILES']=os.pathsep.join(filter(None,[os.environ.get('SCCACHE_EXTRAFILES'),str(recipe)]))
  print(json.dumps({'phase':'build','pin':tag}),flush=True)
  call(['just','--justfile',str(root/'scripts/diagnose-gdn.just'),'--working-directory',str(path),'prepare-build'],path,env,out/('build-'+tag+'.log'),2400)
  tree=subprocess.check_output(['git','-C',str(path/'.deps/llama.cpp'),'rev-parse','HEAD^{tree}'],text=True).strip();assert tree==TREES[tag]
  caches=list((path/'.deps/llama-build').glob('*/CMakeCache.txt'));assert len(caches)==1
  binary=caches[0].parent/'bin/test-backend-ops';assert binary.is_file()
  records[tag]={'source':sha,'native_tree':tree,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'cmake_cache':caches[0].read_text()}
  builds[tag]=(path,binary,env)
  text=call([str(binary),'test','-b','CUDA0','-o','GATED_DELTA_NET','-p',PARAMS],path,env,out/('correctness-'+tag+'.log'),300)
  records[tag]['correctness_log']=text
  (out/'gdn-preflight.json').write_text(json.dumps(records,indent=2))
 rows=[]
 for serial,(phase,tag) in enumerate([('warm-only','main'),('warm-only','upgrade')]+[('measured',tag) for tag in ORDER]):
  path,binary,env=builds[tag];name=f'{serial:02d}-{phase}-{tag}'
  text=call([str(binary),'perf','-b','CUDA0','-o','GATED_DELTA_NET','-p',PARAMS,'--output','csv'],path,env,out/(name+'.log'),180)
  row={'phase':phase,'pin':tag,'source':PINS[tag],'stdout':text};rows.append(row)
  (out/'gdn-matched.json').write_text(json.dumps({'status':'running','records':records,'rows':rows,'filter':PARAMS},indent=2))
  print(json.dumps({'event':'gdn-result',**row}),flush=True)
 (out/'gdn-matched.json').write_text(json.dumps({'status':'passed','records':records,'rows':rows,'filter':PARAMS},indent=2))
if __name__=='__main__':
 with cancel_on_signal():main()
