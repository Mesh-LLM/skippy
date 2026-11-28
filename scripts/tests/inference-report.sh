#!/usr/bin/env bash
# Offline contract tests: no GitHub calls or native hardware required.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
export FIXTURE=$fixture
export SOURCE_SHA=1111111111111111111111111111111111111111
export GITHUB_RUN_ID=42 GITHUB_RUN_ATTEMPT=2 GITHUB_EVENT_NAME=pull_request_target
export PLAN=success REQUIRED=true RESULT=success
export BASELINE_SHA=2222222222222222222222222222222222222222
export GITHUB_EVENT_PATH="$fixture/event.json" GITHUB_STEP_SUMMARY="$fixture/summary.md"
mkdir -p "$fixture/bin" "$fixture/evidence"
export PATH="$fixture/bin:$PATH"
cat > "$fixture/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ $1 == run && $2 == download ]]; then
  [[ ! -f $FIXTURE/download-fails ]] || exit 1
  [[ $3 == 40 && $4 == --repo && $5 == Mesh-LLM/skippy ]] || exit 1
  [[ $6 == --pattern && $7 == inference-*-40-3 && $8 == --dir ]] || exit 1
  mkdir -p "$9"
  cp -R "$FIXTURE/baseline/." "$9/"
  exit
fi
[[ $1 == api ]]
if [[ ${3:-} == --method ]]; then
  printf '%s %s\n' "$2" "$4" > "$FIXTURE/mutation"
  cp "$6" "$FIXTURE/posted.json"
else
  case $2 in
    */workflows/inference.yml/runs\?*) cat "$FIXTURE/main-runs.json" ;;
    */git/trees/1111111111111111111111111111111111111111\?*) cat "$FIXTURE/current-tree.json" ;;
    */git/trees/2222222222222222222222222222222222222222\?*) cat "$FIXTURE/main-tree.json" ;;
    */runs/40/attempts/3/jobs\?*) cat "$FIXTURE/main-jobs.json" ;;
    */runs/42/attempts/2/jobs\?*) cat "$FIXTURE/jobs.json" ;;
    */comments\?*) cat "$FIXTURE/comments.json" ;;
    */pulls/52) cat "$FIXTURE/pr.json" ;;
    *) exit 1 ;;
  esac
fi
EOF
chmod +x "$fixture/bin/gh"
printf '{"number":52}\n' > "$GITHUB_EVENT_PATH"
jq -n --arg sha "$SOURCE_SHA" '{state:"open",head:{sha:$sha}}' > "$fixture/pr.json"
printf '[[]]\n' > "$fixture/comments.json"
jq '[.models[] | select(.pr) | (.id + "-standalone"), (select(.split) | .id + "-split")]' \
  "$root/ci/inference-ladder.json" > "$fixture/rows.json"
jq 'to_entries | map({id:(.key+1), name:("qualification ("+.value+")"), conclusion:"success"}) |
  [{jobs: .[:3]}, {jobs: .[3:]}]' \
  "$fixture/rows.json" > "$fixture/jobs.json"
row=$(jq -r '.[0]' "$fixture/rows.json")
receipt="$fixture/evidence/$row-job.json"
rates="$fixture/evidence/$row-throughput.json"
jq -n --arg sha "$SOURCE_SHA" '{repo_rev:$sha,id:"job_test",status:"succeeded",runs:[{id:"run_test",status:"succeeded"}]}' > "$receipt"
jq -n --arg sha "$SOURCE_SHA" --arg row "$row" '{source_sha:$sha,configuration:$row,job_id:"job_test",run_id:"run_test",workload:"DGX Spark warm probe v2",requests:3,prefill_tokens:300,prefill_ms:600,decode_tokens:384,decode_ms:3840}' > "$rates"
cp "$rates" "$fixture/good-rates.json"
cp "$receipt" "$fixture/good-receipt.json"

