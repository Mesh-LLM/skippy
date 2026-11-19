#!/usr/bin/env bash
set -euo pipefail

stage="${1:?usage: ci-gate.sh summary|runtime|smoke}"
needs="${SKIPPY_CI_NEEDS:?SKIPPY_CI_NEEDS is required}"

case "$stage" in
  summary)
    profile="${SKIPPY_CI_PROFILE-full}"
    case "$profile" in pr|full) ;; *) echo 'CI profile must be pr or full' >&2; exit 1 ;; esac
    if ! jq -es --arg profile "$profile" '
      ["plan", "inference_contracts", "quality", "host", "runtime", "product", "smoke"] as $required
      | if length == 1 then .[0] else error("expected one needs object") end
      | . as $needs
      | type == "object"
      and (keys == ($required | sort))
      and all($required[]; . as $job | $needs[$job].result ==
          (if $profile == "pr" and ($job == "host" or $job == "runtime" or $job == "product" or $job == "smoke")
           then "skipped" else "success" end))
    ' <<<"$needs" >/dev/null; then
      echo "CI results do not satisfy the $profile profile" >&2
      exit 1
    fi
    ;;
  runtime|smoke)
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    plan="$(GITHUB_OUTPUT='' bash "$root/scripts/ci-plan.sh")"
    expected="$(jq -c --arg stage "$stage" '["plan"] + [.runtimes.include[].id | "\($stage)_\(gsub("-"; "_"))"] | sort' <<<"$plan")"
    if ! jq -es --argjson expected "$expected" '
      if length == 1 then .[0] else error("expected one needs object") end
      | . as $needs | type == "object" and (keys == $expected)
      and all($expected[]; $needs[.].result == "success")
    ' <<<"$needs" >/dev/null; then
      echo "$stage gate requires every active runtime from ci/targets.json" >&2
      exit 1
    fi
    ;;
  *) echo 'usage: ci-gate.sh summary|runtime|smoke' >&2; exit 2 ;;
esac
