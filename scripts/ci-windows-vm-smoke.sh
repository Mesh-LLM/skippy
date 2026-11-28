#!/usr/bin/env bash
set -euo pipefail

test "$(uname -s)" = Linux
test "$(uname -m)" = x86_64
: "${WINDOWS_BACKEND:?}" "${WINDOWS_HOST_PROFILE:?}" "${WINDOWS_RECIPE_SHA:?}"
: "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}" "${GITHUB_JOB:?}"
recipe="$PWD/target/windows-recipe/windows"
test "$(git -C target/windows-recipe rev-parse HEAD)" = "$WINDOWS_RECIPE_SHA"
profile_file=$(mktemp)
trap 'rm -f "$profile_file"' EXIT
python3 scripts/ci-windows-vm-profile.py > "$profile_file"
mapfile -t profile < "$profile_file"
[[ "${#profile[@]}" == 6 || "${#profile[@]}" == 10 || "${#profile[@]}" == 14 ]]
firmware=("${profile[@]:6}")
base=${profile[0]}
driver=${profile[1]}
driver_sha256=${profile[2]}
gpu=${profile[3]}
work_root=${profile[4]}
manager=${profile[5]}
lease="github-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$GITHUB_JOB-$WINDOWS_BACKEND"
work="$work_root/$lease"
input="$PWD/target/windows-input"
evidence="$PWD/target/windows-evidence"
mkdir -p "$input" "$evidence"
python3 "$recipe/vm.py" doctor --backend "$WINDOWS_BACKEND" --gpu "$gpu" --inspect > "$evidence/topology.json"
git bundle create "$input/source.bundle" HEAD
cp scripts/ci-windows-vm-task.ps1 "$input/task.ps1"
cp "${SKIPPY_CI_MODEL:?pinned Qwen3 fixture was not admitted}" "$input/model.gguf"
cp "$driver" "$input/driver.exe"
python3 - "$input" "$WINDOWS_BACKEND" "$gpu" "$driver_sha256" <<'PY'
import json, pathlib, subprocess, sys
root, backend, gpu, digest = pathlib.Path(sys.argv[1]), *sys.argv[2:]
device = pathlib.Path('/sys/bus/pci/devices', gpu, 'device').read_text().strip()[2:]
(root/'driver.json').write_text(json.dumps({'backend':backend, 'device_id':device, 'sha256':digest}))
(root/'task.json').write_text(json.dumps({'source':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(), 'backend':backend}))
PY
image=$(python3 - "$base/base.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['launcher_image'])
PY
)
# The installed manager is a prerequisite, not an arbitrary workflow-provided
# shell fragment. It owns the crash journal, exclusive host lease verification,
# original driver bindings, and quarantine on failed restoration.
acquired=false
cleanup() {
  local status=$?
  local restored=true
  trap - EXIT INT TERM
  if [[ -d "$work/shared/output" ]]; then
    cp -a "$work/shared/output/." "$evidence/" || status=1
    if [[ "$status" != 0 && -f "$work/shared/output/guest.log" ]]; then
      tail -80 "$work/shared/output/guest.log"
    fi
  fi
  if [[ "$acquired" == true ]]; then
    "$manager" release --lease "$lease" --gpu "$gpu" --journal "$evidence/host-lifecycle.json" || { status=1; restored=false; }
  fi
  if [[ "$restored" == true && -d "$work" ]]; then
    rm -rf -- "$work" || status=1
  fi
  rm -f "$profile_file"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
acquired=true
host_sha256=$(sha256sum "$recipe/host.py" | cut -d ' ' -f 1)
"$manager" acquire --lease "$lease" --gpu "$gpu" --work "$work" --journal "$evidence/host-lifecycle.json" \
  --expected-host-sha256 "$host_sha256"
just --justfile "$recipe/justfile" run --image "$image" --base "$base" \
  --backend "$WINDOWS_BACKEND" --gpu "$gpu" --lease "$lease" \
  --input "$input" --work "$work" --lifecycle-manager "$manager" --timeout 3600 "${firmware[@]}"
