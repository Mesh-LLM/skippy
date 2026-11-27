# Completed scheduler diagnostics

These 66 scripts are the exact source snapshots of completed, one-off scheduler investigations. Their original byte hashes are in [MANIFEST.json](MANIFEST.json). They are outside the active PR audit and qualification entry points; the active scripts remain directly under `scripts/`.

A sealed historical job may still refer to its original `scripts/profile-scheduler-*` path. For an exact replay, use that job's sealed payload or restore the recorded source at its original path in a scratch checkout. Moving a source snapshot here does not change its historical result or qualify a current binary.

The source and artifact records for completed investigations are in `evals/kv-profiling/SCHEDULER_FIX_PROGRESS.md` and `target/scheduler-fixes/campaign.json`. Do not treat this directory as a current test suite.
