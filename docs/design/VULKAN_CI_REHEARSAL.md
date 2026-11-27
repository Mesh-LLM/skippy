# Vulkan CI rehearsal

Skippy already has Vulkan runtime selection and native packaging support.
The CI addition builds Linux x86_64 and Windows x86_64 packages in full CI,
which nightly, tagged-release validation and llama-upgrade validation reuse.
Linux requires real inference on both the existing NVIDIA CUDA 13 and AMD ROCm
pools, using the same packaged archive. Windows requires archive verification;
Windows Vulkan inference remains unqualified. The short PR roster stays at eight
runtimes; the full roster grows from thirteen to fifteen.

## Execution

The isolated `rehearsal/vulkan-ci` branch exercises the production `ci-package`
and `ci-smoke` actions, then the production artifact, native identity and release
smoke verification functions. Its temporary workflow builds the two platform CLI
producers, both Vulkan products and both Linux vendor receipts. It uses hosted
Ubuntu 24.04 after the configured Depot runner remained queued. Its `release`
artifact prefix selects evidence names only: it has no publication jobs or
credentials, and does not promote main, create tags or publish a release.

The final candidate is implementation commit
`ca9de8552509a69abb604b1c3bad87bd8bf010a5`, cherry-picked onto rehearsal source
`0c103726f44db8f553b1af4fe17f7873dee8290f` with the temporary workflow and verifier.
The candidate implementation is unchanged; the rehearsal adds a temporary
workflow, verifier and Just recipe.

- [Final rehearsal run 45976759553](https://github.com/Mesh-LLM/skippy/actions/runs/45976759553): passed on 2026-11-27. All nine jobs succeeded, including both vendor jobs and final release evidence verification. Its `vulkan-rehearsal-receipt` artifact records `result: passed` and both product hashes.
- [SDK installer rehearsal 45967435799](https://github.com/Mesh-LLM/skippy/actions/runs/45967435799): passed on Ubuntu 24.04 and Windows 2022. Both downloaded, checked and installed LunarG SDK `1.4.328.1`, then executed `glslc` and checked the header identity.
- [NVIDIA environment probe 45976403679](https://github.com/Mesh-LLM/skippy/actions/runs/45976403679): passed Vulkan initialization on GeForce RTX 5080, API `1.4.329`, NVIDIA driver `595.99.02`, after installing `libegl1` in the ephemeral runner container.

## Artifact identities

Downloaded final-run products independently matched their archive-smoke receipts:

| Runtime | Archive SHA-256 |
| --- | --- |
| Linux x86_64 Vulkan | `6dc419a200cde33c7fa693b1b240ac6d166d22091477faf3912f0d8e037e7091` |
| Windows x86_64 Vulkan | `c7b1a630df1e2c3b6358cc5be492813c78fa320f3382cda9166fa2a101518c97` |

Both products record SDK `1.4.328.1` and source
`0c103726f44db8f553b1af4fe17f7873dee8290f`. The explicit inference fixture is
Qwen3-0.6B-Q8_0, SHA-256
`9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031`.

| Linux inference device | Vulkan API | Reported driver version | GPU layers | Completion tokens |
| --- | --- | --- | --- | --- |
| NVIDIA GeForce RTX 5080 | `1.4.329` | `595.99.2.0` | 29/29 | 4 |
| Radeon 8060S Graphics (RADV GFX1151) | `1.4.318` | `25.2.8` | 29/29 | 4 |

Both downloaded vendor receipts match the Linux archive hash, source and fixture
above. Their server logs record `Vulkan0` device selection and positive layer
offload. Both native jobs completed their cleanup hooks successfully. The final
verifier unpacked both products, verified product receipts and native identities,
and required both exact Linux vendor receipts plus Windows archive evidence.

This rehearsal covers the added Vulkan paths. The complete fifteen-runtime
matrix, Graph Contracts, documentation deployment and release publisher were
not executed in this scoped run. Windows GPU inference remains unrun. The
four-token request establishes package/device smoke, not a performance benchmark
or broad model qualification.

## Failures found and fixed

1. Repository action policy rejected the proposed third-party SDK installer.
   The project now downloads official SDK archives through a focused Just recipe,
   checking pinned SHA-256 values before extraction or installer execution.
2. LunarG returned HTTP 403 to Python's downloader on hosted CI. The installer
   uses the runner's `curl`; both actual platform installations passed afterward.
3. JY cold provisioning did not resolve matrix expressions in runner groups.
   Separate NVIDIA and AMD jobs now declare literal groups and labels, and the
   full gate requires both.
4. The NVIDIA pool already runs inside a GPU Actions container without a Docker
   socket. The smoke now runs directly in that image. AMD retains its existing
   compiler job container and native ownership cleanup.
5. NVIDIA's loader opened the injected driver but could not resolve
   `vkCreateInstance`. The runner lacked `libegl1`, which the driver loads during
   headless Vulkan initialization. Installing this user-space dependency fixed
   the probe; host drivers were not changed. This matches
   [NVIDIA's diagnosed container failure](https://github.com/NVIDIA/nvidia-container-toolkit/issues/1952).

Earlier full rehearsals passed both package builds and AMD Qwen3 inference, but
failed or waited on NVIDIA. They are diagnostic evidence, not a successful final
rehearsal. In particular,
[run 45971573053](https://github.com/Mesh-LLM/skippy/actions/runs/45971573053)
finished failed because the NVIDIA job attempted nested Docker.

## Local validation

Completed against implementation `ca9de855`:

```sh
SKIPPY_PYTHON=/tmp/skippy-vulkan-ci-venv/bin/python just ci-automation
actionlint .github/workflows/ci.yml
shellcheck scripts/ci-vulkan-runtime.sh
git diff --check
```

All exited successfully. Automation includes 97 Node tests, Vulkan admission and
SDK corruption tests, release evidence contracts and report checks. These offline
checks do not establish device qualification. No Rust source changed.

## Temporary infrastructure

The AMD runner pool temporarily admitted only the rehearsal workflow and branch,
in addition to its existing main CI workflow. After both native jobs completed,
the original admission was restored and read back successfully: only
`Mesh-LLM/skippy/.github/workflows/ci.yml@refs/heads/main` remains. The NVIDIA pool kept its existing selected-repository
and workflow configuration; its registration was reconciled with those unchanged
settings. The temporary Vulkan rehearsal, SDK rehearsal and runner probe workflows
were disabled after collecting evidence. Their branches and run logs remain for
review. Main, tags, host GPU drivers and published releases were unchanged.
