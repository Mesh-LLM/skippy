#!/usr/bin/env bash
# Read-only inventory of a lab Metal host, taken before any Inkling 2 measurement.
# Changes nothing: it reports hardware, toolchain, Hugging Face cache location, power
# state and whatever else is running, so a result can be tied to a known machine.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"

section() { printf '\n=== %s ===\n' "$1"; }
run() {
  printf '$ %s\n' "$*"
  "$@" 2>&1 || echo "(exit $?)"
}

section identity
run date -u
run hostname
run whoami
run sw_vers
run uname -a

section hardware
# Identifiers (serial, UUID, UDID) are left out of the record; the node name identifies the host.
run system_profiler SPHardwareDataType | grep -v -E "Serial Number|UUID|UDID"
run system_profiler SPDisplaysDataType
for key in machdep.cpu.brand_string hw.memsize hw.ncpu hw.perflevel0.physicalcpu \
  hw.perflevel1.physicalcpu hw.tbfrequency hw.cachesizes hw.l1icachesize hw.l1dcachesize \
  hw.l2cachesize hw.perflevel0.l2cachesize; do
  printf '%s = ' "$key"
  sysctl -n "$key" 2>&1 || true
done

section memory
run vm_stat
run sysctl vm.swapusage
run df -h "$HOME"

section toolchain
run xcodebuild -version
run xcode-select -p
for tool in just cmake ninja rustup cargo rustc clang python3 pip3 uv hf huggingface-cli \
  git brew jq curl xcrun shasum; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%-16s %s\n' "$tool" "$(command -v "$tool")"
  else
    printf '%-16s MISSING\n' "$tool"
  fi
done
run rustup toolchain list
run cargo --version
run just --version
run cmake --version
run python3 --version
run hf version

section huggingface
printf 'HF_HOME=%s\nHF_HUB_CACHE=%s\nHUGGINGFACE_HUB_CACHE=%s\nHF_ENDPOINT=%s\n' \
  "${HF_HOME:-}" "${HF_HUB_CACHE:-}" "${HUGGINGFACE_HUB_CACHE:-}" "${HF_ENDPOINT:-}"
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY'
import importlib.util, os, pathlib
spec = importlib.util.find_spec("huggingface_hub")
print("huggingface_hub module:", spec.origin if spec else "not installed")
home = os.environ.get("HF_HOME") or os.path.join(pathlib.Path.home(), ".cache", "huggingface")
cache = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE") or os.path.join(home, "hub")
root = pathlib.Path(cache)
print("expected hub cache:", root, "exists" if root.exists() else "absent")
model = root / "models--ggml-org--Inkling-2-Mini-GGUF"
print("Inkling-2-Mini-GGUF in cache:", model.exists())
if model.exists():
    for path in sorted(model.rglob("*")):
        if path.is_file() or path.is_symlink():
            print("  ", path, "->", os.path.realpath(path) if path.is_symlink() else "", path.stat().st_size)
PY
fi
if command -v hf >/dev/null 2>&1; then
  run hf cache dir
fi

section power_and_thermal
run pmset -g batt
run pmset -g therm
run pmset -g

section load
run uptime
ps -axo pid,pcpu,rss,etime,comm | grep -i -E "skippy|llama|cargo|cmake|ninja|metal" | grep -v grep || echo "(no matching processes)"

printf '\nINKLING2_PREFLIGHT_DONE\n'