report() {
  rm -f "$fixture/posted.json" "$fixture/mutation" "$GITHUB_STEP_SUMMARY"
  bash "$root/scripts/inference-report.sh" "$fixture/evidence" > "$fixture/stdout" 2> "$fixture/stderr"
}
reject() {
  if report; then printf 'Expected rejection: %s\n' "$*" >&2; exit 1; fi
  [[ ! -f $fixture/posted.json ]]
}

report
jq -e '.body | contains("Lab Qualification: Passed") and contains("| 500.0 | Unavailable | 100.0 | Unavailable |") and contains("/job/1") and (contains("pilot.jianyang.cloud") | not) and (contains("Lab record") | not) and contains("/attempts/2")' "$fixture/posted.json" > /dev/null
grep -q 'POST$' "$fixture/mutation"
cmp "$GITHUB_STEP_SUMMARY" <(jq -rj .body "$fixture/posted.json")

# In-flight legacy workflows can finish after main deploys, without a baseline pin.
BASELINE_SHA='' report
jq -e '.body | contains("Legacy smoke rates are diagnostic only") and contains("| 500.0 | Unavailable | 100.0 | Unavailable |")' "$fixture/posted.json" > /dev/null

# Old protected producers remain readable, but their short answers are never warm TPS.
jq 'del(.workload) | .decode_tokens=11 | .decode_ms=400' "$fixture/good-rates.json" > "$rates"
report
jq -e '.body | contains("Lab Qualification: Passed") and contains("| Unavailable | Unavailable | Unavailable | Unavailable |")' "$fixture/posted.json" > /dev/null
cp "$fixture/good-rates.json" "$rates"
jq '.decode_tokens=11' "$fixture/good-rates.json" > "$rates"
reject 'short answers mislabeled as warm probes'
cp "$fixture/good-rates.json" "$rates"

# Paginated comments: ignore forged markers and preserve newer generations.
jq -n '[[],[
  {id:7,user:{login:"github-actions[bot]",type:"Bot"},body:"<!-- skippy-inference-results:v1 -->\n<!-- run:42:1 -->\nold"},
  {id:8,user:{login:"person",type:"User"},body:"<!-- skippy-inference-results:v1 -->\n<!-- run:99:9 -->\nforged"}
]]' > "$fixture/comments.json"
report
grep -q '/issues/comments/7 PATCH$' "$fixture/mutation"
for generation in '43:1' '42:3'; do
  jq --arg generation "$generation" '.[1][0].body = ("<!-- skippy-inference-results:v1 -->\n<!-- run:" + $generation + " -->")' "$fixture/comments.json" > "$fixture/newer.json"
  cp "$fixture/comments.json" "$fixture/old.json"
  cp "$fixture/newer.json" "$fixture/comments.json"
  report
  [[ ! -f $fixture/posted.json ]]
  mv "$fixture/old.json" "$fixture/comments.json"
done
for changed in '.state="closed"' '.head.sha="2222222222222222222222222222222222222222"'; do
  cp "$fixture/pr.json" "$fixture/open.json"
  jq "$changed" "$fixture/open.json" > "$fixture/pr.json"
  report
  [[ ! -f $fixture/posted.json ]]
  mv "$fixture/open.json" "$fixture/pr.json"
done

for field in source_sha configuration job_id run_id requests; do
  jq --arg field "$field" '.[$field]="wrong"' "$fixture/good-rates.json" > "$rates"
  reject "$field mismatch"
done
cp "$fixture/good-rates.json" "$rates"
for changed in '.repo_rev="wrong"' '.id="job_bad)[url]"' '.status="failed"' '.runs[-1].id="run_wrong"' '.runs[-1].status="failed"'; do
  jq "$changed" "$fixture/good-receipt.json" > "$receipt"
  reject "$changed"
done
cp "$fixture/good-receipt.json" "$receipt"
for value in 0 -1; do
  jq --argjson value "$value" '.prefill_ms=$value' "$fixture/good-rates.json" > "$rates"
  report
  jq -e '.body | contains("| Unavailable | Unavailable | 100.0 | Unavailable |")' "$fixture/posted.json" > /dev/null
