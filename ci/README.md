# Skippy continuous integration

`ci.yml` has explicit `pr` and `full` profiles. `CI` is the required short-profile
summary; extended runs report `Full qualification`, so an optional full run on a
PR commit does not replace its short merge gate. Release callers require the
full workflow result independently of the check name. Docs-only pull requests
and main pushes are excluded from `ci.yml`; all other pull requests and main
pushes use the short profile for repository contracts, formatting/Clippy, unit
and C API tests, and FFI binding freshness. Relevant trusted PRs also run the
protected seven-configuration inference smoke workflow.
Its separate `Inference qualification` status explicitly reports when no serving
inputs changed; required smoke jobs cannot pass by being skipped.
PR admission checks every changed-file page and both paths of a rename against
immutable merge-base and head objects. It reads those objects in a temporary bare
Git repository; it never checks out or runs candidate code to make this decision.
It also rejects a PR head that changes while admission is collecting inputs.

GPU qualification requires a change to the native Linux ARM64 serving workload:
production Rust modules in the CLI's non-development dependency graph, their
embedded assets and build inputs, native sources and patch queues, native build
recipes, or the model/quality/smoke workload. Rust syntax comparisons remove
proven test-only and other-platform items and ignore comments and formatting,
while retaining string literals and unknown feature conditions. Separate test
modules are resolved from production module declarations rather than filename
conventions. An embedded runtime asset still counts if its filename looks like
documentation.

Shared configuration is compared by its consumed content: native Just recipes
and their recipe dependencies/exports, production Cargo dependency declarations
and lockfile closure, native development profiles and dependency overrides, and
the two PR native templates. Unrelated recipes, development dependencies,
standalone tools, offline unit tests, admission policy, dispatch, reporting,
workflow wiring, and publication automation do not dispatch GPU jobs. These
changes retain normal CI and inference contract tests. Changes to the candidate
worker's execution, GPU checks, smoke checks or split preparation still require
qualification. The reviewed candidate-side xtask dependency list must be updated
when these worker helpers acquire a new external dependency.

Admission logs the files and reasons that require qualification and the files
whose production content is unchanged or outside the workload. The controller
still publishes the source-bound status for irrelevant PRs. Invalid source,
unresolvable modules/assets/dependencies, or an unreadable or potentially
truncated file list fail admission; they never become passing skips. Scheduled,
manual, and release qualification remain explicit and do not use the PR filter.

Replay the decision offline using two full commit SHAs already present locally:

```sh
just inference-inputs BASE_SHA HEAD_SHA target/ci-evidence/inference-inputs.json
```

This writes source-bound reasons and exclusions without dispatching native jobs.
The protected controller uses the policy on `main`; a policy PR's own workflow
continues to use the previous policy until the PR is merged.
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

The CPU-only `quality` job runs on GitHub-hosted `ubuntu-24.04` inside
`ghcr.io/general-inference/skippy-ci-cpu:latest`, keeping lab capacity available
for device qualification. The image supplies the pinned Rust toolchain, Just,
sccache, build-essential, mold and LLVM/Clang 22 development inputs. The job
verifies its tools before running the existing checks and pulls the private
image with its own `GITHUB_TOKEN` using `packages: read`.

Cargo and CMake compilation use two workers to fit standard private-repository
hosted capacity. Cargo build snapshots, protected admission snapshots and
sccache include `/etc/skippy-ci-image-id`, so updates to `latest` use a new
compiled-output namespace. Cargo downloads use separate container cache scopes
to avoid restoring archives made with hosted-VM home/workspace paths. Native
source caches remain shared. Protected candidate admission precedes build-cache
restoration and compiler-cache credential export.
Metal/CUDA runtime and model inference jobs retain their dedicated lab routing.

`docs.yml` runs one documentation job when `docs-site/**` changes. On a pull
request it installs the lockfile, builds Nextra, generates the Pagefind index and
retains `docs-site/out`, but receives no publication credentials and does not
deploy. The pull-request job reports the required `CI` check because GitHub
leaves a path-filtered required workflow pending unless another job supplies
that check. After the change reaches protected `main`, the same workflow builds
and deploys the output to `beta.skippy.sh` with `just docs-publish-beta`.

Mixed source and documentation changes run both documentation validation and
the normal short CI workflow. Release validation calls `docs.yml` directly to
create the exact `release-docs-site` artifact.

For a version tag, the release workflow downloads that exact
`release-docs-site` artifact after validation and GitHub release publication,
then deploys it to `skippy.sh` with `just docs-publish`. The publication job
does not rebuild the site. It installs the lockfile-pinned `cf` CLI and exposes
`CLOUDFLARE_API_TOKEN` only to the deploy step.

Configure `SKIPPY_DOCS_CLOUDFLARE_API_TOKEN` as a GitHub Actions repository
secret. Create it from Cloudflare's **Edit Cloudflare Workers** token template
and scope it to the account that owns the `skippy-docs`, `skippy-docs-beta`,
`skippy.sh` and `beta.skippy.sh` resources. The workflow maps it to the
`CLOUDFLARE_API_TOKEN` environment variable expected by `cf` only in the
protected-main beta deploy step and the tagged-release production deploy step.
Pull requests, documentation builds and shared release validation do not
receive this secret.

