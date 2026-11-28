#!/usr/bin/env bash
# Prove HIP can use a real device before paying for a full runtime build.
set -euo pipefail

mkdir -p target/ci-evidence
exec > >(tee target/ci-evidence/rocm-preflight.log) 2>&1
test "$(uname -s)/$(uname -m)" = Linux/x86_64
test -c /dev/kfd
test -d /dev/dri
rocm="${ROCM_PATH:-/opt/rocm}"
expected=$(jq -er '.runtimes[] | select(.id == "linux-x86_64-rocm") | .toolkit' ci/targets.json)
installed=$(<"$rocm/.info/version")
test "${installed%%-*}" = "$expected"
source_sha=$(git rev-parse HEAD)
printf 'Runner: %s\nSource: %s\nROCm: %s\n' "${RUNNER_NAME:-local}" "$source_sha" "$installed"
hipcc --version

probe=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/skippy-rocm-probe.XXXXXX")
trap 'rm -rf "$probe"' EXIT
cat > "$probe/probe.cpp" <<'CPP'
#include <hip/hip_runtime.h>
#include <cstdio>

int main() {
    int count = 0;
    if (hipGetDeviceCount(&count) != hipSuccess || count < 1) {
        std::fprintf(stderr, "HIP did not discover an AMD GPU\n");
        return 1;
    }
    for (int device = 0; device < count; ++device) {
        hipDeviceProp_t properties{};
        if (hipGetDeviceProperties(&properties, device) != hipSuccess) return 1;
        std::printf("HIP device %d: %s (%s)\n", device, properties.name, properties.gcnArchName);
    }
    return 0;
}
CPP
hipcc --offload-arch=gfx1151 "$probe/probe.cpp" -o "$probe/probe"
"$probe/probe"
