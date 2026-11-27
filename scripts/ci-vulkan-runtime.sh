#!/usr/bin/env bash
# Ephemeral runner dependencies only; never replace the host GPU driver.
set -euo pipefail
mkdir -p target/ci-evidence
exec > >(tee target/ci-evidence/vulkan-runtime.log) 2>&1
test "$(uname -s)/$(uname -m)" = Linux/x86_64
case "${SKIPPY_CI_VULKAN_VENDOR:-}" in
  nvidia)
    # The NVIDIA actions-runner image is already the GPU container. Its
    # unprivileged account uses the image's loader and injected host driver.
    command -v vulkaninfo
    # NVIDIA also dlopens EGL during Vulkan initialization, even headlessly.
    sudo -n apt-get update
    sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libegl1
    ;;
  amd)
    test "$(id -u)" = 0
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libvulkan1 vulkan-tools mesa-vulkan-drivers
    ;;
  *) echo 'Vulkan smoke requires an explicit nvidia or amd vendor' >&2; exit 1 ;;
esac
# Expose only the assigned vendor's hardware ICD; Mesa can also install Lavapipe.
icds=()
for manifest in /etc/vulkan/icd.d/*.json /usr/share/vulkan/icd.d/*.json; do
  [[ -f "$manifest" ]] || continue
  case "$SKIPPY_CI_VULKAN_VENDOR" in
    nvidia) pattern='libGLX_nvidia|libvulkan_nvidia' ;;
    amd) pattern='libvulkan_radeon' ;;
  esac
  if jq -e --arg pattern "$pattern" '.ICD.library_path | test($pattern)' "$manifest" >/dev/null; then
    icds+=("$manifest")
  fi
done
if [[ ${#icds[@]} -eq 0 ]]; then
  echo "No hardware Vulkan ICD found for $SKIPPY_CI_VULKAN_VENDOR" >&2
  exit 1
fi
printf 'VK_DRIVER_FILES=%s\n' "$(IFS=:; echo "${icds[*]}")" >> "$GITHUB_ENV"
