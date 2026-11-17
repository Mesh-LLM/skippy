# DGX Spark inference qualification

The agreed inventory lives in `inference-ladder.json`. Generate a source-bound
plan with `just inference-plan pr FULL_COMMIT_SHA`, or use `nightly` / `release`
for the extended ladder. Split runs apply only to models with at least 27B total
parameters (not activated parameters for MoE). Smaller models run standalone.
PR selects four architecture representatives: four standalone and three split
runs (seven rows). Nightly and release select all eight models: eight standalone
and four split runs (twelve rows). The inventory's explicit `split` flag records
this selection. `just inference-contracts` checks this contract
offline. Planning does not load weights or produce qualification evidence.

## Execution boundary

Reuse the Pilot GitHub App, signed workflow-job webhooks and JY-managed runner
integration. The Actions job is an orchestrator only. Model loading, serving,
correctness checks and AgentX run in native JY jobs, outside the Actions
container's 6 GiB limit. Do not raise the limit globally or expose the host
Docker socket to a workflow to work around it.

Trusted same-repository PRs from verified repository collaborators may use native
execution on persistent dedicated Sparks. Protected dispatch code must verify
repository identity and collaborator status before submitting the candidate SHA.
Jobs run under an unprivileged account without sudo, host administration or
publication credentials, with fresh source-pinned workspaces, read-only admitted
model inputs and exclusive reservations. Clean up processes and workspaces on
every outcome, quarantining allocations when cleanup fails. Fork and other
untrusted PRs require isolated ephemeral execution. An `unconfined` capability
alone does not reject a trusted native job; actual account permissions and
resource exposure determine admission.

Standalone requires a GB10 Spark allocation. Split requires an atomic JY
formation containing two distinct GB10 Sparks on the same reachable network.
Both members must receive the same immutable source SHA and model identity.
Use JY rendezvous, member failure teardown and bounded leases; never reserve
only the coordinator and SSH into an unreserved second machine. Hardware
selection must apply to every formation member, not just a job selector that
the formation renderer discards.

Memory budgets must include actual quantized weights, native allocations,
context/cache capacity, concurrency and an operating-system reserve. Nominal
parameter counts are not memory budgets. Admit the measured footprint before
executing; insufficient capacity must queue or fail. There is no passing skip.

## Evidence and checks

Correctness, scored answer quality and serving performance are separate gates.
Retain raw outputs and diagnostics for each. A short PR AgentX replay is a
performance smoke test, not an official AgentX score. Extended qualification
requires a locked harness/scenario/corpus, declared context and load, consistent
warmup, repeated measurements and retained request-level timings and errors.
Do not gate latency on an arbitrary threshold before measuring baseline noise.

Bind evidence to the source SHA, native pin and patch digest, ABI, model shard
hashes, tokenizer and chat template revisions, quantization, split boundaries,
backend/toolkit, actual JY machine identities and harness/settings digest.
Compare each model to its own trusted baseline. Check standalone against an
independent reference and split against standalone. A shared implementation
cannot establish independent numerical correctness by comparing with itself.

PR triggers must include serving, runtime, scheduler, cache, tokenizer, model
loading, topology/transport, native patch queues and their dependency/build
inputs, as well as changes to qualification itself. Existing ordinary CI stays
required; this lane supplements it. Cancel superseded PR allocations and tear
down the entire split formation. Retain nightly and release evidence.

## Implementation and activation

`inference.yml` is the protected controller: pull-request-target dispatch verifies an
open, current same-repository PR and a write-capable collaborator via GitHub.
It uses controller source from main, never executes the candidate on that runner,
and selects inference-related paths. Nightly and manual/reusable release runs
use the same dispatcher. Release publishing depends on qualification.

The repository secret `JIANYANG_INFERENCE_SSH_KEY` is a dedicated submitter SSH
identity. The controller signs a challenge, caches a short-lived session for at
most 30 minutes and renews it during long runs. No admin key is given to Actions
or model jobs. The controller records launches and terminal snapshots under
`target/ci-evidence/inference`; JY retains native logs and artifact directories.

`.gi/templates.yaml` defines standalone native jobs and atomic two-Spark
formations. Split placement is explicitly pinned to spark-000137 and spark-000265
because the current formation renderer does not apply job hardware selectors.
Each native member independently verifies Linux/aarch64 and an actual GB10.
The process supervisor bounds foreground commands; serving process groups are
terminated on normal and error paths. JY leases bound abrupt termination.

`inference-artifacts.json` pins the base GGUF shard hashes and sizes plus the
matching official tokenizer revision and layer count. Most fixtures use Q4_K_M;
Flash-Next uses the four-shard IQ4_XS base model, not similarly named MTP sidecars.
Admission reserves at least 16 GiB beyond weight bytes and checks available
unified memory. This floor is not proof every context fits; real allocation
failures remain failures. Model caches are explicit, hash-verified inputs.

The native workload runs chain parity and state-handoff checks against native
monolithic execution, then scores three exact-output cases and deterministic
repeats through the serving endpoint. These are a small regression set, not a
comprehensive capability benchmark or an independent numerical implementation.

Performance uses AIPerf 0.13.0 on Python 3.12, the date-named 2026-06-21 256k trace variant,
seed 42 and concurrency one. PR uses an explicitly unsafe 120-second,
four-trajectory smoke with no official-score claim. Nightly uses two 900-second
measurements; release uses two 1800-second measurements. Context is 32768 for PR
and 65536 for extended runs. Extended runs require valid AIPerf exports.
These capacity-limited runs are not full-native-context leaderboard submissions.

Local checks and live template-schema admission must precede a native pilot.
No successful hardware qualification is implied by implementation or schema
validation. Record pilot job IDs and any failures before enabling required checks.
The schedule becomes active only when this workflow lands on the default branch.
