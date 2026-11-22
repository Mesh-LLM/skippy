#!/usr/bin/env bash
set -euo pipefail

phase="${1:?expected seed or read}"
case "$phase" in
  seed) mode=READ_WRITE; hits=0; writes=1 ;;
  read) mode=READ_ONLY; hits=1; writes=0 ;;
  *) echo "Unknown sccache smoke phase $phase" >&2; exit 1 ;;
esac

if [[ "$SCCACHE_GHA_RW_MODE" != "$mode" ]]; then
  echo "Expected sccache mode $mode, got $SCCACHE_GHA_RW_MODE" >&2
  exit 1
fi

work_dir="$GITHUB_WORKSPACE/target/sccache-smoke"
mkdir -p "$work_dir"
cd "$work_dir"

compile() {
  printf 'int skippy_sccache_smoke(void) { return %s; }\n' "$1" > smoke.c
  "$SCCACHE" cc -c smoke.c -o smoke.o
  test -s smoke.o
}

compile 1
if [[ "$phase" == read ]]; then
  # This variant was never compiled by the seed job.
  compile 2
fi

stats=$("$SCCACHE" --show-stats --stats-format json | jq '{
  remote: (.cache_location | startswith("ghac,")),
  hits: (.stats.cache_hits.counts | add // 0),
  misses: (.stats.cache_misses.counts | add // 0),
  writes: .stats.cache_writes,
  read_errors: .stats.cache_read_errors,
  write_errors: .stats.cache_write_errors
}')
echo "$stats"
jq -e --argjson hits "$hits" --argjson writes "$writes" \
  '.remote and .hits == $hits and .misses == 1 and .writes == $writes
   and .read_errors == 0 and .write_errors == 0' <<< "$stats" >/dev/null
