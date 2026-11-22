# Skippy continuous integration

`ci.yml` reports `CI` for code checks and inference PR package smoke, and
`Full CI gate` for the complete package roster and Graph Contracts. Release callers
require the full workflow result independently
of the check name. The single required status is `PR checks`, the
aggregate of all applicable PR workflows, checks and statuses.

`pr-checks.yml` evaluates that aggregate on the selected Ubuntu runner, using
protected-main code and no Rust setup, candidate
execution, model inputs or lab credentials. It re-evaluates on PR events and
producer workflow starts/completions, including reruns. Failed, cancelled,
timed-out and startup-failed runs fail the gate; queued/running or missing
applicable workflows leave it pending. Successful, neutral and intentionally
skipped results are accepted. Results bind to the current PR head; older runs
and attempts cannot override newer results. It includes additional automatic PR
workflows and independent checks/statuses, rather than requiring their names
in branch protection. The aggregate reads the native PR trigger paths for CI,
inference, documentation and images from the protected workflow files. Missing
matching producers keep it pending even before GitHub schedules their runs.
New PR producers must also be added to the `workflow_run` trigger list so their
reruns re-evaluate the aggregate.

The controller reports pending and exits while producers work; it does not
hold a hosted runner open waiting for native hardware. Repository `GITHUB_TOKEN`
status updates cannot recursively trigger the controller.

`ci.yml` uses native paths to start on code or inference PRs and on code pushes
to `main`. Once a PR run starts, a pinned `dorny/paths-filter` action reads
`.github/path-filters.yml` to choose code and package jobs independently. It uses
`some-with-excludes`, so a changed file must match an inclusion and no exclusion.
Mixed PRs run every applicable check group.
These are file-level rules. Inline tests in production files, shared Cargo
manifests/lockfiles, and any `Justfile` edit still count as changes to those files.
Separate test files/directories and standalone tools are excluded from GPU
qualification; `.gi/templates.yaml` is exact and `.gi/agents/**` is excluded.

Code paths select Quality canary, which runs CI automation tests before
formatting, Clippy, FFI, and Rust and C++ tests. CI reads PR path rules from the
base commit. PRs matching the inference filter also require Linux x64 CUDA 13 and
macOS ARM64 Metal packages to build and serve a short inference request on real
hardware. Both jobs wait for Quality canary when code checks apply. Docs-only
and metadata-only PRs do not start CI. The CI result gate rejects missing,
failed or unexpectedly skipped results.

The protected inference workflow uses native `pull_request_target` path filters
for the same inference inputs. PR qualification is manual: it runs only when
the PR carries the `qualify` label, and the workflow still triggers on path
matches so the intentionally skipped run satisfies the `PR checks` aggregate.
Manual qualification may queue behind another PR's atomic Spark pair; the PR
budget waits for placement instead of failing fast. Relevant PRs retain
same-repository/collaborator admission, a fresh-head check and all seven GPU
smoke configurations. The credentialed controller runs only protected source.
Python admission verifies trust and source identity.
The syntax/dependency classifier and offline replay command have been removed.
The lab-results comment uses `scripts/inference-report.sh` with hosted-runner
`gh` and `jq`; it needs no Rust setup or compilation. It checks receipt source,
configuration and native-attempt identity, updates only its bot-owned comment,
and refuses to replace results for a newer commit or workflow attempt.

The shared report renderer takes normalized qualification JSON. It renders
Mermaid charts for Actions summaries and saves the Hugging Face card’s PNG
charts in `charts/` beside its README. PNG export uses the reporting
requirements. The Actions summary renderer uses the Python standard library.

```sh
examples=docs/design/inference-qualification-examples
uv run --python 3.12 --with-requirements ci/inference-report-requirements.txt \
  python scripts/inference-render-report.py dataset-card "$examples/examples.json" \
  --output "$examples/README.md"
just inference-render-report summary "$examples/examples.json" --run pr-pass
```

The scheduled inference workflow runs the twelve nightly AgentX configurations
and the seven PR-profile smoke configurations. The smoke runs use the same
harness as labeled PRs. PR smoke, nightly, and release qualification allow
experimental split admission, retain the actual admission status, and require
the full checks for their profile.

