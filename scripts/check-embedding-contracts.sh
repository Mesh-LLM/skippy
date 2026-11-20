#!/usr/bin/env bash
set -euo pipefail

cargo fmt --all --check
cargo check --offline --locked -p skippy-api --features dynamic-native-runtime
cargo clippy --offline --locked -p skippy-api --features dynamic-native-runtime --all-targets -- -D warnings
cargo test --offline --locked -p skippy-api --features dynamic-native-runtime
