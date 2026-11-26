#!/usr/bin/env python3
"""Create a strict-schema serving view without changing native library bytes."""
import copy
import hashlib
import json
from pathlib import Path
import shutil


def manifest_bytes(manifest):
    view=copy.deepcopy(manifest)
    if 'component_rebuild' in view['build']:
        raise ValueError('ambiguous component provenance')
    view['build']['component_rebuild']=view.pop('component_rebuild')
    return (json.dumps(view,indent=2)+'\n').encode()


def materialize(source,destination,expected_sha):
    original=(source/'manifest.json').read_bytes()
    manifest=json.loads(original);data=manifest_bytes(manifest)
    if hashlib.sha256(data).hexdigest()!=expected_sha:
        raise ValueError('serving manifest differs from preregistration')
    shutil.copytree(source,destination)
    (destination/'manifest.json').write_bytes(data)
    (destination/'component-source-manifest.json').write_bytes(original)
    for name,sha in manifest['runtime']['files'].items():
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('invalid runtime file path')
        if hashlib.sha256((destination/path).read_bytes()).hexdigest()!=sha:
            raise ValueError('serving bundle file changed: '+name)
    return destination