## Build prerequisites

### CPU dependency image

`ci/Dockerfile` builds the x86_64 CPU dependency image
`ghcr.io/general-inference/skippy-ci-cpu:latest`. It includes **build-essential
and mold**, LLVM/Clang 22 development packages (including `libclang-cpp22-dev`),
libc++/libc++abi 22, OpenMP and sanitizers, Rust/Clippy/rustfmt
from `rust-toolchain.toml`, Just, sccache, CMake, Ninja and system libraries.
The Ubuntu base is digest-pinned and release binaries are checksum-verified.
The image runs as uid 1001 to match GitHub-hosted workspace ownership. Cargo uses
its default `$HOME/.cargo` directory so the existing dependency cache follows the
job's home directory. Preinstalled Rust toolchains live at `/opt/rust/rustup`,
outside GitHub's fresh `/github/home` mount.
Cargo's Rust wrapper and CMake's C/C++ compiler launchers use the installed
sccache by default. The repository's Linux Rust linker driver selects mold.

Use `just ci-image-build` and `just ci-image-smoke` locally. The Docker build
compiles and runs Rust with mold and sanitized C++23/OpenMP with both libstdc++
and libc++ using lld without networking;
the smoke command checks the installed tools in a read-only, offline container
with a fresh `/github/home` mount, as used by GitHub container jobs.
`just ci-image-export` saves the image to `target/ci-image/skippy-ci-cpu.tar`.

One workflow, `.github/workflows/ci-image.yml`, builds and tests relevant PRs.
Main pushes and manual main dispatches publish the exact tested image in a
separate job with package-write permission. The publisher checks the image ID
and source label, then pushes an immutable build tag and `latest` without
rebuilding or running the image. Run manual refreshes with
`gh workflow run ci-image.yml --ref main` after the workflow lands.

This PR only bootstraps the image; consumer adoption follows after publication.
Future consumers use `latest` and include `/etc/skippy-ci-image-id` in build-cache
keys. Private same-repository pulls use `GITHUB_TOKEN` with `packages: read`.
Future GPU images can use `skippy-ci-rocm` and `skippy-ci-cuda`.

### Source and build caches

Cargo download caches are shared by jobs with compatible dependency sets on the
same OS and keyed by both workspace lockfiles. Tools, quality, host producers and
upgrade builders have separate download scopes so a tools-only first writer
cannot freeze an incomplete product dependency cache. Cargo build snapshots have
a compatibility prefix binding the OS, architecture, job role/backend, profile
debug/incremental settings, toolchain, both lockfiles and native build recipe,
followed by the actual checked-out source SHA. A new revision restores
the latest compatible snapshot and saves updated outputs after success. A rerun
of the same revision can reuse the exact snapshot without another upload. Snapshot
retention and eviction follow the repository's Actions cache settings. Cargo still
validates and rebuilds changed source; caches are not qualification evidence.

Small command-runner jobs cache only `target/debug`. Quality additionally caches
the Rust facade's debug outputs; host producers cache `target/release` as well as
their command runner. Candidate admission uses a separate
`target/ci-admission/debug` cache before source restoration, then build caches
are selected from the admitted checkout. Cache paths exclude native build trees,
packages and qualification evidence. Dependency downloads are separate from
compiled outputs, and the GitHub-backed sccache is not redundantly archived as a
local disk cache.

Native jobs separately cache prepared llama.cpp sources by upstream pin, complete
ordered patch queues and preparation recipe, and whisper.cpp sources by their
pin and preparation recipe. Existing preparation commands still validate or
reapply the selected inputs on every run. Explicit source overrides bypass the
corresponding source cache. Smoke jobs cache the immutable Qwen fixture using its
admission manifest and fetch recipe; model admission always verifies its size and
SHA-256, even after a cache hit. A supplied `SKIPPY_CI_MODEL` bypasses that cache.

The upgrade candidate and both supported-family passes restore command-runner
and compiler caches. Family passes may also reuse prepared sources. Each pass
still requires absent `target/llama-upgrade/native`,
`target/llama-upgrade/cargo` and evidence directories, builds independently and
executes the full qualification battery; those directories are never cached.

The hosted setup action installs full-SHA-pinned Just 1.58.0. The documentation
job additionally selects Node.js 22.23.0. Both hosted and native runners restore
Cargo dependencies, debug artifacts and bounded
sccache compiler results using the shared cache action. Rust follows
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
   then runs the bundled CLI on the target's hosted CPU machine. CUDA bundle
   startup is therefore checked without depending on a hardware runner's driver.
4. `ci smoke` receives that exact archive, admits the pinned model and runs the
   packaged server on its required CPU/Metal/CUDA device.
5. `ci summary` requires plan, inference contracts and quality in short CI. Full
   CI also requires every host, runtime, product and smoke result. Failed,
   cancelled, absent and unexpectedly skipped required jobs all fail the
   summary. Docs-only changes do not start this workflow.

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
