#!/usr/bin/env bash
set -euo pipefail

# Copy the pinned Windows HIP SDK's component licenses and print manifest paths.
# An explicit combined-license file remains authoritative when supplied.
stage_dir="${1:?usage: rocm-distribution-licenses.sh STAGE_DIR SDK_ROOT [COMBINED_LICENSE]}"
root="${2:-}"
override="${3:-}"
sources=()

if [[ -n "$override" ]]; then
    if [[ ! -f "$override" ]]; then
        echo "configured ROCm license file does not exist: $override" >&2
        exit 1
    fi
    sources+=("$override")
else
    for component in rocblas hipblaslt hipblas-common hipblas; do
        source=""
        for candidate in LICENSE.md LICENSE.txt LICENSE; do
            if [[ -f "$root/share/doc/$component/$candidate" ]]; then
                source="$root/share/doc/$component/$candidate"
                break
            fi
        done
        if [[ -z "$root" || -z "$source" ]]; then
            echo "AMD HIP SDK license for $component was not found under ${root:-\$ROCM_PATH}/share/doc; set SKIPPY_ROCM_LICENSE_FILE" >&2
            exit 1
        fi
        sources+=("$source")
    done
fi

mkdir -p "$stage_dir/licenses"
for source in "${sources[@]}"; do
    component="$(basename "$(dirname "$source")")"
    name="AMD-HIP-SDK-$component-$(basename "$source")"
    cp "$source" "$stage_dir/licenses/$name"
    printf 'licenses/%s\n' "$name"
done
