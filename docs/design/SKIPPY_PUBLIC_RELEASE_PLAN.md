# Skippy public release plan

Status: approved product and release plan; implementation is not complete.

This document defines the gate for making the Skippy repository public and
publishing standalone CLI and native-runtime bundles. It records the product,
platform, performance, cache, compatibility, telemetry, certification and
publication decisions that must be implemented and demonstrated before the
repository is made public.

It does not change the current CI or release contract by itself. Until the work
below lands and is exercised on the required hardware, the existing
[`ci/README.md`](../../ci/README.md), target manifest and workflows describe the
implemented behavior.

## Outcome

The public release consists of:

- a public Skippy source repository;
- a backend-neutral CLI archive for every supported host;
- a separate native-runtime bundle for every supported host/backend/toolkit
  combination;
- a stable standalone CLI, settings and core HTTP inference surface;
- default-on, privacy-bounded and user-disableable operational telemetry;
- GPG-signed release tags and checksum manifests; and
- nightly immutable prereleases from protected `main`.

Mesh consumption is independently owned downstream and is not a Skippy public
release blocker. Publishing Rust crates, npm packages, a website or hosted
inference is outside this plan.

## Delivery phases

### Phase one: public repository

Phase one supports both existing Linux architectures and macOS arm64. The CLI
is built once per host and remains backend-neutral. Runtime bundles are
independent artifacts; a missing bundle is never rebuilt by a packaging or
consumer job.

| Host | Required runtime bundles |
| --- | --- |
| macOS arm64 | CPU, Metal |
| Linux x86_64 | CPU, CUDA 12, CUDA 13 |
| Linux arm64 | CPU, CUDA 12, CUDA 13 |

This is three CLI producers and eight runtime bundles. CUDA 12 and CUDA 13 are
separate downloads, not one combined CUDA package.

Every bundle retains its source SHA, llama.cpp pin, ordered patch digest,
Skippy ABI, architecture, backend, toolkit, supported GPU architectures, file
roster, licenses and hashes. CI builds remain credential-free. Hardware jobs
run untrusted pull-request source only on repository-scoped ephemeral runners.

### Phase two: Windows and ROCm

Phase two adds:

- Windows x86_64 CPU and CUDA bundles;
- Windows ROCm when the upstream stack supports the required contract; and
- Linux ROCm for each admitted architecture.

Phase two requires real Windows CUDA and Windows/Linux ROCm runners. Compilation
or toolkit presence is not execution evidence. These cells become required only
after their runner labels, device profiles, packaging contract and real-model
qualification have been checked in and successfully rehearsed.

## Cache hierarchy

L2 and L3 reuse are phase-one release requirements, not post-release roadmap
items. The production hierarchy is:

| Tier | Storage | Lifetime and scope |
| --- | --- | --- |
| L1 | Device or unified memory owned by the live native runtime | Survives request completion and device-cache reuse; lost with the process/runtime |
| L2 | Host RAM | Recovers work after L1 eviction within the live process; not durable across process restart or reboot |
| L3 | Local persistent SSD | Survives process restart and reboot; shared by processes belonging to the same Skippy installation on one node |

The logical cache namespace includes all inputs that affect reuse safety:
model and artifact identity, tokenizer and chat-template identity, stage/layer
range, runtime ABI, cache layout and dtype, backend/platform, position
configuration, tenant/cache salt and token sequence. A mismatch is a miss.

L3 objects are versioned and content-addressed. Publication is atomic, reads
verify integrity, and quota/eviction behavior is explicit. Concurrent processes
from the same installation may share objects without sharing mutable runtime
state. Corrupt, incomplete, incompatible or unauthorized objects fail closed
and are never treated as warm evidence. Cache write or restore failure falls
back to correct recomputation when safe; it never changes model output.

"Warm once per node" means reuse may continue indefinitely while the complete
cache identity remains equal. It does not permit reuse across model, template,
ABI, layout, tenant or recipe changes.

## Model support tiers

