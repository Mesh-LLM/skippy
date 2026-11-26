#!/usr/bin/env python3
"""Read-only inventory of admitted build sources; no model scan or downloads."""
import json
import os
from pathlib import Path
import subprocess


def git(path,*args):
    p=subprocess.run(['git','-C',str(path),*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=20)
    return p.stdout.strip() if p.returncode==0 else None


def main():
    cache=Path.home()/'.cache/skippy/kv-profile/671c6ebbf7661b236ce44d352024c51741b08f9c'
    candidates={cache/'llama-source'}
    for root in (Path.home()/'.cache/skippy',Path('/Users/Shared/jianyang')):
        if not root.exists():continue
        for current,dirs,_ in os.walk(root):
            path=Path(current);depth=len(path.relative_to(root).parts)
            if '.git' in dirs:candidates.add(path)
            dirs[:]=[d for d in dirs if d not in ('.git','model-store','huggingface','models','target','node_modules','artifacts','logs') and depth<4]
    rows=[]
    for path in sorted(candidates):
        if not path.is_dir():continue
        row={'path':str(path),'head':git(path,'rev-parse','HEAD'),'top':git(path,'rev-parse','--show-toplevel')}
        row['upstream_commit_present']=git(path,'cat-file','-t','8954f194837d2ec4d5f67f20c81b84abe63883ba')=='commit'
        row['native_commit_present']=git(path,'cat-file','-t','6704435209b0fa8eabc3aa91e3c0a22f92b89863')=='commit'
        row['llama_source_present']=(path/'src/llama.cpp').exists()
        row['whisper_source_present']=(path/'src/whisper.cpp').exists()
        rows.append(row)
    out=Path(os.environ['JIANYANG_ARTIFACT_DIR']);out.mkdir(exist_ok=True,parents=True)
    (out/'source-inventory.json').write_text(json.dumps({'sources':rows,'scope':'Read-only directories and Git object availability; not source integrity verification.'},indent=2)+'\n')


if __name__=='__main__':main()
