#!/usr/bin/env bash
# Invoked by Just in a fresh, source-pinned JY workspace.
set -euo pipefail
source_sha="$(git rev-parse HEAD)"
test "$source_sha" = "${PROFILE_SOURCE_SHA:?}"
output="${JIANYANG_ARTIFACT_DIR:?}"
cache="$HOME/.cache/skippy/kv-profile/$source_sha"
mkdir -p "$output" "$cache"
exec > >(tee "$output/build.log") 2>&1
export CMAKE_BUILD_PARALLEL_LEVEL=12 CARGO_BUILD_JOBS=12
export CARGO_PROFILE_RELEASE_DEBUG=1 CARGO_PROFILE_RELEASE_STRIP=none
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1
export LLAMA_STAGE_BACKEND=metal
python3 - "$cache" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
models = json.loads(Path('evals/kv-validation/models.json').read_text())
root = Path('/Users/Shared/jianyang/model-store/huggingface/hub')
for model in models:
    file = model['files'][0]
    path = root / ('models--' + model['repo'].replace('/', '--')) / 'snapshots' / model['revision'] / file['name']
    digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
    if digest != file['sha256']:
        raise ValueError(f'model hash mismatch: {model["key"]}')
    model.update(path=str(path), size_bytes=path.stat().st_size)
    print('VERIFIED', model['key'], digest, flush=True)
(Path(sys.argv[1]) / 'models.json').write_text(json.dumps(models, indent=2) + '\n')
PY
uvx --from inferencekit-cli==0.1.1 inferencekit doctor > "$cache/doctor.json"
cat "$cache/doctor.json"
just skippy-cli-release-build
scripts/package-native-runtime.sh --build --backend metal --no-model-package-tool --out "$cache/native-runtimes"
cp target/release/skippy "$cache/skippy"
pin="$(tr -d '[:space:]' < llama_cpp/upstream.txt)"
test ! -e "$cache/llama-source"
git clone --shared --no-checkout .deps/llama.cpp "$cache/llama-source"
git -C "$cache/llama-source" checkout --detach "$pin"
cmake -S "$cache/llama-source" -B "$cache/llama-build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DGGML_NATIVE=OFF -DGGML_CCACHE=OFF \
    -DCMAKE_C_FLAGS_RELEASE='-O3 -DNDEBUG -gline-tables-only' \
    -DCMAKE_CXX_FLAGS_RELEASE='-O3 -DNDEBUG -gline-tables-only' \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
    -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DMTMD_VIDEO=OFF
cmake --build "$cache/llama-build" --parallel 12 --target llama-server
cp "$cache/llama-build/bin/llama-server" "$cache/llama-server"
"$cache/skippy" --version
"$cache/llama-build/bin/llama-server" --version
"$cache/skippy" serve --help > "$cache/skippy-help.txt"
"$cache/llama-build/bin/llama-server" --help > "$cache/llama-help.txt" 2>&1
cp "$cache/models.json" "$cache/doctor.json" "$cache/skippy-help.txt" "$cache/llama-help.txt" "$output/"
python3 - "$cache" "$source_sha" "$pin" "$output" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
root, sha, pin, output = sys.argv[1:]
root = Path(root)
files = [root/'skippy', root/'llama-server', *root.glob('native-runtimes/*/manifest.json')]
record = {'source_sha': sha, 'upstream_sha': pin, 'cache': str(root),
          'files': {str(p.relative_to(root)): hashlib.file_digest(p.open('rb'), 'sha256').hexdigest() for p in files}}
for directory in (root, Path(output)):
    (directory/'build-identity.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record, indent=2), flush=True)
PY
