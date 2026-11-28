#!/usr/bin/env bash
# Supply the BLAS development closure absent from AMD's ROCm compiler image.
set -euo pipefail

version=7.2.4
rocm="${ROCM_PATH:-/opt/rocm}"
installed="$(<"$rocm/.info/version")"
if [[ "${installed%%-*}" != "$version" || "$(uname -m)" != x86_64 ]]; then
  echo "ROCm BLAS overlay requires the Linux x64 ROCm $version compiler image" >&2
  exit 1
fi

overlay="$(mktemp -d "${RUNNER_TEMP:?}/skippy-rocm-blas.XXXXXX")"
archive="$(mktemp "${RUNNER_TEMP}/skippy-rocm-deb.XXXXXX")"
trap 'rm -f "$archive"' EXIT
base="https://repo.radeon.com/rocm/apt/$version/pool/main"
tar_args=(-x -C "$overlay")
case "${SKIPPY_CI_ROCM_DEVICE_SMOKE:-0}" in
  0) tar_args+=(--exclude="./opt/rocm-$version/lib/rocblas/library"
                --exclude="./opt/rocm-$version/lib/hipblaslt/library") ;;
  1) ;; # Real inference needs the device kernels as well as linkable libraries.
  *) echo 'SKIPPY_CI_ROCM_DEVICE_SMOKE must be 0 or 1' >&2; exit 2 ;;
esac
while read -r checksum package; do
  echo "Provisioning ${package##*/}"
  curl --fail --location --silent --show-error --retry 3 --output "$archive" "$base/$package"
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
  # Archive-only jobs need no kernels; device rehearsals retain the full closure.
  dpkg-deb --fsys-tarfile "$archive" | tar "${tar_args[@]}"
done <<'PACKAGES'
e423d5ac2309547cc7f9817243c4be0b0b28f6d386faa8e678c336529809a582 h/hipblas/hipblas_3.2.0.70204-93~24.04_amd64.deb
35c5c313e70a71240f207d253a682616dd0c8a6fe5413f1330f309ca755b6e35 h/hipblas-common-dev/hipblas-common-dev_1.4.0.70204-93~24.04_amd64.deb
4dee2a558a63581927fbdb94ed39dd989f06c9e5bfb95c3ba857e29abd3afe90 h/hipblas-dev/hipblas-dev_3.2.0.70204-93~24.04_amd64.deb
b080afa232f83b54033fa2214c7ead64c5dfc956537818c32366c72f99d735fc h/hipblaslt/hipblaslt_1.2.2.70204-93~24.04_amd64.deb
5c776866a56528e633b6d80611f0a34c3732bb821a1144dab5184fdaa895422a h/hipblaslt-dev/hipblaslt-dev_1.2.2.70204-93~24.04_amd64.deb
7e935af7eedd834da9f227ac1a3f2086d1190e6f1f697ab76a38d8e707f0d95b r/rocblas/rocblas_5.2.0.70204-93~24.04_amd64.deb
fa1ce48b06290f3502c2dd3206edfc9790de16635872413ec2b04504cc7d0a23 r/rocblas-dev/rocblas-dev_5.2.0.70204-93~24.04_amd64.deb
c252a32c7b7b613696ed0466b0efeb42bb0419fca9eee095785f4926837ad662 r/rocsolver/rocsolver_3.32.0.70204-93~24.04_amd64.deb
PACKAGES

prefix="$overlay/opt/rocm-$version"
test -f "$prefix/lib/cmake/hipblas/hipblas-config.cmake"
test -f "$prefix/lib/cmake/rocblas/rocblas-config.cmake"
{
  printf 'CMAKE_PREFIX_PATH=%s%s\n' "$prefix" "${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
  printf 'LD_LIBRARY_PATH=%s:%s\n' "$prefix/lib" "${LD_LIBRARY_PATH:-$rocm/lib}"
} >> "${GITHUB_ENV:?}"
