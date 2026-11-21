#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
entry="$(jq -cer '
  .models[] | select(.family == "qwen3-dense")
  | .artifact as $artifact | $artifact.files[0] as $file
  | {repo: $artifact.repo, revision: $artifact.revision, file: $file,
     size: $artifact.file_integrity[$file].size_bytes,
     sha256: $artifact.file_integrity[$file].blob_id}
' "$root/ci/llama-canary/family-certified.json")"
expected_size="$(jq -er '.size' <<<"$entry")"
expected_sha="$(jq -er '.sha256' <<<"$entry")"

verify_model() {
  local file="$1" actual_size actual_sha
  actual_size="$(wc -c < "$file" | tr -d '[:space:]')"
  if command -v sha256sum >/dev/null 2>&1; then
    actual_sha="$(sha256sum "$file")"
  else
    actual_sha="$(shasum -a 256 "$file")"
  fi
  actual_sha="${actual_sha%% *}"
  if [[ "$actual_size" != "$expected_size" || "$actual_sha" != "$expected_sha" ]]; then
    echo 'Qwen3 fixture size or SHA-256 mismatch' >&2
    return 1
  fi
}

if [[ ${SKIPPY_CI_MODEL+x} ]]; then
  destination="$SKIPPY_CI_MODEL"
else
  repo="$(jq -er '.repo' <<<"$entry")"
  revision="$(jq -er '.revision | select(test("^[a-f0-9]{40}$"))' <<<"$entry")"
  file="$(jq -er '.file' <<<"$entry")"
  model_path="$(NO_COLOR=1 HF_HUB_DISABLE_XET=1 hf download "$repo" "$file" --revision "$revision" --quiet)"
  destination="$(awk '{sub(/^[[:space:]]+/, ""); sub(/^path[:=][[:space:]]*/, "")} /^\// {path=$0} END {print path}' <<<"$model_path")"
  if [[ "$destination" != /* || ! -f "$destination" ]]; then
    echo 'hf download returned no existing absolute Qwen3 fixture path' >&2
    exit 1
  fi
fi

verify_model "$destination"
if [[ -n "${GITHUB_ENV:-}" ]]; then
  absolute="$(realpath "$destination")"
  case "$absolute" in
    *$'\n'*|*$'\r'*) echo 'invalid model path for GITHUB_ENV' >&2; exit 1 ;;
  esac
  printf 'SKIPPY_CI_MODEL=%s\n' "$absolute" >> "$GITHUB_ENV"
fi
