# Skippy continuous integration

`ci.yml` is the single implementation for every pull request, main push, manual
run and reusable invocation. `CI` is the required summary. Pull requests may skip
only the native graph-contract step when every changed path is classified as
independent of graph construction. Unclassified paths fail closed to running the
step. Main, tags, manual runs and reusable release/upgrade calls always run it.
The only source override is the immutable bundle produced by the protected-main
llama upgrade workflow.

The ephemeral JY preflight projects `targets.json` with `jq`; it does not compile the
Rust automation binary. A Linux CPU quality gate owns Rust lint and tests plus
the core/API CTest suite. Once that fast gate passes, host builds and a separate
conditional Linux CPU graph-contract job run in parallel. The graph job owns
binding and documentation drift, generated-patch replay, and native graph
contracts. Runtime matrix jobs explicitly disable those graph suites. They
compile one release runtime, compose it with its exact host producer, and run
packaged inference on the required device. Ordinary CI compiles CUDA only for
the compute capabilities reported by its assigned runner. Release validation
uses the same jobs with the complete architecture lists from `targets.json`.

Graph-sensitive pull-request inputs include the llama pin and patch queues,
native C++ facade and FFI, family manifests, native build scripts, and rewriter
or generator code. CI orchestration, packaging, documentation and ordinary Rust
changes do not rerun graph shapes. A missing base or head commit, an invalid
diff, or an unknown path runs the graph contracts.

Ad hoc CI is a full run, not a reduced diagnostic profile. Launch it from the
Actions UI or with `gh workflow run ci.yml --ref main`. Select `release` CUDA
architecture mode to rehearse the complete release architecture lists. The
workflow rejects a manual source other than protected `main`. Pilot runner
groups remain scoped to this repository; the in-workflow check does not replace
that external repository boundary.

`targets.json` records four backend-neutral hosts and eleven runtime targets.
Three hosts and eight runtimes are currently enabled for CI and release. All
Windows targets stay declared but disabled until Windows infrastructure is
provisioned. Preflight, CLI, CPU, Metal and CUDA work uses eligible ephemeral JY
runners. Metal and CUDA rows require the explicit hardware labels in that
manifest. Compilation never counts as device evidence.

## Build prerequisites

The hosted setup action installs only full-SHA-pinned Just 1.58.0. Rust follows
`rust-toolchain.toml`. Build agents must already provide Git, CMake, Ninja, a
native C/C++ toolchain, curl, and the platform linker selected by `scripts/`.
Linux runtime packaging also needs patchelf. Native binding/generator checks
need libclang and LLVM/Clang development headers and CMake packages; set
`SKIPPY_REWRITER_LLVM_PREFIX` to their installation. Native API quality checks
also need clang-format and clang-tidy. Windows requires Git Bash and the Visual
Studio C++ environment. The automation must fail when a required tool is
missing; it must not install system packages during a job.

Hosted image contents are external prerequisites, not guaranteed by a successful
workflow syntax check. If a hosted image lacks native development inputs,
provide a reviewed preprovisioned ephemeral image with the same command contract
before enabling the required check. Do not turn a missing tool into a skip.

Hardware runners must be created for one job and destroyed afterwards. Their
runner group is repository-scoped; it must not provide persistent host state,
publication credentials, credentialed checkout, or privileged network access to
PR code. Required labels are `self-hosted`, `skippy-ephemeral`, the standard
OS/architecture label, and `metal`, `cuda12` or `cuda13`. CUDA runtime-image
configs also carry `runtime-image`, preventing a family-certification runner
with the same backend from satisfying an exact-toolkit build. CUDA images carry
the exact toolkit from `targets.json` and a compatible driver/device. Toolkit-only
machines are insufficient. The large family battery additionally requests
`family-256g`; provision memory and device capacity for the largest admitted
model and concurrent baseline contexts, not merely the GGUF file size.

