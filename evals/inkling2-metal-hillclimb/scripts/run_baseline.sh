#!/usr/bin/env bash
# Inkling 2 Mini decode baseline on a james-lab Metal host. This is the `command` of the
# `inkling2-metal-baseline` template in .gi/templates.yaml; the template's build step has
# already run `just build backend="metal"` at the admitted commit.
#
# Phases (each writes JSON to the artifact dir and one INKLING2_RESULT line to the log):
#   host inventory, HF cache, GPU membench, then per quant: GGUF inventory, c1 bench
#   (coherence sample, decode, prefill, TTFT), c4 bench, GPU command-buffer profile and
#   the ceilings. A failed phase is recorded and the job continues with the next one, so
#   one broken measurement never hides the others; the exit status is non-zero if any
#   phase failed. Written for the system bash 3.2.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.cargo/bin:$PATH"
export HF_HUB_DISABLE_PROGRESS_BARS=1
export PYTHONUNBUFFERED=1

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
OUT="${JIANYANG_ARTIFACT_DIR:-$REPO/target/inkling2-hillclimb}"
mkdir -p "$OUT"
SKIPPY="$REPO/target/debug/skippy"
BUNDLE="$REPO/target/debug/native-runtimes"
PORT="${INKLING2_PORT:-19337}"
BASE="http://127.0.0.1:$PORT"
MODEL_ID="inkling2-mini"
HF_REPO="ggml-org/Inkling-2-Mini-GGUF"
HF_REVISION="0bdc580634ceac4c4d921f452777bf44228de9af"   # ggml-org/Inkling-2-Mini-GGUF, 2026-11-25
CTX="${INKLING2_CTX:-12288}"          # 8k prefill probe plus headroom; fixed for every run
LOOP_LAYERS="${INKLING2_LOOP_LAYERS:-6-17}"
LOOP_PASSES="${INKLING2_LOOP_PASSES:-3}"
QUANTS="${INKLING2_QUANTS:-Q8_0 IQ4_NL}"
START_S="$(date +%s)"
SERVER_PID=""
FAILED=0
FAILURES=""

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# phase NAME COMMAND...: never aborts the job; remembers the failure.
phase() {
  local name="$1"; shift
  local t0 rc
  t0="$(date +%s)"
  log "== phase $name"
  "$@"
  rc=$?
  if [[ "$rc" == 0 ]]; then log "== phase $name ok ($(( $(date +%s) - t0 )) s)"
  else log "== phase $name FAILED rc=$rc ($(( $(date +%s) - t0 )) s)"; FAILED=1; FAILURES="$FAILURES $name"; fi
  return 0
}

expected_sha() {
  case "$1" in
    Q8_0) echo "d57542f8aea5d7639beba5fc4ae7f584754916f63ac5d5a69328c27f652f45da" ;;
    IQ4_NL) echo "cbdf3fa9dc50a4124977980f37da4f1fa764b0cfe800d275cd43d26ce2a81af4" ;;
    *) echo "unknown" ;;
  esac
}

hf_cache_dir() {
  if [[ -n "${HF_HUB_CACHE:-}" ]]; then echo "$HF_HUB_CACHE"
  elif [[ -n "${HUGGINGFACE_HUB_CACHE:-}" ]]; then echo "$HUGGINGFACE_HUB_CACHE"
  elif [[ -n "${HF_HOME:-}" ]]; then echo "$HF_HOME/hub"
  else echo "$HOME/.cache/huggingface/hub"; fi
}

