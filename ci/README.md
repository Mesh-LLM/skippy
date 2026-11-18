# Skippy continuous integration

`ci.yml` has explicit `pr` and `full` profiles. `CI` is the required short-profile
summary; extended runs report `Full qualification`, so an optional full run on a
PR commit does not replace its short merge gate. Release callers require the
full workflow result independently of the check name.
Pull requests and main pushes use the short profile: repository contracts,
formatting/Clippy, unit and C API tests, and FFI binding freshness. Relevant trusted
PRs also run the protected seven-configuration inference smoke workflow.
Its separate `Inference qualification` status explicitly reports when no serving
inputs changed; required smoke jobs cannot pass by being skipped.
The 308-test native family battery and complete host/runtime/package/smoke matrix
run nightly and for releases. Reusable candidate validation defaults to `full`;
release publication explicitly requests it. Manual dispatch from protected main selects either
profile, defaulting to `full`. Both nightly workflows run at 14:00 UTC.
Full runs can build CUDA for the assigned GPU; release validation explicitly
selects the complete architecture lists from `targets.json`. The only source override is the immutable bundle produced by the protected-main
llama upgrade workflow.

`targets.json` defines four backend-neutral hosts and eleven runtimes.
`enabled_hosts` currently activates Linux and macOS only: three hosts and eight
runtimes. Windows qualification and publication are temporarily deferred at the
maintainer's request. Re-enable its retained definitions by enabling
its host/runtime definitions and adding `windows-x86_64` to `enabled_hosts`. CPU work uses GitHub-hosted
`ubuntu-24.04`, `ubuntu-24.04-arm` and `macos-15`. Metal and CUDA rows require the explicit ephemeral
hardware labels and JY runner group in that manifest. JY uses the requested group
to attribute queued Actions jobs and place capacity; labels alone are insufficient.
Hardware build and smoke jobs declare literal groups and labels in the workflow:
JY cannot resolve a runner group hidden behind a matrix expression. Shared
composite actions retain one implementation of each contract. The runtime and
smoke aggregate gates require every active manifest row, rejecting missing,
skipped, cancelled or failed jobs in full qualification. The PR summary instead
requires those extended stages to be explicitly skipped; skipped unit or quality
checks still fail, and no GPU package qualification is claimed by a short run.
In full qualification, host and runtime producers start after planning alongside
quality; the final gate requires every producer and smoke result.
Compilation never counts as device evidence.

Tagged releases additionally bind admission and publication to the generic JY
Linux builder, and project hosted CI work onto the platform-specific JY groups.
Metal and CUDA jobs retain their exact hardware group and OS, architecture,
backend and `runtime-image` labels. Release validation therefore cannot fall
back to GitHub-hosted capacity or a different toolkit environment. The separate
inference qualification dispatches its source-bound model ladder to native JY
Sparks before publication.

## Build prerequisites

Build-cache keys stay stable across ordinary source commits and change with the
toolchain, lockfile and native build recipe. A matching cache avoids repeated
uploads; Cargo still validates and rebuilds changed source. Caches are not
qualification evidence.

The hosted setup action installs full-SHA-pinned Just 1.58.0. Both hosted and
native runners restore Cargo dependencies, debug artifacts and bounded sccache
compiler results using the shared cache action. Rust follows
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

The ordinary hardware runner jobs below use runners created for one job and
destroyed afterwards. Their runner group is repository-scoped; it must not
provide persistent host state, publication credentials, credentialed checkout,
or privileged network access to PR code. Required labels are `self-hosted`, `skippy-ephemeral`, the standard
OS/architecture label, and `metal`, `cuda12` or `cuda13`. CUDA runtime rows also require `runtime-image`, isolating exact-toolkit images
from family-certification runners. CUDA images carry the
exact toolkit from `targets.json` and a compatible driver/device. Toolkit-only
machines are insufficient. The large family battery additionally requests
`family-256g`; provision memory and device capacity for the largest admitted
model and concurrent baseline contexts, not merely the GGUF file size.

The JY inference ladder additionally permits trusted same-repository PRs from
verified repository collaborators to use native jobs on persistent dedicated
Sparks. Protected dispatch code verifies trust before sending a pinned source
SHA. Jobs use the standard JY account and its existing host permissions without publisher credentials,
fresh workspaces, read-only admitted models, exclusive machine reservations and
bounded leases. Clean up job processes and workspaces after every outcome;
quarantine an allocation whose cleanup fails. Fork and other untrusted PRs still
require isolated ephemeral execution. The `unconfined` capability is not itself
an admission failure; no separate job-account migration is required. Actions
only orchestrates these jobs, so the 6 GiB runner-container limit does not bound
model execution. Split jobs reserve both Sparks atomically. This policy does not
prove live JY configuration.

Quality and upstream candidate generation are the toolchain-specific exceptions
to ordinary hosted CPU work. Quality accepts either 64-bit Linux architecture;
candidate generation retains its Linux x86_64 lane. Both request a
`native-contracts` builder with LLVM/Clang 22 development and CMake packages,
Ninja, lld and `SKIPPY_REWRITER_LLVM_PREFIX` configured. The same generator tools
must be available on full family builders. A standard hosted image without those
development packages cannot run these gates; no job downloads system packages
to disguise that prerequisite.

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
   then runs the bundled CLI on a runner for the target platform. CUDA bundle
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
rejects a failed row. Then exercise a prerelease tag, verify all enabled bundles,
private authenticated downloads, clean installation and CPU/Metal/CUDA serving.
Exercise partial draft upload recovery and published-release overwrite refusal.
Only after source regeneration, both full candidate passes and exact-commit PR
publication work should the llama schedule be enabled.

No hosted/hardware capacity or secret configuration is implied by these files.
