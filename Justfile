llama_dir := env("SKIPPY_LLAMA_DIR", ".deps/llama.cpp")
macos_target := env("MACOSX_DEPLOYMENT_TARGET", "")
export MACOSX_DEPLOYMENT_TARGET := if macos_target == "" { trim(read(justfile_directory() / "scripts/lib/macos-deployment-target.txt")) } else { macos_target }

default: build

docs-install:
    @npm ci --prefix docs-site

docs-dev:
    @npm --prefix docs-site run dev

docs-build:
    @npm --prefix docs-site run build

docs-deploy:
    @npm --prefix docs-site run deploy

docs-publish:
    @npm --prefix docs-site run deploy:prebuilt

docs-publish-beta:
    @npm --prefix docs-site run deploy:prebuilt:beta

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

test: skippy-api-test
    @cargo test --workspace --no-default-features
    @cargo test --manifest-path skippy-api-rs/Cargo.toml --workspace

cache-correctness-metal:
    @SKIPPY_CACHE_QUAL_BACKEND=metal LLAMA_STAGE_BACKEND=metal just with-lld cargo test --locked -p skippy-serving --lib frontend::tests::cache_qualification::cross_tier -- --ignored --exact --nocapture

[unix]
cache-correctness-cuda:
    @SKIPPY_CACHE_QUAL_BACKEND="${SKIPPY_CACHE_QUAL_BACKEND:-cuda}" LLAMA_STAGE_BACKEND=cuda just with-lld cargo test --locked -p skippy-serving --lib frontend::tests::cache_qualification::cross_tier -- --ignored --exact --nocapture

cache-correctness-matrix backend output *options:
    @cargo xtool cache-qualification "{{ backend }}" "{{ output }}" {{ options }}

[unix]
cache-correctness-verify backend report:
    @SKIPPY_CACHE_QUAL_BACKEND={{ quote(backend) }} SKIPPY_CACHE_QUAL_VERIFY_REPORT={{ quote(report) }} just with-lld cargo test --locked -p skippy-serving --features dynamic-native-runtime --lib frontend::tests::cache_qualification::verify_report -- --ignored --exact --nocapture

[unix]
cache-correctness-verify-matrix backend source report_dir:
    @cargo xtool cache-qualification verify {{ quote(backend) }} {{ quote(source) }} {{ quote(report_dir) }}

cache-parity-plan-identity:
    @cargo xtool cache-parity plan-identity

cache-parity-verify plan evidence raw_dir:
    @cargo xtool cache-parity verify {{ quote(plan) }} {{ quote(evidence) }} {{ quote(raw_dir) }}

cache-parity-verify-diagnostic plan evidence raw_dir:
    @cargo xtool cache-parity verify-diagnostic {{ quote(plan) }} {{ quote(evidence) }} {{ quote(raw_dir) }}

cache-parity-verify-tier-a-inputs plan evidence raw_dir metal_correctness cuda_correctness output:
    @cargo xtool cache-parity verify-tier-a-inputs {{ quote(plan) }} {{ quote(evidence) }} {{ quote(raw_dir) }} {{ quote(metal_correctness) }} {{ quote(cuda_correctness) }} {{ quote(output) }}

cache-parity-summarize-trial raw:
    @cargo xtool cache-parity summarize-trial "{{ raw }}"

cache-parity-capture-request address request output:
    @cargo xtool cache-parity capture-request "{{ address }}" "{{ request }}" "{{ output }}"

cache-parity-capture-pairs config request output:
    @cargo xtool cache-parity capture-pairs "{{ config }}" "{{ request }}" "{{ output }}"

cache-parity-capture-trace config workload output:
    @cargo xtool cache-parity capture-trace {{ quote(config) }} {{ quote(workload) }} {{ quote(output) }}

cache-parity-make-workload model state shape width output:
    @cargo xtool cache-parity make-workload {{ quote(model) }} {{ quote(state) }} {{ quote(shape) }} {{ quote(width) }} {{ quote(output) }}

cache-parity-export-trace plan cell capture raw_dir:
    @cargo xtool cache-parity export-trace {{ quote(plan) }} {{ quote(cell) }} {{ quote(capture) }} {{ quote(raw_dir) }}

[macos]
cache-parity-build-metal-probe output:
    @xcrun clang -Wall -Wextra -Werror -fobjc-arc -dynamiclib -framework Metal -framework Foundation tools/xtask/native/metal_memory_probe.m -o {{ quote(output) }}

[unix]
cache-correctness-native-dir:
    @scripts/build-llama.sh --print-build-dir

[unix]
cache-correctness-reference source build pin *options:
    @cmake -S "{{ source }}" -B "{{ build }}" -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_COMMIT="{{ pin }}" -DLLAMA_BUILD_COMMON=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_APP=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF {{ options }}
    @cmake --build "{{ build }}" --target llama-server