Public support is divided into three states. Recognizing a model in llama.cpp
does not by itself make the model supported by Skippy.

### Tier A: first class

A Tier A entry names an immutable model artifact, quantization and sidecars. It:

- passes correctness and cold/L1/L2/L3 cache contracts;
- passes on every phase-one backend on which the model is advertised;
- passes the AgentX and performance contract on every required performance
  backend;
- exercises the default speculation policy when the model supports it;
- blocks release on missing or failing required evidence; and
- remains supported across compatible releases until explicitly deprecated.

### Tier B: certified compatible

A Tier B architecture has a pinned representative in the broad family battery
and passes its declared graph, state-handoff and correctness contracts on the
designated qualification backends. It has no two-percent performance promise
and need not be benchmarked on every release platform. A regression blocks a
llama.cpp upgrade that would invalidate the checked-in certification.

### Experimental or unsupported

Other models may load through generic graph discovery, but Skippy makes no
compatibility, performance or release-blocking promise for them. They must not
be advertised as supported merely because a load happened to succeed.

## Four-family performance roster

The phase-one benchmark uses Q4-class Unsloth GGUFs. Skippy and the llama.cpp
baseline consume the exact same admitted files, chat template, prompt corpus,
sampling configuration and sidecars.

| KV architecture | Representative | Initial quantization |
| --- | --- | --- |
| Dense attention | `unsloth/Qwen3-4B-GGUF` | `Q4_K_M` |
| Hybrid dense | `unsloth/Qwen3.5-4B-GGUF` | `Q4_K_M` |
| Attention MoE | `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` | `Q4_K_M` |
| Hybrid MoE | `unsloth/Qwen3.6-35B-A3B-GGUF` | `UD-Q4_K_M` |

The certification manifest pins the immutable Hugging Face revision, exact
filename, size, SHA-256, required projector or MTP sidecars, chat-template
digest and license for every row. Mutable `main` references are not evidence.

Qwen3.8 Flash Next is an additional `qwen4exp` architecture certificate rather
than the dense representative in this four-family matrix. Its large Q4 artifact
requires an explicitly provisioned large-memory qualification lane before it
can become a required Tier A performance cell.

The existing small Qwen dense fixture remains appropriate for routine package
smoke. A smoke fixture is not a substitute for the four-family benchmark.

## Performance contract

The baseline is llama.cpp built from the exact Skippy upstream pin with matched
compiler, backend, toolkit, model bytes, context, batching, sampling and device
configuration. Candidate and baseline runs alternate on the same otherwise
idle machine. The benchmark records warm-up policy, run order, repetitions,
thermal state and hardware identity so a noisy result cannot silently pass.

Required performance backends are:

- macOS arm64 Metal;
- Linux CUDA; and
- Linux ROCm after phase two admits its runner and release cell.

CPU release bundles require correctness and real inference smoke, but CPU is
not part of the two-percent performance gate.

Every required backend/family/cache-state cell passes independently. Results
must not be averaged across a failing platform, model or cache tier. Skippy must
equal or beat llama.cpp within the following tolerance:

| Measure | Required result |
| --- | --- |
| AgentX score | at least 98% of the matched llama.cpp score |
| P50, P90 and P99 TTFT | no more than 2% slower |
| Prefill throughput | no more than 2% lower |
| Decode throughput | no more than 2% lower |
| Peak host memory | no more than 2% higher |
| Peak device memory | no more than 2% higher |
| Output correctness and request failure rate | no regression |

Before these thresholds become required, the harness fixes the number of
alternating rounds and samples, accepted confidence interval, AgentX version,
corpus digest and score calculation. A result inside the two-percent boundary
but outside the configured confidence requirement is inconclusive, not passing.

Each family/backend combination exercises four distinct scenarios:

1. true cold prefill;
2. warm L1/device-resident replay;
3. L2/host-memory restoration after L1 eviction; and
4. L3/disk restoration after process restart.

The corpus includes exact replay, divergent tails and a growing agentic coding
conversation. It includes realistic input/output length distributions and
concurrency rather than a single repeated prompt. Inference-only and end-to-end
latencies are both retained.

