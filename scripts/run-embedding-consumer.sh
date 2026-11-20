#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
mode="${1:?use contracts or qualify}"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT

source_repo="${2:-$root}"
revision="${3:-}"
case "$mode" in
  contracts)
    if [[ -z "$revision" ]]; then
      # Freeze the working tree into a local Git object without updating the index,
      # branches or HEAD. This permits testing a change before it is committed.
      export GIT_INDEX_FILE="$work/index"
      git -C "$root" read-tree HEAD
      git -C "$root" add -A -- Cargo.toml Cargo.lock crates tools/process-supervision \
        llama_cpp fixtures/production-embedding
      tree="$(git -C "$root" write-tree)"
      revision="$(git -C "$root" -c user.name=Skippy -c user.email=fixture@invalid \
        commit-tree "$tree" -p HEAD -m "Freeze embedding contract inputs")"
      unset GIT_INDEX_FILE
    fi
    ;;
  qualify)
    : "${2:?supply a Git repository or bare mirror containing the source revision}"
    : "${revision:?supply the full immutable Skippy commit}"
    : "${SKIPPY_FIXTURE_BUNDLE:?supply the verified dynamic bundle directory}"
    : "${SKIPPY_FIXTURE_MANIFEST_SHA256:?supply the trusted bundle manifest SHA-256}"
    : "${SKIPPY_FIXTURE_MODEL:?supply the local GGUF fixture}"
    : "${SKIPPY_FIXTURE_ENTRY:?supply the stage-zero return endpoint}"
    : "${SKIPPY_FIXTURE_WORKER:?supply the binary worker endpoint}"
    if [[ ! -f "$SKIPPY_FIXTURE_BUNDLE/manifest.json" || ! -f "$SKIPPY_FIXTURE_MODEL" ]]; then
      echo "qualification requires an existing bundle manifest and local model" >&2
      exit 2
    fi
    ;;
  *)
    echo "unknown embedding fixture mode: $mode" >&2
    exit 2
    ;;
esac
if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]] ||
   [[ "$(git -C "$source_repo" rev-parse --verify "$revision^{commit}")" != "$revision" ]]; then
    echo "the fixture requires a full immutable Skippy commit" >&2
    exit 2
fi

consumer="$work/consumer"
mkdir -p "$consumer/vendor/skippy"
git -C "$source_repo" archive "$revision" | tar -x -C "$consumer/vendor/skippy"
fixture="$consumer/vendor/skippy/fixtures/production-embedding"
cp "$fixture/Cargo.toml" "$consumer/"
cp -R "$fixture/src" "$consumer/"
cp "$consumer/vendor/skippy/Cargo.lock" "$consumer/Cargo.lock"

export SKIPPY_FIXTURE_REVISION="$revision"
export SKIPPY_LLAMA_AUTO_BUILD=0
export SKIPPY_FFI_BINDGEN=
export CARGO_TARGET_DIR="${SKIPPY_FIXTURE_TARGET_DIR:-$root/target/embedding-consumer}"

cd "$consumer"
cargo fmt --all --check
cargo check --offline --all-targets --features staged
cargo clippy --offline --locked --all-targets --features staged -- -D warnings
cargo test --offline --locked --features staged
if [[ "$mode" == "qualify" ]]; then
    cargo run --offline --locked --bin local
    cargo run --offline --locked --features staged --bin staged
fi