Preflight, quality, x86_64 CLI/CPU work and upstream candidate generation request
available ephemeral Linux
builder from the repository-scoped `jy-rnr_vfnz8k5p4yrh334` runner group with
the container-only `jy-linux-docker` label and the `MeshLLM (latest) · CPU`
environment, LLVM/Clang 22 development and CMake packages, Ninja and lld, with
`SKIPPY_REWRITER_LLVM_PREFIX` configured. The container-only label prevents a
native Linux host runner in the same repository-scoped group from satisfying an
image-dependent job. Mold remains an optional linker acceleration when the
runner image provides it and the active-target probe succeeds; it is not a CI
prerequisite. The same generator tools must be available on full family
builders. A standard hosted image without those development packages cannot run
these gates; no job downloads system packages to disguise that prerequisite.

Qwen3 dense smoke preparation is explicit: `just automation-run ci model-fetch`
resolves the immutable public model URL and verifies the family manifest's size
and SHA-256. A runner-provided `SKIPPY_CI_MODEL` is verified without downloading.
This command sets the path for subsequent Actions steps; locally export the path
before running `just ci-smoke`. Default `just test` does not download models.

Every package smoke executes the bundled CLI and runtime, submits an actual
OpenAI completion, checks generated-token usage, and requires a structured GPU
backend-device selection event for Metal/CUDA. Shutdown must be graceful.
Product artifacts include logs and source-bound smoke evidence. A missing
device, model or required row fails rather than passing as a skip.

## Artifact flow

1. Preflight validates and projects the four hosts and eleven runtimes without
   compiling repository automation.
2. The Linux CPU quality gate runs common semantic coverage once. Core/API tests
   configure with `SKIPPY_BUILD_LLAMA=OFF`.
3. When graph coverage is required, the graph job validates the canonical
   patched Linux CPU tree while host builds proceed independently. A classified
   non-graph pull request records an intentional skipped graph job.
4. `ci host` builds each CLI once, verifies its imports, records its source and
   checksums, and archives executable permissions.
5. Each runtime lane builds the selected native variant with graph tests disabled,
   verifies its manifest and dependency closure, downloads the matching host,
   composes the exact product, and runs Qwen3 on the required CPU/Metal/CUDA
   device. Ordinary CUDA lanes compile the assigned runner's detected compute
   capabilities. Release lanes compile and verify the complete checked-in
   architecture list. Compilation never substitutes for inference evidence.
6. The stable `CI` summary accepts successful preflight, quality, host and
   complete runtime-lane results plus either successful graph coverage or its
   classified path-based skip. Failed, cancelled or absent required phases fail
   the summary.

Native runtime manifests retain the upstream pin, patch digest, ABI and backend
metadata. Product provenance hashes the complete file roster. Actions artifacts
are same-run transfer inputs, never cross-run cache authority. CI has no
publication or signing secrets.

## Llama upgrades

`llama-upgrade.yml` runs from protected main. The controller resolves a release
or explicit immutable upstream SHA and exports a digest-bound Git bundle. Both
CI passes and both family passes restore the same candidate. The CPU, Metal and
CUDA family jobs consume immutable offline model caches and retain per-pass
evidence. They receive no GitHub write credential.

Only the publisher uses `SKIPPY_UPGRADE_APP_ID` and
`SKIPPY_UPGRADE_APP_PRIVATE_KEY`, with repository-scoped Contents and Pull
requests write permissions. It pushes only the verified candidate and opens a
normal PR; it does not merge, tag or release. App authentication ensures the PR
starts ordinary CI.

The daily cron is intentionally disabled until the unchanged-pin and real
changed-pin acceptance runs succeed. The controller/family migration must be
completed and validated before enabling it; the retained shell battery must not
be treated as working merely because its script is present. Missing native
commands, regeneration, family receipts or publisher admission are hard failures.

The retained family manifest includes dense, MoE, Next, VL, VL MoE and TTS Qwen3
fixtures, plus the broader supported family roster. Preserve declared
profile-specific oracles and all pinned sidecars. The OCR image's legacy filename
and label are fixture compatibility data, not a downstream product dependency.

## Bring-up evidence

Before requiring CI, execute a complete PR and main run and prove the summary
rejects a failed row. Then exercise a prerelease tag, verify all eleven bundles,
private authenticated downloads, clean installation and CPU/Metal/CUDA serving.
Exercise partial draft upload recovery and published-release overwrite refusal.
Only after source regeneration, both full candidate passes and exact-commit PR
publication work should the llama schedule be enabled.

No hosted/hardware capacity or secret configuration is implied by these files.
