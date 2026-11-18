#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
matrix="$root/ci/targets.json"

jq -e '
  . as $matrix
  | .schema_version == 1
    and (.hosts | length == 4)
    and (.runtimes | length == 11)
    and ([.hosts[].id, .runtimes[].id] | length == (unique | length))
    and all(.hosts[];
      (.id | type == "string")
      and (.display_name | type == "string")
      and (.display_name | length > 0)
      and (.runner | length > 0))
    and all(.runtimes[];
      (.id | type == "string")
      and (.display_name | type == "string")
      and (.display_name | length > 0)
      and (.runner | length > 0)
      and (.host as $host | any($matrix.hosts[]; .id == $host)))
' "$matrix" >/dev/null

jy_runners="${SKIPPY_CI_JY_RUNNERS:-false}"
case "$jy_runners" in
  true)
    runner_group="jy-rnr_vfnz8k5p4yrh334"
    hosts="$(jq -c --arg group "$runner_group" \
      '{include: [.hosts[] | select(.enabled != false) | .runner = {group: $group, labels: .runner}]}' \
      "$matrix")"
    runtimes="$(jq -c --arg group "$runner_group" \
      '{include: [.runtimes[] | select(.enabled != false) | .runner = {group: $group, labels: .runner}]}' \
      "$matrix")"
    ;;
  false)
    hosts="$(jq -c '{include: [.hosts[] | select(.enabled != false)]}' "$matrix")"
    runtimes="$(jq -c '{include: [.runtimes[] | select(.enabled != false)]}' "$matrix")"
    ;;
  *)
    printf 'SKIPPY_CI_JY_RUNNERS must be true or false, got %q\n' "$jy_runners" >&2
    exit 1
    ;;
esac
printf '%s\n' "$(jq -cn --argjson hosts "$hosts" --argjson runtimes "$runtimes" '{hosts: $hosts, runtimes: $runtimes}')"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'hosts=%s\nruntimes=%s\n' "$hosts" "$runtimes" >> "$GITHUB_OUTPUT"
fi
