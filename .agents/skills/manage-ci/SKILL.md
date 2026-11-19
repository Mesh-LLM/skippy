---
name: manage-ci
description: Maintain standalone Skippy CI, native hardware qualification, llama upgrades, and tagged releases.
---

# Skippy CI ownership

Read this file before inspecting or changing CI. This is the standalone Skippy
contract; no downstream product's workflow topology applies here.

- `.github/workflows/pr-checks.yml` owns the single intended required `PR checks`
  aggregate on a standard GitHub-hosted Ubuntu runner. Protected-main code reads
  workflow/check/status results without executing candidate code or using lab
  credentials. Failures/cancellations fail it; pending or missing applicable work
  stays pending; success/neutral/intentional skips pass. Producer starts and
  completions re-evaluate reruns; latest exact-head results govern. Add new PR
  producer workflows to its event list. Activate the one-check branch rule only
  after the controller reaches main and its real aggregate is verified; preserve
  the previous two required checks until then, and retain all review protections.

- `.github/workflows/ci.yml` is the single implementation with explicit `pr`
  and `full` profiles. Declarative paths in `.github/path-filters.yml` select
  code checks and reusable PR documentation validation; a lightweight result gate
  always reports (`CI` for short runs, `Full CI gate` for full runs) and rejects
  failed selection or missing selected results.
  Code changes run formatting, Clippy, unit/API tests, FFI freshness and
  repository contracts. The separate protected inference workflow uses the same
  path filters from protected main and runs the
  seven short serving configurations for relevant trusted PRs. Nightly,
  release and reusable candidate validation use `full`, adding the complete
  native battery, host/runtime producers, packaging and hardware smoke. A short
  summary accepts only its explicitly skipped extended stages; a full summary
  requires every stage to succeed.
  The full-profile Runtime test summary uses `jq` and the already validated
  plan matrix; it requires every expected smoke result without checkout or Rust setup.
- `.github/workflows/docs.yml` owns documentation validation. Docs pull requests
  build without credentials or publication, while protected-main docs changes
  deploy the validated output to the beta site. PR CI calls this reusable workflow
  for `docs-site/**` changes and waits for its uniquely named documentation job.
  Release validation calls the same workflow to create the production
  documentation artifact.
- `.github/workflows/ci-image.yml` owns the standalone `skippy-ci-cpu` dependency
  image. PRs build and qualify offline without publication credentials; main
  pushes and manual main dispatches publish the exact tested artifact in a
  separate job with packages write. Only a fully qualified image advances
  `latest`; retain its immutable build tag and unique cache identity. Bootstrap
  the publisher before changing CI consumers. ROCm/CUDA variants remain separate
  image names and must not inherit CPU qualification as device evidence.
- `.github/workflows/release.yml` starts only on version-tag pushes and calls
  both validation implementations. A release never edits source, bumps
  versions, moves a tag, or publishes an incomplete artifact matrix.
- `.github/workflows/llama-upgrade.yml` keeps control on protected main, prepares
  an immutable candidate, certifies it, independently repeats certification,
  and opens an ordinary PR. Failed or incomplete qualification cannot publish.
- `ci/targets.json` owns four host definitions and eleven runtime definitions.
  `enabled_hosts` selects the active CI and release roster. Windows is temporarily
  deferred; the active roster has three hosts and eight runtimes. Do not publish
  artifacts for disabled hosts or describe those hosts as qualified.
  Keep CLI builds backend-neutral and build each host once. Package by copying
  exact verified producer outputs; consumers must not rebuild missing inputs.
- Keep workflow path routing declarative in YAML. Build and qualification
  commands remain typed Rust under `tools/xtask`; Just recipes and platform
  adapters stay thin. PR lab-result comments use the focused Bash/gh/jq report
  script without Rust setup. Prefer shell for small CI checks, with Python as
  a fallback when structured parsing needs it.
- All builds use Just. Run Cargo serially within one checkout. Include both
  Rust workspaces, full crate tests, FFI contracts, and native API contracts.
- Ordinary tests are offline. Model admission is explicit and digest-bound.
  GPU qualification requires a real matching device; missing fixtures, devices,
  capacity, or evidence fail or queue, never become passing skips.
- Split admission unit tests use explicit recipe-bound fixtures; a native source
  change does not renew the bundled certification roster. PR split smoke opts
  into experimental planning and retains the actual admission status. It still
  requires stage/frontier validation and all GPU/response checks. Nightly and
  release keep certified admission; PR smoke never renews release certificates.
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
- The protected inference results-comment job alone receives pull-requests write
  permission for PR comments, plus read-only contents/actions. It receives no
  native dispatch credential and never executes candidate code.
- Cache keys bind source recipe, lockfile, toolchain, platform, and backend.
  Caches are accelerators, not artifact correctness or qualification evidence.
  Restore Cargo and compiler caches on native runners as well as hosted runners;
  image-provided tooling does not make fresh containers retain compiler outputs.
- Cancel superseded PR validation only. Release and upgrade concurrency must
  retain evidence and never cancel another release or an independent CI run.
- The code workflow's `CI` summary rejects failed, cancelled, or missing selected jobs;
  `PR checks` additionally covers the separate inference and image workflows.
  Upload diagnostic evidence on failure before cleanup.
- Runtime metadata includes the source SHA, llama pin, patch digest, ABI,
  architecture, backend, toolkit and file hashes. Signing, if configured, runs
  before final hashes. Preserve licenses and CUDA redistribution notices.

Before editing, inspect the affected workflows, Just commands, Rust owners,
target matrix and `ci/README.md`. Validate syntax, matrix consistency, relevant
Rust tests and the actual Just commands. Report unexecuted hardware checks and
unverified external runner/repository configuration explicitly. Never describe
workflow syntax validation as a successful build or hardware qualification.
