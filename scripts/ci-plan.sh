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

hosts="$(jq -c '{include: [.hosts[] | select(.enabled != false)]}' "$matrix")"
runtimes="$(jq -c '{include: [.runtimes[] | select(.enabled != false)]}' "$matrix")"
printf '%s\n' "$(jq -cn --argjson hosts "$hosts" --argjson runtimes "$runtimes" '{hosts: $hosts, runtimes: $runtimes}')"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'hosts=%s\nruntimes=%s\n' "$hosts" "$runtimes" >> "$GITHUB_OUTPUT"
fi
