#!/usr/bin/env bash
set -euo pipefail

cargo_bin="${CARGO_HOME:-$HOME/.cargo}/bin"
export PATH="$cargo_bin:$PATH"
if ! rustup_path="$(command -v rustup)"; then
  brew install rustup
  rustup_path="$(brew --prefix rustup)/bin/rustup"
fi

# With no channel argument, rustup uses the repository's rust-toolchain.toml.
# Export the toolchain's bin directory even if Homebrew also supplies Cargo.
"$rustup_path" toolchain install --no-self-update --no-update
rust_bin="$(dirname "$("$rustup_path" which cargo)")"
"$rust_bin/cargo" --version
"$rust_bin/rustc" --version
printf '%s\n' "$rust_bin" >> "$GITHUB_PATH"
