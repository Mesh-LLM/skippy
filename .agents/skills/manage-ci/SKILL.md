---
name: manage-ci
description: Maintain standalone Skippy CI, native hardware qualification, llama upgrades, and tagged releases.
---

# Skippy CI ownership

Read this file before inspecting or changing CI. This is the standalone Skippy
contract; no downstream product's workflow topology applies here.

- `.github/workflows/ci.yml` is the single implementation for pull requests,
  main pushes, and reusable validation. Every invocation runs the same quality,
  host, native-runtime, packaging, and hardware-smoke contracts.
- `.github/workflows/release.yml` starts only on version-tag pushes and calls
  that CI implementation. A release never edits source, bumps versions, moves
  a tag, or publishes an incomplete artifact matrix.
- `.github/workflows/llama-upgrade.yml` keeps control on protected main, prepares
  an immutable candidate, certifies it, independently repeats certification,
  and opens an ordinary PR. Failed or incomplete qualification cannot publish.
- `ci/targets.json` owns four host definitions and eleven runtime definitions.
  `enabled_hosts` selects the active CI and release roster. Windows is temporarily
  deferred; the active roster has three hosts and eight runtimes. Do not publish
  artifacts for disabled hosts or describe those hosts as qualified.
  Keep CLI builds backend-neutral and build each host once. Package by copying
  exact verified producer outputs; consumers must not rebuild missing inputs.
- Keep policy and orchestration in typed Rust under `tools/xtask`. Just recipes
  and platform adapters are thin. Do not add Python or generic shell policy.
- All builds use Just. Run Cargo serially within one checkout. Include both
  Rust workspaces, full crate tests, FFI contracts, and native API contracts.
- Ordinary tests are offline. Model admission is explicit and digest-bound.
  GPU qualification requires a real matching device; missing fixtures, devices,
  capacity, or evidence fail or queue, never become passing skips.
- Trusted same-repository PRs from repository collaborators may run as native
  JY jobs on persistent dedicated lab hardware, including DGX Sparks. Admission
  must verify the source repository and contributor's collaborator status using
  GitHub; a branch name or same-repository check alone is not a trust decision.
  Dispatch and admission policy run from protected code, never from PR source.
  Fork and other untrusted PRs require isolated ephemeral execution and receive
  no native lab dispatch credentials.
- Trusted native PR jobs may use the standard JY account and its existing host
  permissions. Do not pass publication credentials or other jobs' secrets. Use a
  fresh source-pinned workspace, read-only admitted model inputs, exclusive JY
  machine reservations, bounded leases and process-tree/workspace cleanup on
  completion, failure and cancellation. JY's `unconfined` capability alone is
  not a rejection. No separate job-account migration is required.
  Cleanup failure quarantines the allocation until reconciled.
- Runner groups and dispatch credentials are repository-scoped. The Actions
  runner only orchestrates inference qualification; native JY jobs receive
  explicit model-sized resource budgets rather than the 6 GiB Actions-container
  limit. Split runs reserve both machines atomically. Publisher credentials
  remain separate from all build and inference jobs.
- Pin third-party actions by full commit SHA. Default permissions are read-only.
  Only the release publisher receives contents write; only the upgrade publisher
  receives a scoped App credential. Do not expose publisher secrets to builds.
- Cache keys bind source recipe, lockfile, toolchain, platform, and backend.
  Caches are accelerators, not artifact correctness or qualification evidence.
- Cancel superseded PR validation only. Release and upgrade concurrency must
  retain evidence and never cancel another release or an independent CI run.
- A stable `CI` summary rejects failed, cancelled, or missing required jobs.
  Upload diagnostic evidence on failure before cleanup.
- Runtime metadata includes the source SHA, llama pin, patch digest, ABI,
  architecture, backend, toolkit and file hashes. Signing, if configured, runs
  before final hashes. Preserve licenses and CUDA redistribution notices.

Before editing, inspect the affected workflows, Just commands, Rust owners,
target matrix and `ci/README.md`. Validate syntax, matrix consistency, relevant
Rust tests and the actual Just commands. Report unexecuted hardware checks and
unverified external runner/repository configuration explicitly. Never describe
workflow syntax validation as a successful build or hardware qualification.
