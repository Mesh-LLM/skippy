#!/usr/bin/env python3
"""Seal a verified scheduler fix patch for the existing offline Just builder."""
import argparse
import base64
import hashlib
import json
import lzma
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    source = '671c6ebbf7661b236ce44d352024c51741b08f9c'
    patch = args.patch.read_text()
    sha = hashlib.sha256(patch.encode()).hexdigest()
    files = {'target/scheduler-fix.patch': patch,
             'scripts/profile-scheduler-prefill-build.sh': Path('scripts/profile-scheduler-prefill-build.sh').read_text()}
    packed = base64.b85encode(lzma.compress(json.dumps(files).encode())).decode()
    command = ("set -eu\ncd skippy\npython3 - <<'INSTALL'\nimport base64,json,lzma\nfrom pathlib import Path\n"
               "for name,data in json.loads(lzma.decompress(base64.b85decode(" + repr(packed) + "))).items():\n"
               " p=Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)\nINSTALL\n"
               'env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
               'JIANYANG_ARTIFACT_DIR="$JIANYANG_ARTIFACT_DIR" '
               'bash scripts/profile-scheduler-prefill-build.sh target/scheduler-fix.patch '
               + source + ' ' + sha + '\n')
    if len(command.encode()) > 16384:
        raise ValueError('sealed build command exceeds limit')
    spec = json.loads(Path('target/scheduler-fixes/prefill-admission-build.spec.json').read_text())
    spec['spec']['name'] = 'Cache-off logprob prefill alignment fix build'
    spec['spec']['runtime']['command'] = command
    spec['spec']['runtime']['model_package']['sha256'] = sha
    spec['spec']['labels'].update(phase='logprob-prefill-build', patch=sha)
    args.out.write_text(json.dumps(spec, indent=2)+'\n')
    args.out.with_suffix('.inputs.json').write_text(json.dumps({'source': source, 'patch': sha,
        'files': {k: hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()}}, indent=2)+'\n')
    print(args.out, sha, len(command.encode()))


if __name__ == '__main__':
    main()
