#!/usr/bin/env bash
set -euo pipefail

if ! command -v uvx >/dev/null 2>&1; then
  echo "inferencekit requires uvx (install uv first)" >&2
  exit 127
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export INFERENCEKIT_RUNS_DIR="${INFERENCEKIT_RUNS_DIR:-$repo_root/target/inferencekit}"

exec uvx --from 'inferencekit-cli==0.1.1' inferencekit "$@"
