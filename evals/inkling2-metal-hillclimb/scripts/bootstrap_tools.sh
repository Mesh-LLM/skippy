#!/usr/bin/env bash
# Make a lab macOS host able to run `just build backend="metal"` for Skippy.
# Installs only what is missing, through Homebrew, and installs the repository's
# pinned Rust toolchain (rust-toolchain.toml). Mirrors what CI does on macOS with
# scripts/ci-macos-rust.sh; it does not change the repository.
set -euo pipefail
export HOMEBREW_NO_AUTO_UPDATE=1
export HOMEBREW_NO_INSTALL_CLEANUP=1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v brew >/dev/null 2>&1; then
  echo "bootstrap: Homebrew is required but not installed" >&2
  exit 1
fi

need() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "bootstrap: $1 present ($(command -v "$1"))"
  else
    echo "bootstrap: installing $1"
    brew install "$1"
  fi
}

xcode-select -p >/dev/null 2>&1 || { echo "bootstrap: Xcode command line tools are required" >&2; exit 1; }
need just
need cmake
need ninja
if ! command -v rustup >/dev/null 2>&1; then
  echo "bootstrap: installing rustup"
  brew install rustup
  export PATH="$(brew --prefix rustup)/bin:$PATH"
fi
# With no channel argument rustup reads the repository's rust-toolchain.toml.
rustup toolchain install --no-self-update --no-update
export PATH="$(dirname "$(rustup which cargo)"):$PATH"
cargo --version
rustc --version
just --version
cmake --version | head -n 1
xcodebuild -version
