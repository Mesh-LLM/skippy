#!/usr/bin/env bash
# Protected paired-result formatter. Candidate/native receipts are data only.

validate_rates() {
  jq -e --arg sha "$3" --arg row "$4" --slurpfile job "$2" '
    .source_sha == $sha and .configuration == $row and .requests == 3 and
    $job[0].repo_rev == $sha and $job[0].status == "succeeded" and
    .job_id == $job[0].id and .run_id == $job[0].runs[-1].id and
    $job[0].runs[-1].status == "succeeded" and
    (.job_id | type == "string" and test("^job_[A-Za-z0-9_]+$")) and
    (.run_id | type == "string" and test("^run_[A-Za-z0-9_]+$")) and
    (if has("workload") then .workload == "DGX Spark warm probe v2" and .decode_tokens == 384 else true end)
  ' "$1" > /dev/null
}

prepare_baseline() {
  local pair
  for pair in "$1"/*-paired.json; do
    [[ -f $pair ]] || continue
    [[ ${BASELINE_SHA:-} =~ ^[a-f0-9]{40}$ ]] || fail 'Missing admitted immutable main SHA'
    printf 'Paired reference: protected main commit %s, pinned at admission and rerun on the same reserved hardware.' "$BASELINE_SHA"
    return
  done
  printf 'Paired comparison unavailable. Legacy smoke rates are diagnostic only; no main delta is reported.'
}

paired_rows() {
  local pairs=$1 receipt=$2 row=$3 state=$4 action=$5
  jq -e --arg sha "$SOURCE_SHA" --arg main "$BASELINE_SHA" --arg row "$row" --slurpfile job "$receipt" '
    def positive: type == "number" and isfinite and . > 0;
    def phase:
      (.main_tok_s | positive) and (.candidate_tok_s | positive) and
      (.ratio | positive) and (.ci95 | length == 2) and all(.ci95[]; positive) and
      .ci95[0] <= .ratio and .ratio <= .ci95[1] and
      (.noise_pct | type == "number" and isfinite and . >= 0) and
      (.calibrated | type == "boolean") and
      (.verdict == "improved" or .verdict == "regressed" or .verdict == "inconclusive") and
      (if .calibrated then true else .verdict == "inconclusive" end);
    .schema_version == 1 and .protocol == "DGX Spark paired probe v1" and
    .source_sha == $sha and .baseline_sha == $main and .configuration == $row and
    .rounds == 6 and .repetitions == 3 and (.runs | length == 24) and
    .job_id == $job[0].id and .run_id == $job[0].runs[-1].id and
    $job[0].repo_rev == $sha and $job[0].status == "succeeded" and
    $job[0].runs[-1].status == "succeeded" and
    (.summary | map(.shape)) == [0,1,2] and
    all(.summary[]; (.prefill | phase) and (.decode | phase))
  ' "$pairs" > /dev/null || fail 'Paired comparison evidence mismatch'
  jq -r --arg row "$row" --arg state "$state" --arg action "$action" '
    def rounded: (. * 10 | round) / 10 | tostring;
    def pct: ((. - 1) * 100) as $p | (if $p >= 0 then "+" else "" end) + ($p | rounded) + "%";
    def comparison:
      "\(.ratio | pct) [\(.ci95[0] | pct), \(.ci95[1] | pct)] · \(.verdict) (main \(.main_tok_s | rounded))";
    .summary[] |
    "| `\($row)` · \(.prefill.tokens_per_request) input | \($state) | \(.prefill.candidate_tok_s | rounded) | \(.prefill | comparison) | \(.decode.candidate_tok_s | rounded) | \(.decode | comparison) | \(.prefill.noise_pct | rounded)% / \(.decode.noise_pct | rounded)% | \($action) |"
  ' "$pairs"
}
