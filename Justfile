import 'ci/images.just'

automation_python := env("SKIPPY_PYTHON", "python3")
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

[positional-arguments]
automation-run *args:
    @bash scripts/automation-run.sh "$@"

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

embedding-contract-check:
    @cargo fmt -p skippy-api -p skippy-inference-api -p skippy-serving --check
    @cargo check --locked --offline -p skippy-api -p skippy-inference-api -p skippy-serving --features skippy-api/dynamic-native-runtime
    @cargo clippy --locked --offline -p skippy-api -p skippy-inference-api -p skippy-serving --features skippy-api/dynamic-native-runtime --all-targets -- -D warnings
    @cargo test --locked --offline -p skippy-api -p skippy-inference-api --features skippy-api/dynamic-native-runtime
    @cargo test --locked --offline -p skippy-serving --features dynamic-native-runtime --lib embedded::tests
    @cargo test --locked --offline -p skippy-serving --features dynamic-native-runtime --lib readiness::tests
    @python3 scripts/embedding-consumer.py check

[positional-arguments]
embedding-serving-test bundle_root:
    @SKIPPY_NATIVE_RUNTIME_BUNDLE_DIR="$1" cargo test --locked --offline -p skippy-serving --features dynamic-native-runtime

[positional-arguments]
embedding-consumer-check *args:
    @python3 scripts/embedding-consumer.py check "$@"

[positional-arguments]
embedding-consumer-qualify *args:
    @python3 scripts/embedding-consumer.py qualify "$@"

lint: llama-prepare
    @cargo fmt --all --check
    @cargo clippy --workspace --all-targets -- -D warnings
    @cargo fmt --manifest-path skippy-api-rs/Cargo.toml --all --check
    @cargo clippy --manifest-path skippy-api-rs/Cargo.toml --workspace --all-targets -- -D warnings

test: skippy-api-test
    @cargo test --workspace --no-default-features
    @cargo test --manifest-path skippy-api-rs/Cargo.toml --workspace

llama-prepare:
    @scripts/prepare-llama.sh pinned

llama-build: llama-prepare
    @scripts/build-llama.sh

# Includes placement, released-page compaction, sampling-pool, and row-list
# operator regressions. Accelerator qualification selects its backend explicitly.
native-full-replay backend="cpu": llama-prepare
    @LLAMA_STAGE_BACKEND="{{ backend }}" LLAMA_STAGE_FULL_REPLAY=ON scripts/build-llama.sh

cache-reuse-smoke base_url model prompt_file output wave="4":
    @cargo run --locked -p skippy-correctness -- open-ai-cache-reuse --base-url "{{ base_url }}" --model "{{ model }}" --prompt-file "{{ prompt_file }}" --wave "{{ wave }}" > "{{ output }}"

kv-matrix-build native_lib_dir: llama-prepare
    #!/usr/bin/env bash
    set -euo pipefail
    source_dir="$(cd "{{ llama_dir }}" && pwd)"
    cmake -S evals/kv-validation/native -B target/kv-validation -G Ninja \
      -DCMAKE_BUILD_TYPE=Release -DNATIVE_SOURCE="$source_dir" \
      -DNATIVE_LIB_DIR="{{ native_lib_dir }}"
    cmake --build target/kv-validation --parallel

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
    just automation-run automation native-generator contracts api-doc --check

ci-plan:
    @bash scripts/ci-plan.sh

[positional-arguments]
inference-plan profile source output="target/ci-evidence/inference-plan.json":
    @"{{ automation_python }}" scripts/inference-plan.py plan "$@"

process-supervision-test:
    @cargo test --locked -p process-supervision

inference-contracts:
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_markdown.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_nightly.py

inference-publication-test:
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/inference_publication.py

automation-contracts: inference-contracts
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/automation_contracts.py
    @PYTHONPATH=scripts "{{ automation_python }}" scripts/tests/release_contracts.py

[positional-arguments]
inference-render-report format input *options:
    @"{{ automation_python }}" scripts/inference-render-report.py "$@"

[positional-arguments]
inference-dispatch profile source output="target/ci-evidence/inference" *configuration:
    @"{{ automation_python }}" scripts/inference-dispatch.py dispatch "$@"

[positional-arguments]
inference-execute profile model mode:
    @"{{ automation_python }}" scripts/inference-execute.py "$@"

[positional-arguments]
inference-cancel ledger="target/ci-evidence/inference/jobs.json":
    @"{{ automation_python }}" scripts/inference-dispatch.py cancel "$@"

inference-build:
    @"{{ automation_python }}" scripts/inference-build.py

inference-build-uncached:
    @command -v hf uv nvidia-smi
    @SCCACHE_BASEDIRS="$PWD" LLAMA_STAGE_BACKEND=cuda LLAMA_STAGE_CUDA_ARCHITECTURES=121 LLAMA_STAGE_LINK_MODE=static just llama-build
    @LLAMA_STAGE_BACKEND=cuda LLAMA_STAGE_CUDA_ARCHITECTURES=121 LLAMA_STAGE_LINK_MODE=static cargo build --locked -p skippy-cli -p skippy-correctness

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

[positional-arguments]
ci-host host:
    @"{{ automation_python }}" scripts/ci-product.py host "$@"

[positional-arguments]
ci-runtime runtime:
    @"{{ automation_python }}" scripts/ci-product.py runtime "$@"

[positional-arguments]
ci-package runtime host_dir runtime_dir:
    @"{{ automation_python }}" scripts/ci-product.py package "$@"

[positional-arguments]
ci-smoke runtime product_dir:
    @"{{ automation_python }}" scripts/ci-product.py smoke "$@"

ci-model-fetch:
    @bash scripts/ci-model-fetch.sh

[positional-arguments]
ci-artifact-smoke runtime product_dir:
    @"{{ automation_python }}" scripts/ci-product.py artifact-smoke "$@"

ci: lint test llama-check

[positional-arguments]
release version canary="false":
    @bash scripts/release-dispatch.sh "$@"

[positional-arguments]
release-version tag:
    @"{{ automation_python }}" scripts/ci-release.py bump "$@"

[positional-arguments]
release-candidate *args:
    @"{{ automation_python }}" scripts/ci-release-candidate.py "$@"

[positional-arguments]
check-release tag:
    @"{{ automation_python }}" scripts/ci-release.py validate "$@"

[positional-arguments]
release-prepare tag input_dir:
    @"{{ automation_python }}" scripts/ci-release.py prepare "$@"

[positional-arguments]
release-prepare-candidate tag input_dir:
    @"{{ automation_python }}" scripts/ci-release.py prepare-candidate "$@"

[positional-arguments]
release-verify directory="dist/release":
    @"{{ automation_python }}" scripts/ci-release.py verify "$@"

[positional-arguments]
release-select tag index:
    @"{{ automation_python }}" scripts/ci-release.py select "$@"

[positional-arguments]
release-upload-plan directory remote:
    @"{{ automation_python }}" scripts/ci-release.py upload-plan "$@"

inference-admit:
    @"{{ automation_python }}" scripts/inference-plan.py admit

[positional-arguments]
inference-matrix profile source *options:
    @"{{ automation_python }}" scripts/inference-plan.py matrix "$@"

[positional-arguments]
inference-publish-nightly evidence:
    @"{{ automation_python }}" scripts/inference-publish-nightly.py "$@"
