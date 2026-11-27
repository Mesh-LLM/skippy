#!/usr/bin/env bash
# Runs from protected main; evidence is data, never executable source.
set -euo pipefail
export LC_ALL=C

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ ${GITHUB_EVENT_NAME:-} == pull_request_target ]] || fail 'Results comments require pull_request_target'
[[ ${SOURCE_SHA:-} =~ ^[a-fA-F0-9]{40}$ ]] || fail 'Invalid source SHA'
[[ ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]*$ ]] || fail 'Invalid workflow run ID'
[[ ${GITHUB_RUN_ATTEMPT:-} =~ ^[1-9][0-9]*$ ]] || fail 'Invalid workflow attempt'
[[ ${REQUIRED:-} == true ]] || fail 'Results comments require inference qualification'

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
repository=repos/Mesh-LLM/skippy
marker='<!-- skippy-inference-results:v1 -->'
number=$(jq -er '.number | select(type == "number" and . > 0 and . == floor)' "$GITHUB_EVENT_PATH")
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT
directory=${1:?Usage: inference-report.sh EVIDENCE_DIRECTORY}
run=$GITHUB_RUN_ID
attempt=$GITHUB_RUN_ATTEMPT
url="https://github.com/Mesh-LLM/skippy/actions/runs/$run"

# Pagination is needed even though today's PR ladder fits on one page.
gh api "$repository/actions/runs/$run/attempts/$attempt/jobs?per_page=100" --paginate --slurp |
  jq -e '[.[] | .jobs[]]' > "$temporary/jobs.json"
jq -e '
  if .schema_version != 1 then error("Unsupported ladder") else
    [.models[] | select(.pr) |
      if (.id | test("^[a-z0-9-]+$")) then
        (.id + "-standalone"), (select(.split) | .id + "-split")
      else error("Invalid configuration ID") end]
  end | if length > 0 and length == (unique | length) then . else error("Invalid PR ladder") end
' "$root/ci/inference-ladder.json" > "$temporary/rows.json"

outcome='Failed or incomplete'
description='Inference admission or qualification incomplete or failed'
if [[ ${PLAN:-} == success && ${RESULT:-} == success ]]; then
  description='Native Spark lab qualification'
  if jq -e --slurpfile jobs "$temporary/jobs.json" '
    all(.[]; . as $id | any($jobs[0][];
      .name == ("qualification (" + $id + ")") and .conclusion == "success"))
  ' "$temporary/rows.json" > /dev/null; then
    outcome=Passed
  fi
fi

# shellcheck source=scripts/lib/inference-report-baseline.sh
source "$root/scripts/lib/inference-report-baseline.sh"
baseline_note=$(prepare_baseline)

cat > "$temporary/body.md" <<EOF
$marker
<!-- run:$run:$attempt -->
### Skippy Lab Qualification: $outcome