# ensure_model QUANT -> local path of the pinned file in the HF cache.
# Called inside $(...): everything it has to say goes to stderr, or the log lines end up
# in the caller's `path` and the file check fails on a multi-line string.
ensure_model() {
  local quant="$1" tool path
  if tool="$(command -v hf || command -v huggingface-cli)"; then
    log "downloading (if absent) with $tool: $HF_REPO Inkling-2-Mini-$quant.gguf @$HF_REVISION" 1>&2
    path="$("$tool" download "$HF_REPO" "Inkling-2-Mini-${quant}.gguf" --revision "$HF_REVISION" \
      2>>"$OUT/hf_download_${quant}.log" | awk '{sub(/^[[:space:]]*path:[[:space:]]*/, "")} /^\//{p=$0} END{print p}')"
  else
    log "no hf CLI found; falling back to the huggingface_hub Python API" 1>&2
    python3 -m pip install --user -q huggingface_hub >>"$OUT/hf_download_${quant}.log" 2>&1
    path="$(python3 - "$HF_REPO" "Inkling-2-Mini-${quant}.gguf" "$HF_REVISION" 2>>"$OUT/hf_download_${quant}.log" <<'PY'
import sys
from huggingface_hub import hf_hub_download
print(hf_hub_download(repo_id=sys.argv[1], filename=sys.argv[2], revision=sys.argv[3]))
PY
)"
  fi
  if [[ -z "$path" || ! -f "$path" ]]; then
    log "model file not found for $quant (path '$path')" 1>&2
    tail -n 20 "$OUT/hf_download_${quant}.log" 2>/dev/null 1>&2
    return 1
  fi
  printf '%s\n' "$path"
}

# record_model QUANT: download, verify the LFS sha256, record revision and cache paths
record_model() {
  local quant="$1" expected path size sha
  expected="$(expected_sha "$quant")"
  path="$(ensure_model "$quant")" || return 1
  size="$(stat -f %z "$path")"
  log "verifying sha256 of $path ($size bytes)"
  sha="$(shasum -a 256 "$path" | awk '{print $1}')"
  printf '%s\n' "$path" >"$OUT/model_${quant}.path"
  python3 - "$quant" "$path" "$size" "$sha" "$expected" "$HF_REPO" "$HF_REVISION" <<'PY' | tee "$OUT/model_${quant}.json"
import json, os, subprocess, sys
quant, path, size, sha, expected, repo, revision = sys.argv[1:8]
home = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
cache = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE") or os.path.join(home, "hub")
real = os.path.realpath(path)
snapshot = os.path.dirname(real)
model_dir = os.path.dirname(os.path.dirname(snapshot))
def du(p):
    try:
        return int(subprocess.run(["du", "-sk", p], capture_output=True, text=True, timeout=600).stdout.split()[0]) * 1024
    except Exception:
        return None
refs = {}
try:
    for name in os.listdir(os.path.join(model_dir, "refs")):
        refs[name] = open(os.path.join(model_dir, "refs", name)).read().strip()
except OSError:
    pass
out = {
    "repo": repo, "requested_revision": revision, "snapshot_revision": os.path.basename(snapshot),
    "quant": quant, "file": os.path.basename(real), "path": real, "link": path,
    "bytes": int(size), "gib": round(int(size) / 2**30, 3), "sha256": sha, "expected_sha256": expected,
    "sha256_matches_hf_lfs": sha == expected, "hf_home": os.environ.get("HF_HOME"),
    "hf_hub_cache": cache, "refs": refs,
    "model_dir": model_dir, "model_dir_bytes": du(model_dir), "hub_cache_bytes": du(cache),
}
print("INKLING2_RESULT model_%s %s" % (quant, json.dumps(out, separators=(",", ":"))))
PY
}