done
cp "$fixture/good-rates.json" "$rates"
for state in failure cancelled skipped timed_out startup_failure ''; do
  jq --arg state "$state" '.[0].jobs[0].conclusion=$state' "$fixture/jobs.json" > "$fixture/failed.json"
  cp "$fixture/jobs.json" "$fixture/success.json"
  mv "$fixture/failed.json" "$fixture/jobs.json"
  report
  jq -e '.body | contains("Lab Qualification: Failed or incomplete") and (contains("| 500.0 | Unavailable | 100.0 | Unavailable |") | not)' "$fixture/posted.json" > /dev/null
  mv "$fixture/success.json" "$fixture/jobs.json"
done
printf '[{"jobs":[]}]\n' > "$fixture/jobs.json"
report
jq -e '.body | contains("Lab Qualification: Failed or incomplete") and contains("Missing or incomplete")' "$fixture/posted.json" > /dev/null
REQUIRED=false reject 'irrelevant change'
GITHUB_EVENT_NAME=pull_request reject 'unprotected event'
SOURCE_SHA=invalid reject 'mutable source'
# Paired receipts replace historical comparisons; the protected reporter never
# queries successful-main runs or downloads an unrelated allocation.
jq 'to_entries | map({id:(.key+1), name:("qualification ("+.value+")"), conclusion:"success"}) |
  [{jobs: .}]' "$fixture/rows.json" > "$fixture/jobs.json"
PYTHONPATH="$root/scripts" python3 - "$fixture/evidence/$row-paired.json" "$row" <<'PYDATA'
import json, os, sys
from lib.inference_pairs import PROTOCOL, ROUNDS, REPETITIONS, schedule, summarize
source, baseline = os.environ['SOURCE_SHA'], os.environ['BASELINE_SHA']
placement = [{'role':'leader','node':'spark-test'}]
data = {'schema_version':1,'protocol':PROTOCOL,'source_sha':source,'baseline_sha':baseline,
        'configuration':sys.argv[2],'rounds':ROUNDS,'repetitions':REPETITIONS,
        'placements':placement,'runs':[],'job_id':'job_test','run_id':'run_test'}
for design in schedule(baseline, source):
    ratio = 1.1 if design['experiment']=='ab' and design['arm']=='b' else 1
    data['runs'].append({**design,'placements':placement,'quality':'passed',
        'samples':[{'shape':i%3,'prefill_tokens':200+i%3,'prefill_ms':100/ratio,
                    'decode_tokens':128,'decode_ms':200/ratio} for i in range(REPETITIONS*3)]})
data['summary'] = summarize(data, source, baseline, sys.argv[2])
with open(sys.argv[1], 'w') as stream: json.dump(data, stream)
PYDATA
report
jq -e '.body | contains("+10% [+10%, +10%] · improved") and contains("pinned at admission") and contains("200 input") and contains("Actions logs") and (contains("pilot.jianyang.cloud") | not) and (contains("Lab record") | not)' "$fixture/posted.json" > /dev/null
jq -e '[.body | split("\n")[] | select(startswith("| `")) | split("|") | length] | all(. == 10)' "$fixture/posted.json" > /dev/null
cp "$fixture/evidence/$row-paired.json" "$fixture/good-paired.json"
for changed in '.baseline_sha="wrong"' '.source_sha="wrong"' '.run_id="wrong"' '.rounds=5' '.runs=[]' '.summary=[]' '.summary[0].decode.ci95=[0,2]' '.summary[0].decode.calibrated=false'; do
  jq "$changed" "$fixture/good-paired.json" > "$fixture/evidence/$row-paired.json"
  reject "$changed"
done
cp "$fixture/good-paired.json" "$fixture/evidence/$row-paired.json"
BASELINE_SHA=main reject 'mutable baseline'
BASELINE_SHA='' reject 'missing paired baseline'
printf '%s\n' 'Inference report contracts passed'
