#!/usr/bin/env python3
"""Recover exact Git source identities from existing indexed working files, offline."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess


def run(path,args,data=None):
    return subprocess.check_output(['git','-C',str(path),*args],input=data,stderr=subprocess.PIPE,
        env={**os.environ,'GIT_ALLOW_PROTOCOL':'file','GIT_NO_LAZY_FETCH':'1'},timeout=120)


def recover(source,destination,record):
    body=record['commit'].encode();commit=hashlib.sha1(b'commit '+str(len(body)).encode()+b'\0'+body).hexdigest()
    if commit!=record['revision']:raise ValueError('commit identity mismatch')
    tree=body.split(b'\n',1)[0].decode()[5:]
    if len(tree)!=40:raise ValueError('tree identity missing')
    if destination.exists():raise ValueError('destination must be new')
    entries=run(source,['ls-files','--stage','-z']).split(b'\0');validated=[]
    # Validate all supplied bytes before creating a destination; never read link targets.
    for entry in entries:
        if not entry:continue
        info,name=entry.split(b'\t',1);mode,blob,stage=info.split();rel=PurePosixPath(os.fsdecode(name))
        if stage!=b'0' or rel.is_absolute() or any(x in ('.','..','.git') for x in rel.parts):raise ValueError('unsafe index entry')
        path=source/rel
        if source.resolve() not in (path.parent.resolve(),*path.parent.resolve().parents):raise ValueError('source parent escapes root')
        if mode==b'120000':
            if not path.is_symlink():raise ValueError('link mode drift')
            data=os.fsencode(os.readlink(path))
        elif mode in (b'100644',b'100755'):
            if path.is_symlink() or not path.is_file():raise ValueError('file mode drift')
            data=path.read_bytes()
        else:raise ValueError('unsupported index mode')
        actual=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest().encode()
        if actual!=blob:raise ValueError('working file hash mismatch: '+str(rel))
        validated.append((rel,mode,data))
    if not validated:raise ValueError('empty source index')
    destination.mkdir(parents=True);run(destination,['init','--quiet'])
    index=[]
    for rel,mode,data in validated:
        blob=run(destination,['hash-object','-w','--stdin'],data).strip()
        index.append(mode+b' '+blob+b'\t'+os.fsencode(str(rel))+b'\0')
    # Git validates index paths. No working files are materialized before the tree matches.
    run(destination,['update-index','-z','--index-info'],b''.join(index))
    if run(destination,['write-tree']).decode().strip()!=tree:raise ValueError('recovered tree differs from pinned commit')
    if run(destination,['hash-object','-t','commit','-w','--stdin'],body).decode().strip()!=commit:raise ValueError('commit reconstruction')
    run(destination,['checkout','--quiet','--force','--detach',commit])
    run(destination,['diff-index','--quiet','HEAD','--'])
    return {'revision':commit,'tree':tree,'files':len(validated),'source':str(source),'destination':str(destination),
            'scope':'Exact indexed bytes, modes and symlinks reconstructed offline; parent history not imported.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);p.add_argument('--record',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    a.out.write_text(json.dumps(recover(a.source,a.destination,json.loads(a.record.read_text())),indent=2)+'\n')
