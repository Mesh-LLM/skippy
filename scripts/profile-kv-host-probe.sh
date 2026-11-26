#!/usr/bin/env bash
# Read-only prerequisites for the four-model Metal profiling campaign.
set -euo pipefail
out="${JIANYANG_ARTIFACT_DIR:?}/host-probe.txt"
mkdir -p "$(dirname "$out")"
exec > >(tee "$out") 2>&1
id
sw_vers
sysctl -n machdep.cpu.brand_string hw.memsize hw.ncpu
sysctl vm.swapusage
pmset -g therm
pmset -g custom
vm_stat
df -h .
for tool in just cmake ninja clang++ rustc cargo uv python3 inferencekit; do
    command -v "$tool" || true
done
xcode-select -p || true
xcrun xctrace version || true
xcrun xctrace list templates || true
ls -d /Applications/Xcode*.app /Volumes/* /mnt/* /Users/Shared/* 2>/dev/null || true
python3 - <<'PY'
import os
from pathlib import Path

roots = [Path('/mnt/model-store'), Path('/Volumes'), Path('/Users/Shared'),
         Path.home() / '.cache', Path.home() / '.jianyang']
names = ('Qwen3-4B-Instruct-2507-Q4_K_M.gguf', 'Qwen3.5-4B-Q4_K_M.gguf',
         'Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf', 'Qwen3.5-35B-A3B-Q4_K_M.gguf')
for root in roots:
    if not root.is_dir():
        continue
    for directory, dirs, files in os.walk(root):
        if len(Path(directory).relative_to(root).parts) > 10:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in ('node_modules', '.git', 'chunk-cache')]
        for name in files:
            if name in names:
                path = Path(directory) / name
                print('MODEL', path, path.stat().st_size, flush=True)
PY
