#!/usr/bin/env bash
set -euo pipefail
patch_file="$1"
expected_source="$2"
expected_patch="$3"
artifact_dir="${JIANYANG_ARTIFACT_DIR:?}"
source_sha="$(git rev-parse HEAD)"
test "$source_sha" = "$expected_source"
test "$(shasum -a 256 "$patch_file" | cut -d ' ' -f 1)" = "$expected_patch"
test -z "$(git status --porcelain --untracked-files=no)"
git apply --check "$patch_file"
git apply "$patch_file"
git diff --check
candidate="$HOME/.cache/skippy/kv-profile/$source_sha/scheduler-fix-$expected_patch"
mkdir -p "$candidate"
export CARGO_BUILD_JOBS=12 CARGO_PROFILE_RELEASE_DEBUG=1 CARGO_PROFILE_RELEASE_STRIP=none
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1
export CARGO_NET_OFFLINE=true
just skippy-cli-release-build
pending="$(mktemp "$candidate/skippy.XXXXXX")"
trap 'rm -f "$pending"' EXIT
cp target/release/skippy "$pending"
chmod 755 "$pending"
"$pending" --version
mv "$pending" "$candidate/skippy"
python3 - "$candidate" "$source_sha" "$expected_patch" "$artifact_dir" <<'PY'
import hashlib,json,sys
from pathlib import Path
candidate,source,patch,artifact = sys.argv[1:]
path = Path(candidate)
with (path / 'skippy').open('rb') as stream:
    sha = hashlib.file_digest(stream, 'sha256').hexdigest()
evidence = {'source': source, 'patch_sha256': patch, 'candidate_path': candidate, 'binary_sha256': sha}
for directory in (path, Path(artifact)):
    (directory / 'scheduler-fix-build.json').write_text(json.dumps(evidence, indent=2) + '\n')
print(json.dumps(evidence), flush=True)
PY