The protected scheduled publisher reads the current public dataset revision,
compares verified measurements directly with its saved baselines, and uploads
aggregate Parquet results, the baseline manifest, and the generated card in
one revision-checked commit to
[the public qualification dataset](https://huggingface.co/datasets/meshllm/skippy-inference-qualification).
The initial tolerance and promotion threshold are 5% per performance metric.
A changed benchmark recipe seeds its own reference. Passing measurements are
recorded while the saved reference remains in force until a new source exceeds
the improvement threshold. Regressions and incomplete evidence fail nightly
after publication and preserve the affected baseline.

The scheduled publisher uses the existing `HF_TOKEN` Actions secret. It needs
write access to this Hugging Face dataset. Only the publisher step receives
the token. Public receipts contain aggregate results, source and workload
identities, and verdicts. Native logs, lab receipts, and native access
credentials stay in their existing systems.

Scheduled, manual and reusable full/candidate validation bypass path selection.
Tagged releases and manual llama upgrades retain their explicit triggers. The
`CI images` workflow retains its native paths for its workflow,
`ci/Dockerfile*`, `ci/targets.json`, `rust-toolchain.toml` and `Justfile`.
Protected inference and aggregate workflow changes take effect after they
reach `main`.
Nightly, reusable candidate validation and tagged releases use the full profile.
Manual dispatch from protected main defaults to full. Both nightly workflows run
at 14:00 UTC. Release validation uses the complete CUDA architecture lists from
`targets.json`. Full runs may instead use the assigned GPU's native architecture
for the Linux x64 CUDA 13 device job. Compiler-only CUDA builds always use the
manifest architecture lists. The only source override is the immutable bundle
produced by the protected-main llama upgrade workflow.

`targets.json` defines four hosts and eleven runtimes. Three hosts and eight
runtimes are active. Windows qualification and publication are deferred. After
the quality and automation canary, each CPU job builds, packages and tests its
runtime on the selected Ubuntu x64, Ubuntu ARM64, or macOS ARM64 runner. Each
also produces the shared CLI for its platform. Linux x64 builds the shared CLI
on Ubuntu 24.04 for compatibility with the CUDA toolchain images. Each backend
job waits for its matching CPU job only. Linux x64 CUDA 12 and Linux ARM64
CUDA 12 and 13 run on Ubuntu in the corresponding compiler images
and check the packaged CLI and runtime artifact without a device. Linux x64
CUDA 13 and macOS Metal run device smoke on ephemeral JY runners. Tagged
releases add device smoke for the other three CUDA packages using their exact
archives. JY hardware jobs declare literal runner groups and labels. The full
CI gate matches all package jobs to the plan and checks required device results.

### Depot runners

The repository Actions variable `SKIPPY_DEPOT_RUNNERS_ENABLED=true` selects
Depot for every job that otherwise uses a GitHub-hosted runner. This includes
PR validation and its aggregate, code and package CI, compiler images and
publishing, documentation and deployment, release validation and publication,
the inference controllers, and llama upgrade preparation and publication. Most
Linux jobs use four-core Ubuntu 24.04 runners of the existing architecture.
Graph Contracts and llama upgrade preparation use eight-core x64 runners. The
macOS CPU package uses Depot's macOS 15 ARM64 runner. Device jobs use their
specified JY groups. Clearing the variable or setting it to `false` restores
GitHub-hosted placement and direct PR workflow routing for subsequent runs.
[Depot runner labels](https://depot.dev/docs/github-actions/runner-types)

The Depot runner group admits Skippy and uses exact protected workflow refs.
Allow these entries alongside the existing approved workflows:

```text
Mesh-LLM/skippy/.github/workflows/ci.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/ci-images.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/ci-image-builds.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/docs.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/inference.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/llama-upgrade.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/pr-checks.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/release.yml@refs/heads/main
```

With Depot enabled, `ci-pr.yml`, `ci-images-pr.yml`, and `docs-pr.yml` retain
GitHub's ordinary PR triggers and call their runner-owning workflows from
protected `main` without forwarding secrets. The image dispatcher enters
through `ci.yml`, which calls the read-only image builders at its protected
revision. The same builders supply exact artifacts for the image publishers.
The CI dispatcher requests `cache-mode: read` for the Actions cache. The image
and docs dispatchers request `cache-mode: none`. All retain GitHub's fork-run
approval policy.
Same-repository PRs to main continue to use the protected `ci.yml` path for
code and native admission. The PR controller expects the producer for the
active route. Refresh affected open PRs with a new PR event after cutover so
their selected workflows run against the current head.

On Depot, sccache uses WebDAV with the job connection supplied by the Depot
organization setting **Allow Actions jobs to automatically connect to Depot
Cache**. sccache writes only when building the exact protected `main` commit
on push, schedule, or manual dispatch with a write-capable job cache mode.
PR and other branch builds use `SCCACHE_WEBDAV_RW_MODE=READ_ONLY`. Ordinary
Depot jobs skip shared Cargo, model, native source, and npm caches. The
GitHub-hosted jobs use sccache's Actions backend.
[Depot sccache integration](https://depot.dev/docs/cache/integrations/sccache)

After enabling the switch, verify full CI and `CI images` with
`build_only=true`, then check one ordinary PR and one same-repository PR
through the aggregate.

### PR admission and quality

Same-repository PRs to `main` use the workflow definition on protected `main`.
The workflow verifies repository identity, the PR author's write access and the
exact candidate SHA before running candidate code. The ordinary PR workflow
runs approved forks on the selected ephemeral runner. Changes requiring native hardware
stay blocked until an isolated native runner path is available.

Restrict each Skippy lab runner group to the protected workflow references
below before enabling public fork builds. Validate a same-repository PR and a
release canary after applying the restrictions.

```text
Mesh-LLM/skippy/.github/workflows/ci.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/release.yml@refs/heads/main
Mesh-LLM/skippy/.github/workflows/llama-upgrade.yml@refs/heads/main
```

The CPU-only `quality` job runs on Ubuntu 24.04 inside
`ghcr.io/mesh-llm/skippy-ci-cpu:latest` for lint, tests and FFI binding
freshness. The image supplies the pinned Rust toolchain, Just, sccache,
build-essential, mold and LLVM/Clang 22 development inputs. The job verifies
its tools and pulls the private image with its own `GITHUB_TOKEN` using
`packages: read`.

Cargo and CMake compilation use two workers to fit standard private-repository
hosted capacity. Cargo build snapshots and
sccache include `/etc/skippy-ci-image-id`, so updates to `latest` use a new
compiled-output namespace. Cargo downloads use separate container cache scopes
to avoid restoring archives made with hosted-VM home/workspace paths. Native
source caches remain shared. Protected candidate admission precedes build-cache
restoration and compiler-cache credential export.

Full CI runs `just llama-check` in the same x64 CPU toolchain image after the
canary, in parallel with packaging. It uses Depot Ubuntu 24.04 with four build
workers when the repository switch is enabled, and GitHub-hosted Ubuntu 24.04
with two build workers otherwise. It checks Graph Contracts, patch regeneration,
public headers and generated API docs. The gate requires both CTest batches to
pass. The `native-contracts-evidence` artifact retains logs and patch reports.

`docs.yml` triggers directly for `docs-site/**` changes on PRs and pushes to
`main`. It installs the lockfile, builds Nextra, generates the Pagefind index
and retains `docs-site/out`. The protected PR aggregate waits for this workflow
on docs-only and mixed PRs. Only the main push deploys to `beta.skippy.sh`.
Release validation calls the workflow to create the `release-docs-site` artifact.

The release workflow downloads that exact
`release-docs-site` artifact after validation and GitHub release publication,
then deploys it to `skippy.sh` with `just docs-publish`. The publication job
does not rebuild the site. It installs the lockfile-pinned `cf` CLI and exposes
`CLOUDFLARE_API_TOKEN` only to the deploy step.

Configure `SKIPPY_DOCS_CLOUDFLARE_API_TOKEN` as a GitHub Actions repository
secret. Create it from Cloudflare's **Edit Cloudflare Workers** token template
and scope it to the account that owns the `skippy-docs`, `skippy-docs-beta`,
`skippy.sh` and `beta.skippy.sh` resources. The workflow maps it to the
`CLOUDFLARE_API_TOKEN` environment variable expected by `cf` only in the
protected-main beta deploy step and the release production deploy step.
Pull requests, documentation builds and shared release validation do not
receive this secret.

## Build prerequisites

### CPU dependency image

`ci/Dockerfile` builds the x86_64 CPU dependency image
`ghcr.io/mesh-llm/skippy-ci-cpu:latest`. It includes **build-essential
and mold**, LLVM/Clang 22 development packages (including `libclang-cpp22-dev`),
libc++/libc++abi 22, OpenMP and sanitizers, Rust/Clippy/rustfmt
from `rust-toolchain.toml`, Just, sccache, CMake, Ninja, Python, jq, OpenSSH client tools and
system libraries. `ssh-keygen` supports the offline private-key normalization
test; the image build also generates and reloads a disposable Ed25519 fixture.
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

The `.github/workflows/ci-images.yml` workflow is named `CI images` and builds
and tests relevant PRs. Main pushes and manual main dispatches publish the exact
tested CPU and GPU images in separate jobs with package-write permission. Each
publisher verifies its tested artifact and metadata, then pushes immutable build
tags and `latest` without rebuilding or running the image. Run manual refreshes
with `gh workflow run ci-images.yml --ref main` after the workflow lands.

This PR only bootstraps the image; consumer adoption follows after publication.
Future consumers use `latest` and include `/etc/skippy-ci-image-id` in build-cache
keys. Private same-repository pulls use `GITHUB_TOKEN` with `packages: read`.

### CUDA and ROCm compiler images

The `CI images` workflow also builds
`ghcr.io/mesh-llm/skippy-ci-cuda12`,
`ghcr.io/mesh-llm/skippy-ci-cuda13`, and
`ghcr.io/mesh-llm/skippy-ci-rocm` from digest-pinned vendor development
images. CUDA 12.9.2 and 13.1.2 use separate images, matching `targets.json`, so
each hosted runner only needs space for one toolkit. Both CUDA toolkits build
on native x86_64 and ARM64 Ubuntu runners.
Use `skippy-ci-cuda12:latest-arm64` and `skippy-ci-cuda13:latest-arm64` for
Linux ARM64. The `latest` tags serve Linux x86_64. ROCm builds for Linux x86_64.
The images include Rust and native build tools and compile a small
backend-specific kernel during the image build. PRs build,
smoke, and stream each exact image into a gzip-compressed archive without
publication credentials. Protected-main publication loads those same
artifacts, checks their image IDs, platforms, source revisions, backend and
toolkit labels, then pushes immutable build tags and the corresponding latest
tags. ARM64 build tags also end in `-arm64`.

The kernel compiler probes and container smoke establish compiler/toolkit image
contents only. They do not execute GPU kernels and cannot qualify a GPU runtime;
device evidence still comes from the matching CUDA/ROCm inference jobs.
Run `just ci-gpu-image-build cuda12 BASE_IMAGE 12.9.2`,
`just ci-gpu-image-build cuda13 BASE_IMAGE 13.1.2`, or
`just ci-gpu-image-build rocm BASE_IMAGE 7.2.4`, then the matching smoke and
export recipes. For ARM64, pass `arm64` as the final argument to both the build
and smoke recipes. Use the digest-pinned base for the selected architecture from
the workflow matrix.

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

Jobs that initialize the shared compiler cache send Cargo and CMake C/C++
compilation through sccache. Pushes, scheduled runs, and manual runs on protected
`main` warm the GitHub compiler cache when they build the triggering commit. PRs
and other builds can read compatible entries. The job log shows the access mode.
sccache 0.19.0 counts writes skipped in read-only mode as write errors. Quality
canary records cumulative statistics after lint and again after tests, including
JSON counters in its evidence artifact.

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

Qwen3 dense smoke preparation is explicit: `just ci-model-fetch`
resolves the immutable public model URL and verifies the family manifest's size
and SHA-256. A runner-provided `SKIPPY_CI_MODEL` is verified without downloading.
This command sets the path for subsequent Actions steps; locally export the path
before running `just ci-smoke`. Default `just test` does not download models.

Inference smoke executes the bundled CLI and runtime, checks generated tokens
in a completion, and verifies graceful shutdown. Metal and CUDA require
observed device selection and positive layer offload. Missing required fixtures
or devices fail the job. Compiler-only CUDA rows validate the extracted package's source, hashes,
runtime metadata and native dependency closure, then check CLI startup. This
artifact smoke is recorded separately from inference evidence.

## Artifact flow

`just ci-plan` validates the target matrix with `jq`. The package and
smoke commands use Python 3.9 or later. Release inventory uses Python
3.11 or later for TOML parsing. Set `SKIPPY_PYTHON` to select the interpreter.

1. Each platform's CPU job builds and verifies its CLI once using
   `just ci-host`. Inference PR jobs build their two CLIs locally because they
   do not run the CPU package jobs.
2. Each selected job calls `just ci-runtime` for its variant, then
   `just ci-package` to compose verified producer archives. It smokes the
   extracted product on the same runner and uploads the package with its evidence.
3. Release-only device jobs download the exact compiler-only CUDA archives,
   run inference smoke, and upload the same archives with device evidence under
   `release-product-*`. The publisher requires all eight variants and smoke
   evidence before publication.
4. The hosted CI gate reads the dependency results and the validated plan
   without checkout or Rust setup. Required work must pass and unselected work
   must report a skip.

Native runtime manifests retain the upstream pin, patch digest, ABI and backend
metadata. Product provenance hashes the complete file roster. Actions artifacts
are same-run transfer inputs, never cross-run cache authority. CI has no
publication or signing secrets.

## Cut a release

The **Release** workflow runs daily at 17:00 UTC with the next patch version and
skips a `main` that is already released; see [RELEASE.md](../RELEASE.md). For a
chosen version run `just release vX.Y.Z`, or dispatch **Release** from `main` in
GitHub Actions. For a rehearsal, run `just release vX.Y.Z true`. The canary stages a
temporary candidate and runs the full package, device, inference and docs
matrix. It leaves `main`, version tags, GitHub releases and deployed docs
untouched.

The workflow creates one version commit from the dispatched main SHA. The
commit updates workspace crate versions, internal path dependency pins and
`Cargo.lock`. The candidate ref under `release-candidates/` lets native
runners fetch the same SHA as hosted builds. The full matrix produces the
release manifest before the publisher receives any credentials.

Set up a repository-scoped release GitHub App with Contents write permission
and authorize it to fast-forward protected `main`. Configure the `release`
environment to accept deployments from `main`, with `SKIPPY_RELEASE_APP_ID` as
a variable and `SKIPPY_RELEASE_APP_PRIVATE_KEY` as a secret. The temporary
candidate namespace must be writable by the repository Actions token. Keep
the release tag deletion and non-fast-forward protections in place.

Without `SKIPPY_RELEASE_APP_ID` the publisher uses the built-in Actions token,
which cannot push to protected `main`. It then creates the tag only: the release
is the version commit one commit ahead of `main`, and `main` keeps its version.

With the App configured and once the candidate passes, the publisher advances `main` and creates the new
tag in one atomic push, then publishes the verified archives and deploys the
prebuilt docs. Main CI reuses the protected Release run's validation receipt
for that exact commit. An ordinary push without a matching receipt runs
ordinary CI.

If `main` moves before promotion, dispatch again from the new `main`. If
publication fails after promotion, rerun the failed jobs in the original run
while its artifacts remain available. This reuses the same commit, tag and
archives. Completed runs remove their temporary refs. Failed runs retain
theirs for retry and diagnosis.

## Llama upgrades

`llama-upgrade.yml` runs from protected main. The controller resolves a release
or explicit immutable upstream SHA and exports a digest-bound Git bundle. It
uses the x64 CPU toolchain image and a checksum-verified GitHub CLI on the
selected Ubuntu runner. Both CI passes and both family passes restore the same
candidate. The CPU, Metal and CUDA family jobs consume immutable offline model
caches and retain per-pass evidence. They receive no GitHub write credential.

Candidate preparation can be rehearsed before merge from a clean local PR
checkout, with the native build prerequisites above and
`SKIPPY_REWRITER_LLVM_PREFIX` set:

```sh
just automation-run upgrade resolve --upstream FULL_UPSTREAM_SHA --force-certify false
just automation-run upgrade prepare FULL_UPSTREAM_SHA
```

These commands do not push, merge, tag or publish. Preparation writes a candidate
bundle only after regeneration and full patch replay succeed. It does not run
family qualification. Core/model-support conflicts require a reviewed repair,
not a wider automatic-update allowlist. Failed native preparation retains Git
status, the source diff and the active failed patch (when available) under
`target/llama-upgrade/diagnostics` before removing its temporary checkout; the
workflow uploads that directory on failure. Local preparation does not bypass
the protected-main admission checks for the full Actions pipeline.

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
rejects a failed row. Then exercise a canary and a dispatched prerelease. Verify
all enabled bundles, private authenticated downloads, clean installation and
CPU/Metal/CUDA serving.
Exercise partial draft upload recovery and published-release overwrite refusal.
Only after source regeneration, both full candidate passes and exact-commit PR
publication work should the llama schedule be enabled.

No hosted/hardware capacity or secret configuration is implied by these files.
