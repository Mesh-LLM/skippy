#!/usr/bin/env bash
set -euo pipefail

runtime_id="${1:?expected a Windows CUDA runtime ID}"
toolkit="${2:?expected the provisioned CUDA toolkit version}"

# Archive smoke builds the manifest's full CUDA architecture list.
arches="$(GITHUB_OUTPUT='' bash scripts/ci-plan.sh | jq -er \
    --arg runtime "$runtime_id" --arg toolkit "$toolkit" '
    .runtimes.include[]
    | select(.id == $runtime and .host == "windows-x86_64" and .backend == "cuda"
             and .validation == "artifact" and .toolkit == $toolkit)
    | .gpu_architectures | select(test("^[0-9]+(;[0-9]+)*$"))
')"
c_compiler="$(command -v "${CC:-cl.exe}")"
cxx_compiler="$(command -v "${CXX:-cl.exe}")"
nvcc_digest="$(bash scripts/artifact-file.sh sha256 "${CUDACXX:?expected the provisioned CUDA compiler}")"
cc_digest="$(bash scripts/artifact-file.sh sha256 "$c_compiler")"
cxx_digest="$(bash scripts/artifact-file.sh sha256 "$cxx_compiler")"

# Track the native build inputs by path and Git blob. A release-only Cargo
# version bump leaves them intact. sccache still keys each compiler invocation.
digest="$({
    printf '%s\n' "$runtime_id" "$toolkit" "$arches" \
        "${VCToolsVersion:?expected the MSVC toolset version}" \
        "${WindowsSDKVersion:?expected the Windows SDK version}" \
        "$nvcc_digest" "$cc_digest" "$cxx_digest"
    git ls-tree -r HEAD -- Justfile llama_cpp scripts/build-llama.sh scripts/build-windows.ps1 \
        scripts/ci-product.py scripts/ci-windows-cuda.py scripts/ci-windows-cuda-cache.sh \
        scripts/enable-msvc-dev-env.ps1 scripts/lib/cuda-toolkit.sh \
        scripts/package-native-runtime.sh scripts/prepare-llama.sh scripts/prepare-whisper.sh
} | bash scripts/artifact-file.sh sha256 -)"
printf 'skippy-native-windows-cuda-v1-%s\n' "$digest"
