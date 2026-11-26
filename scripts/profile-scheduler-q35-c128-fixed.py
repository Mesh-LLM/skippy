#!/usr/bin/env python3
"""Run the original four-arm c128 workload with the qualified Q4 Metal component."""
import json
from pathlib import Path
import runpy
import sys

import lib.profile_scheduler_suite as suite


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.cache/'q35-c128-fixed-roots';root.mkdir(exist_ok=True)
    provenance=json.loads(Path('q35-c128-fixed-inputs.json').read_text())
    digest=runpy.run_path('scripts/profile-scheduler-native-probe.py')['digest']
    for name,sha in provenance['files'].items():
        if digest(Path(name))!=sha:raise ValueError('sealed input changed: '+name)
    build=json.loads(Path('component-build.json').read_text());combined=json.loads(Path('combined-build.json').read_text())
    original_inputs=suite.inputs
    components=runpy.run_path('scripts/profile-scheduler-q4-retest.py')
    serving=runpy.run_path('scripts/profile-scheduler-serving-bundle.py')
    def fixed_inputs(cache,key,directory):
        identity,candidate,roots,model,replay=original_inputs(cache,key,directory)
        pin=json.loads(Path('ci/inference-artifacts.json').read_text())['qwen35-4b']
        if (key!='qwen3.5-4b-q4-k-m' or model['repo']!=pin['repo'] or model['revision']!=pin['revision']
                or len(model['files'])!=1 or model['files'][0]['sha256']!=pin['files'][0]['sha256']
                or Path(model['path']).name!=pin['files'][0]['path'] or Path(model['path']).stat().st_size!=pin['files'][0]['size']
                or digest(Path(model['path']))!=provenance['model_sha256']):
            raise ValueError('model identity changed')
        original=json.loads((cache/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json').read_text())
        if digest(cache/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json')!=provenance['original_manifest_sha256']:
            raise ValueError('original native runtime changed')
        variant=next(v for v in build['variants'] if v['composition']['arm']=='fixed')
        bundle=components['component'](cache,variant,original,digest)
        for arm,binary in [('skippy-base',cache/'skippy'),('skippy-patched',Path(combined['candidate_path'])/'skippy')]:
            expected=identity['files']['skippy'] if arm=='skippy-base' else combined['binary_sha256']
            if digest(binary)!=expected:raise ValueError('binary identity changed: '+arm)
            view=directory/('fixed-'+arm);view.mkdir();(view/'skippy').symlink_to(binary)
            dest=view/'native-runtimes/skippy-native-runtime-darwin-aarch64-metal'
            dest.parent.mkdir();serving['materialize'](bundle,dest,provenance['serving_manifest_sha256'])
            roots[arm]=view
        return identity,combined,roots,model,replay
    suite.inputs=fixed_inputs
    suite.PATCH=provenance['combined_patch_sha256']
    sys.argv=['profile-scheduler-suite.py','--cache',str(a.cache),'--output',str(a.output),
              '--model','qwen3.5-4b-q4-k-m','--phase','load','--concurrency','128',
              '--cooldown','120','--ref-e2e-ms','791.173594',
              '--round','0','--round','1','--round','2','--round','3']
    runpy.run_path('scripts/profile-scheduler-suite.py',run_name='__main__')


if __name__=='__main__':main()
