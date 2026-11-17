llama_dir := env("SKIPPY_LLAMA_DIR", ".deps/llama.cpp")
macos_target := env("MACOSX_DEPLOYMENT_TARGET", "")
export MACOSX_DEPLOYMENT_TARGET := if macos_target == "" { trim(read(justfile_directory() / "scripts/lib/macos-deployment-target.txt")) } else { macos_target }

default: build

automation-run *args:
    @cargo xtool {{ args }}

[private]
[unix]
with-lld *command:
    #!/usr/bin/env bash
    set -euo pipefail
    scripts/cargo-linker --skippy-probe >/dev/null
    exec {{ command }}

[private]
[windows]
with-lld *command:
    @{{ command }}

[unix]
build backend="" cuda_arch="" rocm_arch="":
    @scripts/build-development-product.sh --backend "{{ backend }}" --cuda-arch "{{ cuda_arch }}" --rocm-arch "{{ rocm_arch }}"

[windows]
build backend="" cuda_arch="" rocm_arch="":
    @powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1 -Backend "{{ backend }}" -CudaArch "{{ cuda_arch }}" -RocmArch "{{ rocm_arch }}"

skippy: build

skippy-cli-build:
    @cargo build --locked -p skippy-cli --features dynamic-native-runtime

skippy-cli-release-build:
    @cargo build --release --locked -p skippy-cli --features dynamic-native-runtime

[unix]
release-runtime-build backend="" target="":
    #!/usr/bin/env bash
    set -euo pipefail
    selected="{{ backend }}"
    if [[ -z "$selected" ]]; then
      if [[ "$(uname -s)" == Darwin ]]; then selected=metal; else selected=cpu; fi
    fi
    args=(--build --backend "$selected")
    [[ -n "{{ target }}" ]] && args+=(--target "{{ target }}")
    scripts/package-native-runtime.sh "${args[@]}"

[windows]
release-runtime-build backend="cpu" target="x86_64-pc-windows-msvc":
    @powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1 -Backend "{{ backend }}" -Target "{{ target }}" -BuildProfile release -RuntimeOnly

release-build backend="" target="":
    @just release-runtime-build "{{ backend }}" "{{ target }}"
    @just skippy-cli-release-build

package-builder-release-build backend="cpu":
    @just release-runtime-build "{{ backend }}"
    @just with-lld cargo build --release --locked -p skippy-package-builder

snapshot-promoter-release-build:
    @just with-lld cargo build --release --locked -p skippy-model-package --bin promote-layer-package-snapshot

model-publisher-release-build:
    @just with-lld cargo build --release --locked -p skippy-model-package --bin model-package-publish

layer-job-helper-release-build:
    @just with-lld cargo build --release --locked -p skippy-model-package --bin model-package-layer-job

format:
    @cargo fmt --all
    @cargo fmt --manifest-path skippy-api-rs/Cargo.toml --all

lint: llama-prepare
    @cargo fmt --all --check
    @cargo clippy --workspace --all-targets -- -D warnings
    @cargo fmt --manifest-path skippy-api-rs/Cargo.toml --all --check
    @cargo clippy --manifest-path skippy-api-rs/Cargo.toml --workspace --all-targets -- -D warnings

test: skippy-llama-api-test
    @cargo test --workspace --no-default-features
    @cargo test --manifest-path skippy-api-rs/Cargo.toml --workspace

llama-prepare:
    @scripts/prepare-llama.sh pinned

llama-build: llama-prepare
    @scripts/build-llama.sh

skippy-ffi-bindgen-check: llama-prepare
    @SKIPPY_FFI_BINDGEN=check SKIPPY_FFI_BINDGEN_SELFTEST=1 cargo check -p skippy-ffi

skippy-llama-api-test: llama-prepare
    #!/usr/bin/env bash
    set -euo pipefail
    build_dir="${SKIPPY_LLAMA_API_BUILD_DIR:-target/skippy-llama-api-build}"
    cmake -S skippy_api -B "$build_dir" -G Ninja \
      -DCMAKE_BUILD_TYPE=Debug \
      -DSKIPPY_API_BUILD_TESTS=ON \
      -DSKIPPY_LINK_COMPILE_COMMANDS=ON \
      -DSKIPPY_LLAMA_SOURCE_DIR="{{ absolute_path(llama_dir) }}"
    cmake --build "$build_dir" --parallel
    ctest --test-dir "$build_dir" --output-on-failure --no-tests=error

llama-check: skippy-ffi-bindgen-check skippy-llama-api-test
    #!/usr/bin/env bash
    set -euo pipefail
    scripts/check-skippy-public-headers.sh
    backend=cpu
    if [[ "$(uname -s)" == Darwin ]]; then backend=metal; fi
    export LLAMA_STAGE_BACKEND="$backend"
    export LLAMA_STAGE_BUILD_TESTS=ON
    scripts/build-llama.sh --build-only
    build_dir="$(scripts/build-llama.sh --print-build-dir)"
    LLAMA_STAGE_BUILD_DIR="$build_dir" scripts/check-skippy-generated-family-patch.sh
    cargo xtool automation native-generator contracts api-doc --check

ci-plan:
    @cargo xtool ci plan

ci-contracts:
    @cargo xtool ci contracts

ci-host host:
    @cargo xtool ci host "{{ host }}"

ci-runtime runtime:
    @cargo xtool ci runtime "{{ runtime }}"

ci-package runtime host_dir runtime_dir:
    @cargo xtool ci package "{{ runtime }}" "{{ host_dir }}" "{{ runtime_dir }}"

ci-smoke runtime product_dir:
    @cargo xtool ci smoke "{{ runtime }}" "{{ product_dir }}"

ci-summary:
    @cargo xtool ci summary

ci: ci-contracts lint test llama-check ci-plan

check-release tag:
    @cargo xtool release validate "{{ tag }}"

release-prepare tag input_dir:
    @cargo xtool release prepare "{{ tag }}" "{{ input_dir }}"

release-verify directory="dist/release":
    @cargo xtool release verify "{{ directory }}"
