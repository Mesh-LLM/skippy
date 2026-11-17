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
- `ci/targets.json` owns the four host targets and eleven runtime variants.
  Keep CLI builds backend-neutral and build each host once. Package by copying
  exact verified producer outputs; consumers must not rebuild missing inputs.
- Keep policy and orchestration in typed Rust under `tools/xtask`. Just recipes
  and platform adapters are thin. Do not add Python or generic shell policy.
- All builds use Just. Run Cargo serially within one checkout. Include both
  Rust workspaces, full crate tests, FFI contracts, and native API contracts.
- Ordinary tests are offline. Model admission is explicit and digest-bound.
  GPU qualification requires a real matching device; missing fixtures, devices,
  capacity, or evidence fail or queue, never become passing skips.
- PR hardware runs require isolated ephemeral runners. Runner groups must be
  limited to this repository, with no release credentials or privileged network
  access. Never execute PR source on a persistent privileged machine.
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