Commit: \`$SOURCE_SHA\` · PR smoke · [Workflow and timings]($url/attempts/$attempt) · [Saved evidence]($url#artifacts)

$description.

$baseline_note

| Configuration · workload | Outcome | Prefill tok/s | Prefill vs main [95% CI] | Decode tok/s | Decode vs main [95% CI] | A/A noise prefill / decode | Actions logs |
| --- | --- | ---: | --- | ---: | --- | --- | --- |
EOF

rate() {
  local value
  value=$(jq -r --arg phase "$2" '
    .[$phase + "_tokens"] as $tokens | .[$phase + "_ms"] as $ms |
    if $tokens > 0 and $ms > 0 and ($ms | isfinite) then
      ($tokens * 1000 / $ms) | if isfinite then . else "Unavailable" end
    else "Unavailable" end
  ' "$1")
  if [[ $value == Unavailable ]]; then printf '%s' "$value"; else printf '%.1f' "$value"; fi
}

jq -r '.[]' "$temporary/rows.json" > "$temporary/rows.txt"
while IFS= read -r row; do
  job=$(jq -c --arg name "qualification ($row)" '[.[] | select(.name == $name)][0] // {}' "$temporary/jobs.json")
  case $(jq -r '.conclusion // ""' <<< "$job") in
    success) state=Passed ;;
    failure|timed_out|action_required|startup_failure) state=Failed ;;
    cancelled) state=Cancelled ;;
    skipped) state='Skipped (not qualified)' ;;
    *) state='Missing or incomplete' ;;
  esac
  action=—
  action_id=$(jq -r '.id // empty' <<< "$job")
  if [[ -n $action_id ]]; then
    [[ $action_id =~ ^[1-9][0-9]*$ ]] || fail 'Invalid Actions job ID'
    action="[Logs]($url/job/$action_id)"
  fi
  receipt="$directory/$row-job.json"
  prefill=Unavailable
  decode=Unavailable
  prefill_delta=Unavailable
  decode_delta=Unavailable
  rates="$directory/$row-throughput.json"
  pairs="$directory/$row-paired.json"
  if [[ $state == Passed && -f $pairs ]]; then
    paired_rows "$pairs" "$receipt" "$row" "$state" "$action" >> "$temporary/body.md"
    continue
  fi
  if [[ $state == Passed && -f $rates ]]; then
    # Invalid current evidence remains a reporting failure, not an unavailable baseline.
    validate_rates "$rates" "$receipt" "$SOURCE_SHA" "$row" || fail 'Comment throughput evidence identity mismatch'
    # Producer and protected reporter can straddle a deployment. Never relabel legacy samples.
    if jq -e '.workload == "DGX Spark warm probe v2"' "$rates" > /dev/null; then
      prefill=$(rate "$rates" prefill)
      decode=$(rate "$rates" decode)
    fi
  fi
  printf "| \`%s\` | %s | %s | %s | %s | %s | Unavailable | %s |\n" "$row" "$state" "$prefill" "$prefill_delta" "$decode" "$decode_delta" "$action" >> "$temporary/body.md"
done < "$temporary/rows.txt"

cat >> "$temporary/body.md" <<'EOF'

Paired measurements run six main/main pairs, then six main/PR pairs, alternating A/B and B/A order. Every arm restarts the server and repeats each of three prompt shapes three times, with an uncached 64-token warmup and exactly 128 measured output tokens per request at concurrency 1. Correctness and streaming checks must pass for every arm. Each shape is reported separately.

Rates are diagnostic server phase timings, not end-to-end client throughput. Each round rate is total tokens / total phase time; the table shows geometric mean rates and paired geometric ratios. Pointwise 95% Student-t intervals use six server-pair log ratios (df=5); nested requests are not independent trials. Raw responses, client times, build provenance, and placements are retained in lab evidence. A/A noise is the largest absolute log-interval bound, converted to a percentage. Improvement/regression requires the PR interval to clear that noise envelope. An A/A interval excluding equality or an envelope above 5% makes the comparison inconclusive. Intervals are per shape/phase, without a matrix-wide multiple-comparison correction. Missing paired evidence gives no delta; saved historical main runs are never substituted.

Smoke checks cover GPU startup, deterministic endpoint repeats and bounded streaming. They do not establish an AgentX performance score. [Nightly history and saved performance baselines](https://huggingface.co/datasets/meshllm/skippy-inference-qualification) are published separately from these paired comparisons. Native build and qualification output streams into each configuration’s Actions log, with per-attempt copies in Saved evidence. Any capture gaps are reported in the log and saved diagnostics. GitHub evidence copies expire after 30 days.
EOF
if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then cat "$temporary/body.md" >> "$GITHUB_STEP_SUMMARY"; fi

gh api "$repository/issues/$number/comments?per_page=100" --paginate --slurp |
  jq -e --arg marker "$marker" '
    [.[][] | select(.user.login == "github-actions[bot]" and .user.type == "Bot") |
      select(.body | startswith($marker))] | last // {}
  ' > "$temporary/comment.json"

# Re-read the PR immediately before posting; stale runs must not overwrite newer results.
gh api "$repository/pulls/$number" > "$temporary/pr.json"
if ! jq -e --arg sha "$SOURCE_SHA" '.state == "open" and .head.sha == $sha' "$temporary/pr.json" > /dev/null ||
   jq -e --argjson run "$run" --argjson attempt "$attempt" '
     (.body // "" | split("\n")[1] // "" |
       capture("^<!-- run:(?<run>[0-9]+):(?<attempt>[0-9]+) -->$")?) as $old |
     ($old.run | tonumber) > $run or
     (($old.run | tonumber) == $run and ($old.attempt | tonumber) > $attempt)
   ' "$temporary/comment.json" > /dev/null; then
  printf '%s\n' 'Skipping results comment for superseded qualification'
  exit 0
fi

method=POST
endpoint="$repository/issues/$number/comments"
comment_id=$(jq -r '.id // empty' "$temporary/comment.json")
if [[ -n $comment_id ]]; then
  [[ $comment_id =~ ^[1-9][0-9]*$ ]] || fail 'Invalid comment ID'
  method=PATCH
  endpoint="$repository/issues/comments/$comment_id"
fi
jq -Rs '{body: .}' "$temporary/body.md" > "$temporary/payload.json"
gh api "$endpoint" --method "$method" --input "$temporary/payload.json" > /dev/null
