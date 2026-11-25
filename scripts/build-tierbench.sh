#!/usr/bin/env bash
# Build tools/tierbench and its plugins into target/tierbench for one backend.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND=""
CUDA_ARCH=""
ROCM_ARCH=""

usage() {
    echo "usage: scripts/build-tierbench.sh [--backend metal|cpu|cuda|rocm] [--cuda-arch LIST] [--rocm-arch LIST]" >&2
}

while (($# > 0)); do
    case "$1" in
        --backend) BACKEND="${2:-}"; shift 2 ;;
        --cuda-arch) CUDA_ARCH="${2:-}"; shift 2 ;;
        --rocm-arch) ROCM_ARCH="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done

if [[ -z "$BACKEND" ]]; then
    case "$(uname -s)" in
        Darwin) BACKEND=metal ;;
        *) BACKEND=cpu ;;
    esac
fi

flags=(-DCMAKE_BUILD_TYPE=Release)
case "$BACKEND" in
    metal|cpu) ;;
    cuda)
        flags+=(-DTIERBENCH_CUDA=ON)
        if [[ -n "$CUDA_ARCH" ]]; then flags+=("-DCMAKE_CUDA_ARCHITECTURES=$CUDA_ARCH"); fi
        ;;
    rocm)
        if [[ -z "$ROCM_ARCH" ]]; then echo "rocm needs --rocm-arch, the gfx target rocminfo reports" >&2; exit 1; fi
        flags+=(-DTIERBENCH_HIP=ON "-DCMAKE_HIP_ARCHITECTURES=$ROCM_ARCH")
        ;;
    *) usage; exit 1 ;;
esac

cmake -S "$REPO_ROOT/tools/tierbench" -B "$REPO_ROOT/target/tierbench" "${flags[@]}"
cmake --build "$REPO_ROOT/target/tierbench" -j
echo "Built tierbench: $REPO_ROOT/target/tierbench/tierbench (plugins in target/tierbench/plugins)"
