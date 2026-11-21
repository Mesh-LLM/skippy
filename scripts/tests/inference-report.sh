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
export GITHUB_EVENT_PATH="$fixture/event.json" GITHUB_STEP_SUMMARY="$fixture/summary.md"
mkdir -p "$fixture/bin" "$fixture/evidence"
export PATH="$fixture/bin:$PATH"
cat > "$fixture/bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == api ]]
if [[ ${3:-} == --method ]]; then
  printf '%s %s\n' "$2" "$4" > "$FIXTURE/mutation"
  cp "$6" "$FIXTURE/posted.json"
else
  case $2 in
    */jobs\?*) cat "$FIXTURE/jobs.json" ;;
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
GITHUB_OUTPUT="$fixture/cuda" python3 "$root/scripts/inference-plan.py" matrix pr "$SOURCE_SHA"
GITHUB_OUTPUT="$fixture/metal" python3 "$root/scripts/inference-plan.py" matrix pr "$SOURCE_SHA" --backend metal
CUDA_MATRIX=$(sed 's/^matrix=//' "$fixture/cuda")
METAL_MATRIX=$(sed 's/^matrix=//' "$fixture/metal")
export CUDA_MATRIX METAL_MATRIX
jq -n --argjson cuda "$CUDA_MATRIX" --argjson metal "$METAL_MATRIX" \
  '[$cuda.include[], $metal.include[]] | to_entries | map({id:(.key+1), name:.value.name, conclusion:"success"}) |
  [{jobs: .[:3]}, {jobs: .[3:]}]' \
  > "$fixture/jobs.json"
row=$(jq -r '.include[0].id' <<< "$CUDA_MATRIX")
receipt="$fixture/evidence/$row-job.json"
rates="$fixture/evidence/$row-throughput.json"
jq -n --arg sha "$SOURCE_SHA" '{repo_rev:$sha,id:"job_test",status:"succeeded",runs:[{id:"run_test",status:"succeeded"}]}' > "$receipt"
jq -n --arg sha "$SOURCE_SHA" --arg row "$row" '{source_sha:$sha,configuration:$row,job_id:"job_test",run_id:"run_test",requests:3,prefill_tokens:300,prefill_ms:600,decode_tokens:30,decode_ms:300}' > "$rates"
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
jq -e '.body | contains("Lab Qualification: Passed") and contains("| 500.0 | 100.0 |") and contains("/jobs/job_test") and contains("/attempts/2") and contains("Metal - Qwen/Qwen3.5-4B - standalone")' "$fixture/posted.json" > /dev/null
grep -q 'POST$' "$fixture/mutation"
cmp "$GITHUB_STEP_SUMMARY" <(jq -rj .body "$fixture/posted.json")

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
  jq -e '.body | contains("| Unavailable | 100.0 |")' "$fixture/posted.json" > /dev/null
done
cp "$fixture/good-rates.json" "$rates"
for state in failure cancelled skipped timed_out startup_failure ''; do
  jq --arg state "$state" '.[0].jobs[0].conclusion=$state' "$fixture/jobs.json" > "$fixture/failed.json"
  cp "$fixture/jobs.json" "$fixture/success.json"
  mv "$fixture/failed.json" "$fixture/jobs.json"
  report
  jq -e '.body | contains("Lab Qualification: Failed or incomplete") and (contains("| 500.0 | 100.0 |") | not)' "$fixture/posted.json" > /dev/null
  mv "$fixture/success.json" "$fixture/jobs.json"
done
printf '[{"jobs":[]}]\n' > "$fixture/jobs.json"
report
jq -e '.body | contains("Lab Qualification: Failed or incomplete") and contains("Missing or incomplete")' "$fixture/posted.json" > /dev/null
REQUIRED=false reject 'irrelevant change'
GITHUB_EVENT_NAME=pull_request reject 'unprotected event'
SOURCE_SHA=invalid reject 'mutable source'
printf '%s\n' 'Inference report contracts passed'
