# PR #233 qualification evidence

The [PR description](https://github.com/Mesh-LLM/skippy/pull/233) has four
historical per-model tables. The current [status](../SCHEDULER_FIX_PROGRESS.md)
separates measured fixes from pending quality, numerical and timing gates.

The [history archive](scheduler-pr233-history.tar.xz) preserves the original
campaign plans, 6,176 request timing rows, cell metrics, raw audit reports,
old task-quality protocol/fixture and detailed progress diary. The
[index](scheduler-pr233-history-index.json) records each member's path,
byte length and SHA256 plus the archive SHA256 and immutable source commit.
It contains regular relative files only. Individual earlier immutable-commit
links in the PR description remain valid.

The compact version 3 prompt archives and first-cell/repeat evidence archives
remain as separate files here for direct review. The durable scorer and
materializer remain under `scripts/`. Completed one-off profiling scripts
were removed from the PR; their bytes are in the ignored local backup at
`target/scheduler-fixes/backups/pr233-full-review-material-20261128.tar.xz`
(SHA256 `fed99a21bc7c5f8aa2bc5e29569f9409506c10e13bcbb1029aa4ae69b9dfea02`,
122 files) and in immutable commit `82a50a1e`. The backup is not a PR artifact.
