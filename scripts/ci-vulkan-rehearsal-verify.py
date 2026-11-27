#!/usr/bin/env python3
"""Verify the production Vulkan build and dual-vendor smoke artifact contracts."""
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from lib import ci_artifacts as artifacts
from lib.ci_targets import targets, validate_native_identity

results=json.loads(os.environ['REHEARSAL_RESULTS'])
if not results or any(row['result'] != 'success' for row in results.values()):
    raise ValueError(f'Rehearsal jobs did not all succeed: {results}')
spec=importlib.util.spec_from_file_location('ci_release',Path('scripts/ci-release.py'))
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
hosts,runtimes=targets()
source=artifacts.source()
input_dir=Path('rehearsal-artifacts')
receipts=[]
for variant in ('linux-x86_64-vulkan','windows-x86_64-vulkan'):
    runtime=runtimes[variant]
    host=hosts[runtime['host']]
    product=input_dir/f'release-product-{variant}'
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        artifacts.unpack(product,root)
        artifacts.verify(root,'product',host['target'],variant)
        validate_native_identity(root/'native-runtimes'/variant,host,runtime)
    digest=artifacts.digest(product/f'skippy-{variant}.tar.gz')
    if runtime['validation']=='inference':
        for vendor in ('nvidia','amd'):
            release.verify_smoke(input_dir,variant,source,'vulkan',digest,vendor=vendor)
    else:
        release.verify_smoke(input_dir,variant,source,'vulkan',digest,artifact_only=True)
    receipts.append({'variant':variant,'sha256':digest,'validation':runtime['validation']})
artifacts.write_json(Path('rehearsal.json'),{'source':source,'run_id':os.environ['GITHUB_RUN_ID'],
    'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'products':receipts,'result':'passed'})
print('Vulkan rehearsal passed: both packages and both real GPU vendors match the exact source/archive identities')