llama-prepare:
    @scripts/prepare-llama.sh pinned

llama-build: llama-prepare
    @scripts/build-llama.sh

skippy-ffi-bindgen-check: llama-prepare
    @SKIPPY_FFI_BINDGEN=check SKIPPY_FFI_BINDGEN_SELFTEST=1 cargo check -p skippy-ffi

skippy-ffi-bindgen: llama-prepare
    @SKIPPY_FFI_BINDGEN=regen cargo check --locked -p skippy-ffi

skippy-api-test:
    #!/usr/bin/env bash
    set -euo pipefail
    build_dir="${SKIPPY_API_BUILD_DIR:-${SKIPPY_LLAMA_API_BUILD_DIR:-target/skippy-api-build}}"
    cmake -S skippy_api -B "$build_dir" -G Ninja \
      -DCMAKE_BUILD_TYPE=Debug \
      -DSKIPPY_BUILD_LLAMA=OFF \
      -DSKIPPY_API_BUILD_TESTS=ON \
      -DSKIPPY_LINK_COMPILE_COMMANDS=ON
    cmake --build "$build_dir" --parallel
    ctest --test-dir "$build_dir" --output-on-failure --no-tests=error --stop-on-failure

llama-check: skippy-ffi-bindgen-check
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

inference-plan profile source output="target/ci-evidence/inference-plan.json":
    @cargo xtool inference plan "{{ profile }}" "{{ source }}" "{{ output }}"

inference-contracts:
    @cargo test --locked -p xtask inference::

inference-dispatch profile source output="target/ci-evidence/inference":
    @cargo xtool inference dispatch "{{ profile }}" "{{ source }}" "{{ output }}"

inference-execute profile model mode:
    @cargo xtool inference execute "{{ profile }}" "{{ model }}" "{{ mode }}"

inference-cancel ledger="target/ci-evidence/inference/jobs.json":
    @cargo xtool inference cancel "{{ ledger }}"

inference-build:
    @cargo xtool inference build

inference-build-uncached:
    @command -v hf uv nvidia-smi
    @SCCACHE_BASEDIRS="$PWD" LLAMA_STAGE_BACKEND=cuda LLAMA_STAGE_CUDA_ARCHITECTURES=121 LLAMA_STAGE_LINK_MODE=static just llama-build
    @LLAMA_STAGE_BACKEND=cuda LLAMA_STAGE_CUDA_ARCHITECTURES=121 LLAMA_STAGE_LINK_MODE=static cargo build --locked -p skippy-cli -p skippy-correctness

inference-tool-check:
    @cargo check --locked -p xtask
    @cargo clippy --locked -p xtask --all-targets -- -D warnings
    @cargo test --locked -p xtask

inference-format:
    @rustfmt --edition 2024 tools/xtask/src/inference.rs tools/xtask/src/inference/tests.rs

# Linux diagnostic: retain all MiMo2 graph assertions and report peak memory.
native-contract-build: llama-prepare
    @LLAMA_STAGE_BACKEND=cpu LLAMA_STAGE_LINK_MODE=static LLAMA_STAGE_BUILD_TESTS=ON scripts/build-llama.sh --build-only

native-mimo2-contract:
    #!/usr/bin/env bash
    set -euo pipefail
    export LLAMA_STAGE_BACKEND=cpu LLAMA_STAGE_LINK_MODE=static LLAMA_STAGE_BUILD_TESTS=ON
    build_dir="$(scripts/build-llama.sh --print-build-dir)"
    ctest --test-dir "$build_dir" -R '^skippy_generate_contract_mimo2$' --output-on-failure --no-tests=error
    /usr/bin/time -v ctest --test-dir "$build_dir" -R '^skippy_graph_contract_mimo2$' --output-on-failure --no-tests=error

ci-contracts:
    @cargo xtool ci contracts

ci-summary:
    @cargo xtool ci summary

ci-host host:
    @cargo xtool ci host "{{ host }}"

ci-runtime runtime:
    @cargo xtool ci runtime "{{ runtime }}"

ci-package runtime host_dir runtime_dir:
    @cargo xtool ci package "{{ runtime }}" "{{ host_dir }}" "{{ runtime_dir }}"

ci-smoke runtime product_dir:
    @cargo xtool ci smoke "{{ runtime }}" "{{ product_dir }}"

ci: lint ci-contracts test llama-check

check-release tag:
    @cargo xtool release validate "{{ tag }}"

release-prepare tag input_dir:
    @cargo xtool release prepare "{{ tag }}" "{{ input_dir }}"

release-verify directory="dist/release":
    @cargo xtool release verify "{{ directory }}"
