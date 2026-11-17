# Skippy continuous integration

`ci.yml` runs the same commands for every pull request, main push, manual run and
reusable invocation. `CI` is the required summary. There is no path planner or
lighter PR profile. The only source override is the immutable bundle produced by
the protected-main llama upgrade workflow.

Ad hoc CI is a full run, not a reduced diagnostic profile. Launch it from the
Actions UI or with `gh workflow run ci.yml --ref main`. The workflow rejects a
manual source other than protected `main` before it requests any self-hosted
runner. Pilot runner policies must independently allow only
`.github/workflows/ci.yml@refs/heads/main`; the in-workflow check does not replace
that external admission boundary.

`targets.json` is the release matrix: four backend-neutral hosts and eleven
runtimes. CPU work uses GitHub-hosted `ubuntu-24.04`, `ubuntu-24.04-arm`,
`macos-15` and `windows-2025`. Metal and CUDA rows require the explicit ephemeral
hardware labels in that manifest. Compilation never counts as device evidence.

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
OS/architecture label, and `metal`, `cuda12` or `cuda13`. CUDA images carry the
exact toolkit from `targets.json` and a compatible driver/device. Toolkit-only
machines are insufficient. The large family battery additionally requests
`family-256g`; provision memory and device capacity for the largest admitted
model and concurrent baseline contexts, not merely the GGUF file size.

Quality and upstream candidate generation are the toolchain-specific exceptions
to ordinary hosted CPU work. They request an ephemeral Linux x86_64
builder from the repository-scoped `jy-rnr_vfnz8k5p4yrh334` runner group with
the container-only `jy-linux-docker` label,
LLVM/Clang 22 development and CMake packages, Ninja, mold, lld and
`SKIPPY_REWRITER_LLVM_PREFIX` configured. The container-only label prevents a
native Linux host runner in the same repository-scoped group from satisfying an
image-dependent job. Mold must pass the repository's active-target linker probe;
quality CI sets `SKIPPY_REQUIRE_MOLD=1` and pins `SKIPPY_MOLD=/usr/bin/mold`, so
it cannot silently fall back to lld or depend on a runner-specific job `PATH`.
The same generator tools must be available on full family builders. A standard
hosted image without those development packages cannot run these gates; no job
downloads system packages to disguise that prerequisite.

Qwen3 dense smoke preparation is explicit: `just automation-run ci model-fetch`
resolves the immutable public model URL and verifies the family manifest's size
and SHA-256. A runner-provided `SKIPPY_CI_MODEL` is verified without downloading.
This command sets the path for subsequent Actions steps; locally export the path
before running `just ci-smoke`. Default `just test` does not download models.

Every package smoke executes the bundled CLI and runtime, submits an actual
OpenAI completion, checks generated-token usage, and requires observed positive
GPU layer offload for Metal/CUDA. Shutdown must be graceful. Product artifacts
include logs and source-bound smoke evidence. A missing device, model or required
row fails rather than passing as a skip.

## Artifact flow

1. `ci host` builds each CLI once, verifies its imports, records its source and
   checksums, and archives executable permissions.
2. `ci runtime` builds the selected native variant and verifies its manifest and
   dependency closure. The artifact is bound to the source, target and variant.
3. `ci package` verifies and composes those producer archives without compiling,
   then runs the bundled CLI on the target's hosted CPU machine. CUDA bundle
   startup is therefore checked without depending on a hardware runner's driver.
4. `ci smoke` receives that exact archive, admits the pinned model and runs the
   packaged server on its required CPU/Metal/CUDA device.
5. `ci summary` accepts only successful plan, quality, host, runtime and product
   and smoke jobs. Failed, cancelled, absent and skipped required jobs all fail
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