start_server() {
  # start_server TAG MODEL_PATH CTX CONCURRENCY -> sets SERVER_PID; fails if not ready
  local tag="$1" model="$2" ctx="$3" conc="$4" i ready=0
  "$SKIPPY" --runtime-bundle "$BUNDLE" --debug serve \
    --model-path "$model" --model-id "$MODEL_ID" --ctx-size "$ctx" --n-gpu-layers 999 \
    --bind-addr "127.0.0.1:$PORT" --generation-concurrency "$conc" \
    --speculative-strategy disabled --startup-timeout-secs 1500 \
    --telemetry-level "${TELEMETRY_LEVEL:-summary}" --telemetry-queue-capacity "${TELEMETRY_QUEUE:-1024}" \
    >"$OUT/server_$tag.stdout.log" 2>"$OUT/server_$tag.stderr.log" &
  SERVER_PID=$!
  for i in $(seq 1 1800); do
    if curl -sf -m 5 "$BASE/v1/models" >/dev/null 2>&1; then ready=1; break; fi
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 1
  done
  if [[ "$ready" != 1 ]]; then
    log "server $tag is not ready after ${i}s"
    tail -n 80 "$OUT/server_$tag.stderr.log" "$OUT/server_$tag.stdout.log" 2>/dev/null
    return 1
  fi
  log "server $tag ready after ${i}s (pid $SERVER_PID); /v1/models: $(curl -s -m 10 "$BASE/v1/models")"
}

stop_server() {
  # Stop only after every request finished: SIGTERM, wait, escalate after two minutes.
  [[ -n "$SERVER_PID" ]] || return 0
  local pid="$SERVER_PID" i
  SERVER_PID=""
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null
    for i in $(seq 1 120); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then log "server $pid ignored SIGTERM for 120s; SIGKILL"; kill -KILL "$pid" 2>/dev/null; fi
  fi
  wait "$pid" 2>/dev/null
  log "server $pid stopped"
}
trap 'stop_server' EXIT

# Measured device memory bandwidth and compute, from Skippy's Metal membench
# (crates/skippy-gpu-bench/native/metal): a 512 MB buffer read and two FMA kernels.
run_membench() {
  "$SKIPPY" --runtime-bundle "$BUNDLE" --output json gpu-benchmark --timeout-secs 300 >"$OUT/membench.json" 2>"$OUT/membench.stderr.log"
  local rc=$?
  cat "$OUT/membench.json"
  [[ "$rc" == 0 ]] || { cat "$OUT/membench.stderr.log"; return "$rc"; }
}

run_quant() {
  # run_quant QUANT: inventory, c1, c4, profile, ceiling for one quantization
  local q="$1" path window
  path="$(cat "$OUT/model_${q}.path")"
  phase "inventory_$q" python3 "$HERE/gguf_inventory.py" "$path" --tag "gguf_inventory_$q" --out "$OUT/gguf_inventory_$q.json"

  if start_server "c1_$q" "$path" "$CTX" 1; then
    phase "bench_c1_$q" python3 "$HERE/bench.py" --base-url "$BASE" --model-id "$MODEL_ID" --quant "$q" \
      --phases sanity,c1,prefill,ttft --reps "${INKLING2_REPS:-6}" --server-pid "$SERVER_PID" \
      --tag "bench_c1_$q" --out "$OUT/bench_c1_$q.json"
  else FAILED=1; FAILURES="$FAILURES server_c1_$q"; fi
  stop_server

  if start_server "c4_$q" "$path" "$CTX" 4; then
    phase "bench_c4_$q" python3 "$HERE/bench.py" --base-url "$BASE" --model-id "$MODEL_ID" --quant "$q" \
      --phases c4 --concurrency 4 --reps 3 --server-pid "$SERVER_PID" --tag "bench_c4_$q" --out "$OUT/bench_c4_$q.json"
  else FAILED=1; FAILURES="$FAILURES server_c4_$q"; fi
  stop_server

  # GPU profile: debug telemetry to stderr, Metal splitting graphs at op-class boundaries.
  TELEMETRY_LEVEL=debug
  TELEMETRY_QUEUE=16384
  export SKIPPY_TELEMETRY_STDERR=1 GGML_METAL_OP_PROFILE=1
  start_server "profile_$q" "$path" 4096 1 || { FAILED=1; FAILURES="$FAILURES server_profile_$q"; }
  if [[ -n "$SERVER_PID" ]]; then
    phase "profile_request_$q" python3 "$HERE/bench.py" --base-url "$BASE" --model-id "$MODEL_ID" --quant "$q" \
      --phases profile --warm 32 --profile-tokens 64 --tag "profile_request_$q" --out "$OUT/profile_request_$q.json"
  fi
  stop_server
  unset SKIPPY_TELEMETRY_STDERR GGML_METAL_OP_PROFILE
  TELEMETRY_LEVEL=summary
  TELEMETRY_QUEUE=1024
  window="$(python3 -c 'import json,sys; print("%d,%d" % tuple(json.load(open(sys.argv[1]))["profile"]["window_ns"]))' \
    "$OUT/profile_request_$q.json" 2>/dev/null)"
  if [[ -n "$window" ]]; then
    phase "profile_$q" python3 "$HERE/profile_from_spans.py" "$OUT/server_profile_$q.stderr.log" --window "$window" \
      --tag "profile_$q" --out "$OUT/profile_$q.json"
  else
    log "no profile window for $q; skipping the profile phase"; FAILED=1; FAILURES="$FAILURES profile_$q"
  fi

  phase "ceiling_$q" python3 "$HERE/ceiling.py" --inventory "$OUT/gguf_inventory_$q.json" \
    --membench "$OUT/membench.json" --bench "$OUT/bench_c1_$q.json" --bench "$OUT/bench_c4_$q.json" \
    --profile "$OUT/profile_$q.json" --loop-layers "$LOOP_LAYERS" --loop-passes "$LOOP_PASSES" \
    --tag "ceiling_$q" --out "$OUT/ceiling_$q.json"
}