## Checked-in certification evidence

Certification remains simple and repository-owned. Results are checked-in JSON
validated by a checked-in schema; there is no external evidence service and no
time-based expiry.

An evidence row identifies at least:

- model, sidecar and prompt-corpus digests;
- llama.cpp pin and ordered patch digest;
- ABI and cache-format versions;
- Skippy benchmark/eval implementation identity;
- backend, toolkit, OS, architecture and hardware profile;
- complete benchmark configuration;
- per-scenario measurements and pass/fail decisions; and
- the generating workflow run and source commit for traceability.

Evidence remains valid indefinitely for the identity it describes. A change to
an identity input does not delete or expire the old result; it means the old row
does not certify the new recipe. Release admission consumes matching checked-in
rows instead of rerunning the entire family battery.

Human edits cannot turn a failing measurement into a passing one. Typed xtask
automation validates the schema, recomputes the threshold decision and rejects
duplicate, missing, stale-identity or internally inconsistent rows.

## Nightly workflows

Llama upgrade and nightly release are separate schedules.

### Nightly llama upgrade

The existing protected-main upgrade contract remains authoritative: resolve an
immutable upstream candidate, replay and regenerate the ordered queues, run two
independent certification passes and open an ordinary PR. It never publishes
unmerged candidate source, merges its own PR or moves a release tag.

### Nightly release

A separate scheduled workflow releases protected `main` as an immutable GitHub
prerelease named `b${run_number}`. It calls the same CI/build/package
implementation as pull requests, main and production releases. It consumes
matching checked-in certification rows and publishes only a complete phase-one
artifact matrix.

The `b` channel is deliberately separate from strict SemVer `v*` production
tags. A nightly rerun may complete an identity-matching private draft, but may
not overwrite an already published build or move its tag.

Nightly and production releases never cancel one another. Failed jobs retain
diagnostic evidence and do not publish a partial final release.

## Public compatibility contract

### Stable phase-one surfaces

The following are supported public interfaces:

- released CLI and runtime-bundle archive layout;
- runtime manifest, checksum, installation and discovery behavior;
- `skippy serve`, `prompt`, `models`, `runtime` and `doctor`;
- supported commands' JSON and JSONL output and exit-status behavior;
- versioned serving-settings TOML and configuration precedence;
- `GET /v1/models`;
- `POST /v1/chat/completions`;
- `POST /v1/completions`;
- `POST /v1/responses`; and
- `/health`, `/healthz` and `/readyz`.

Incompatible changes to a stable surface require an appropriate compatibility
version change, documented migration and deprecation where practical.

### Experimental surfaces

The following may change until separately promoted:

- `plan-split`, distributed topology files and the binary stage protocol;
- the C++ and separate Rust facade APIs;
- `/v1/decisions` and `/v1/systemone`;
- embedding, reranking, audio and realtime routes without corresponding Tier A
  model certification;
- model-package publication internals; and
- the physical L2/L3 representation, provided incompatible cache entries are
  detected and safely invalidated.

Experimental behavior is documented as such and is not silently presented as
part of the stable public API.

## Telemetry and privacy

Operational telemetry is enabled by default and can be disabled. The first run
displays a clear notice. Settings, environment and CLI each provide a stable
opt-out; the effective configuration reports whether telemetry is enabled.
Collection, queue or delivery failure never delays or fails inference.

Allowed fields are:

- Skippy version and build identity;
- OS and CPU architecture;
- backend and coarse device model;
- model family and quantization;
- context, input and output token counts;
- TTFT, prefill/decode throughput and request duration;
- peak host and device memory;
- L1/L2/L3 hit, miss, restore and eviction measurements;
- speculation acceptance measurements;
- structured, sanitized error codes; and
- a random installation identifier.

Skippy never sends:

- prompts, generated text or embeddings;
- tool definitions, arguments or results;
- repository content or local paths;
- API keys, Hugging Face tokens, credentials or environment values;
- hostnames, usernames, email addresses or other direct identity; or
- raw logs and unsanitized stack traces.

Before public launch, the repository publishes the payload schema, endpoint
operator, retention period, opt-out instructions and privacy contact. The
collector does not retain source IP addresses beyond bounded abuse protection.

## GPG signing

Every production and nightly release has:

- a GPG-signed Git tag; and
- a detached GPG signature over the canonical checksum manifest.

The checksum manifest covers every final artifact after any signing or
packaging transformation. The public key, fingerprint, rotation process and
revocation instructions are published in the repository. Private signing key
material is available only to the publisher job and never to build, test,
hardware or pull-request jobs.

## Public-repository readiness

Before changing repository visibility:

- audit reachable history, tags, release assets and generated fixtures for
  credentials, private source, private URLs and unintended proprietary data;
- verify all first- and third-party license and CUDA redistribution notices;
- publish contribution, security-reporting, telemetry/privacy and support-tier
  documentation;
- protect `main`, production tags and nightly tag creation;
- prove fork pull requests cannot reach persistent privileged hardware or
  publication/signing credentials; and
- rehearse an authenticated draft followed by a public anonymous download and
  signature/checksum verification from a clean host.

Repository visibility changes only after this audit and the release acceptance
gate both pass.

## Implementation ownership

Automation policy and evidence validation live in typed Rust under
`tools/xtask`. Just recipes and platform scripts remain thin adapters. The
implementation should extend the existing owners rather than create a second
CI or release system:

- `ci/targets.json` remains the target/runtime source of truth;
- `.github/workflows/ci.yml` remains the shared build and validation workflow;
- `.github/workflows/release.yml` remains the strict SemVer production path;
- `.github/workflows/llama-upgrade.yml` remains the protected upgrade path; and
- the nightly workflow reuses those implementations while owning only nightly
  admission and publication policy.

The cache work follows the invariants in
[`SKIPPY_UNIFIED_RADIX_CACHE.md`](SKIPPY_UNIFIED_RADIX_CACHE.md), extending them
with L2 and shared-on-node L3 behavior. The full family roster remains separate
from the four-family performance representatives.

## Phase-one acceptance checklist

The repository is ready to become public only when all of the following are
demonstrated:

- [ ] All three host CLIs and eight runtime bundles build, package and run their
      required real-model smoke.
- [ ] L1, L2 and shared-on-node L3 correctness, integrity, eviction and restart
      recovery contracts pass.
- [ ] All four model families pass every required Metal and CUDA performance
      cell within the two-percent contract.
- [ ] Checked-in certification JSON is schema-valid, identity-matched and
      independently threshold-checked.
- [ ] A nightly upgrade run completes both certification passes and opens an
      ordinary PR without publishing candidate source.
- [ ] A `b${run_number}` nightly release publishes a complete immutable matrix
      from protected `main`.
- [ ] Stable CLI, configuration and HTTP contracts pass their compatibility
      fixtures.
- [ ] Telemetry notice, payload allowlist, opt-outs, failure isolation, privacy
      disclosure and retention policy are verified.
- [ ] The release tag and checksum manifest verify against the published GPG
      key from a clean machine.
- [ ] Public-repository history, licensing, runner isolation and secret audits
      are complete.
- [ ] Anonymous clean-host download, checksum verification, installation and
      CPU/Metal/CUDA serving succeed.

ROCm and Windows rows are added to the same gate in phase two; they are not
papered over as passing skips in phase one.

## Operational inputs still to pin

Implementation may proceed without reopening product scope, but the following
deployment inputs must be committed before their gates become required:

- immutable Hugging Face revisions and file/sidecar digests for the four-family
  roster;
- exact Metal, CUDA and later ROCm runner hardware profiles;
- AgentX version, corpus digest, sampling configuration and statistical run
  policy;
- telemetry endpoint operator, retention period and privacy contact; and
- GPG public key, publisher-secret provisioning, rotation and revocation
  procedure.
