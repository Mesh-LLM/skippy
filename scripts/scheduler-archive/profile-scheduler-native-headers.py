#!/usr/bin/env python3
"""Seal exact pinned header changes for private, offline JY staging."""
import base64
import hashlib
import json
import lzma
from pathlib import Path
import runpy
import subprocess

SOURCE = '671c6ebbf7661b236ce44d352024c51741b08f9c'
BASE = '8954f194837d2ec4d5f67f20c81b84abe63883ba'
NATIVE = '6704435209b0fa8eabc3aa91e3c0a22f92b89863'
PATHS = ['include', 'ggml/include', 'vendor/nlohmann']


def git(*args):
    return subprocess.check_output(['git', '-C', '.deps/llama.cpp', *args])


def main():
    root = Path('target/scheduler-fixes/native-headers')
    root.mkdir(exist_ok=True)
    patch = git('diff', '--binary', BASE, NATIVE, '--', *PATHS)
    packed = lzma.compress(patch)
    sha = hashlib.sha256(packed).hexdigest()
    manifest = {'upstream': BASE, 'native': NATIVE, 'compressed_sha256': sha,
                'patch_sha256': hashlib.sha256(patch).hexdigest(), 'files': {}, 'chunks': []}
    for rev in (BASE, NATIVE):
        names = git('ls-tree', '-r', '--name-only', rev, '--', *PATHS).decode().splitlines()
        manifest['files'][rev] = {name: hashlib.sha256(git('show', rev + ':' + name)).hexdigest() for name in names}
    replace = runpy.run_path('scripts/profile-kv-spec.py')['replace']
    for index, start in enumerate(range(0, len(packed), 10000)):
        data = packed[start:start + 10000]
        encoded = base64.b85encode(data).decode()
        chunk_sha = hashlib.sha256(data).hexdigest()
        name = f'chunk-{index}.xzpart'
        manifest['chunks'].append({'name': name, 'sha256': chunk_sha})
        code = '\n'.join([
            'import base64,hashlib,json,os', 'from pathlib import Path',
            f'data=base64.b85decode({encoded!r})', f'assert hashlib.sha256(data).hexdigest()=={chunk_sha!r}',
            f'root=Path.home()/".cache/skippy/kv-profile/{SOURCE}/native-headers-{sha}"',
            'root.mkdir(parents=True,exist_ok=True)', f'p=root/{name!r}',
            'if p.exists():\n assert p.read_bytes()==data\nelse:\n p.write_bytes(data)',
            f'record={{"chunk":{name!r},"sha256":{chunk_sha!r},"bytes":len(data),"native":{NATIVE!r}}}',
            '(Path(os.environ["JIANYANG_ARTIFACT_DIR"])/"header-stage.json").write_text(json.dumps(record,indent=2))',
            'print(json.dumps(record))'])
        command = "set -eu\npython3 - <<'STAGE'\n" + code + '\nSTAGE\n'
        assert len(command.encode()) < 16384
        spec = replace(json.loads(Path('evals/kv-profiling/jy/measure.json.in').read_text()), {
            '@SOURCE_SHA@': SOURCE, '@MODEL_MANIFEST_SHA256@': chunk_sha,
            '@COMMAND@': command, '@PHASE@': f'native-header-stage-{index}', '@DURATION@': 120})
        spec['spec']['labels']['campaign'] = 'scheduler-fixes'
        spec['spec']['harness'].pop('request_count', None)
        spec['spec']['harness'].update(warmup_requests=0, duration_s=120, concurrency=[1])
        (root / f'stage-{index}.spec.json').write_text(json.dumps(spec, indent=2)+'\n')
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (root / 'headers.patch.xz').write_bytes(packed)
    print(json.dumps({'chunks': len(manifest['chunks']), 'compressed_sha256': sha, 'bytes': len(packed)}))


if __name__ == '__main__':
    main()