log "Inkling 2 Mini Metal baseline: commit ${JIANYANG_COMMIT:-?} on $(hostname)"
cd "$REPO"
log "HEAD $(git rev-parse HEAD 2>/dev/null); skippy $SKIPPY"; [[ -x "$SKIPPY" ]] || { log "skippy binary missing"; exit 1; }
if pgrep -x skippy >/dev/null 2>&1; then
  log "WARNING: other skippy processes are running on this host:"
  ps -axo pid,pcpu,rss,etime,command | grep -E "[s]kippy" | head -n 5
fi

phase "host_inventory" bash "$HERE/preflight.sh"
phase "membench" run_membench
phase "membench_extract" python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
found = []
def walk(o):
    if isinstance(o, dict):
        if "p50_gbps" in o: found.append(o)
        for v in o.values(): walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
walk(data)
print("INKLING2_RESULT membench " + json.dumps(found, separators=(",", ":")))
sys.exit(0 if found else 1)
' "$OUT/membench.json"

for q in $QUANTS; do
  if [[ "$q" == IQ4_NL && $(( $(date +%s) - START_S )) -gt "${INKLING2_SECOND_QUANT_DEADLINE_S:-9000}" ]]; then
    log "skipping IQ4_NL: $(( $(date +%s) - START_S )) s already elapsed"
    FAILED=1; FAILURES="$FAILURES skipped_IQ4_NL"
    continue
  fi
  phase "model_$q" record_model "$q"
  [[ -s "$OUT/model_$q.path" ]] || { log "no model for $q; skipping its runs"; continue; }
  run_quant "$q"
  pmset -g therm 2>/dev/null | tr '\n' ' '; echo
done

python3 - "$FAILED" "$FAILURES" "$START_S" "${JIANYANG_COMMIT:-}" "${JIANYANG_JOB_ID:-}" <<'PY'
import json, os, subprocess, sys, time
failed, failures, start = sys.argv[1], sys.argv[2].strip(), int(sys.argv[3])
print("INKLING2_RESULT job " + json.dumps({
    "failed": failed == "1", "failures": failures, "elapsed_s": int(time.time()) - start,
    "commit": sys.argv[4], "job_id": sys.argv[5], "node": os.uname().nodename,
    "artifact_dir": os.environ.get("JIANYANG_ARTIFACT_DIR"),
    "artifacts": sorted(os.listdir(os.environ.get("JIANYANG_ARTIFACT_DIR") or ".")),
}, separators=(",", ":")))
PY
log "baseline finished; failed phases:${FAILURES:- none}"
exit "$FAILED"
