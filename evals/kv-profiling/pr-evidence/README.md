# PR #233 qualification evidence

The [PR description](https://github.com/Mesh-LLM/skippy/pull/233) reports
four historical model tables and the current-source performance status.
The registered [performance protocol](../SCHEDULER_PERFORMANCE_PROTOCOL.json)
sets the current claim and limits.

The [history archive](scheduler-pr233-history.tar.xz) preserves original
campaign plans, 6,176 request timing rows, audits, failed runs, and earlier
task-quality experiments. Its [index](scheduler-pr233-history-index.json)
records every member's path, byte length and SHA256, plus the archive hash
and source commit. The archive contains regular relative files only.

One-off profiling and task-quality source files removed from this PR have
verified ignored local backups under `target/scheduler-fixes/backups/` and
remain recoverable from the earlier immutable PR commits. The backup is
local to the contributor workspace, not a PR artifact.
