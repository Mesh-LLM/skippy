# skippy-api

Use `execution` with the `dynamic-native-runtime` feature for ordinary
in-process production serving. See the [contract and specialist extension
points](../../docs/PRODUCTION_EMBEDDING.md).

Shared Skippy model preparation. `SingleStageOptions` and a resolved `StageSourceIdentity` produce the same single-stage configuration for standalone and embedded callers. Model-family cache policy and checkpoint quantization/importance-matrix preparation live here. Product hooks, diagnostics and model discovery remain caller concerns.

`package::identity_from_package_v2` verifies a local package and returns its model identity. It checks the package generation, content-derived package ID, confined artifact paths, sizes and digests. The producer native ABI is provenance; runtime loading checks host/library ABI compatibility separately. Callers pass an optional `SidecarDigestCache` opened at an explicit directory; `None` disables the advisory digest cache. The API does not read environment variables or choose a home/cache directory. Existing v2 cache records keep their format and keys.

`package::metadata` verifies metadata-only package identity without fetching layer artifacts. It preserves source provenance separately from the generated metadata digest and pins resolved Hugging Face snapshot references. Callers supply the local directory and optional digest cache; remote acquisition is available through `package::acquisition`.

`source` prepares local GGUF, multipart GGUF, and immutable Hugging Face GGUF source identities. Strict local GGUF verification always hashes source bytes and preserves the strong-fingerprint checks; the source registry is a locator, never proof of content. Hugging Face snapshot roots and the metadata client factory are caller-supplied. Existing identity hash domains remain unchanged. Managed multipart views now live under `.skippy/multipart-gguf` within the HF repository cache; these are hard links to verified blobs, and existing `.mesh-llm` views are not deleted.

`source::planning` constructs the source-complete graph-planning manifest from verified GGUF identity. Embedding applications retain its profile-scoped source policy and serving hooks; callers supply transport and cache policy to package acquisition.

`stage_admission` owns native graph discovery, independent realization and fail-closed admission. The native input/descriptor reader remains together in its `native` module. `split_certification` owns the bundled architecture roster and checks the exact upstream pin, patch-queue digest and ABI. Standalone and embedded callers use the same release certification; experimental admission remains an explicit boolean chosen by the caller. Recipe hashing retains its v1 domain so moving ownership does not change certification identity.

`stage_load` builds admitted stage configs from neutral options, verified resident tensor names and activation frontiers. An embedding application maps its control requests and peer endpoints into these types; no coordinator identity, lease or iroh type crosses this boundary. `materialization` resolves local package-v2 closures, verifies required artifacts and preserves metadata-first ordering. `package::acquisition::remote::PackageAcquisition` owns network acquisition; callers supply progress observers.

The opt-in `direct_graph_admission` test accepts `SKIPPY_TEST_GGUF_PATH` and exercises real native two-stage planning, certification, tensor binding and activation frontiers. Run the complete API test suite with `--include-ignored` only when a prepared static native runtime and the pinned model fixture are available. This planning gate is separate from end-to-end split serving.

`serving::ModelLoadRequest` owns native model loading, graph-bound activation
widths, prediction-return listeners, tokenizer-bound hook construction and OpenAI
backend composition. `serving::InferenceOptions` is shared resolved configuration;
An embedding application translates its product configuration and supplies observers/plugin hooks.
Guardrail/compaction wrapping uses the serving library implementation, including
an optional caller-owned telemetry sink.

Before opening the target, model loading derives recurrent rollback retention
from enabled speculative proposals and preserves any larger caller-supplied
depth. In native ABI 0.1.81, an explicit zero disables recurrent snapshots for
ordinary decoding; direct runtime users must request positive retention for
in-place verification. Windows outside native retention continue through the
existing checkpoint/replay fallback. The C configuration factory and a null C
configuration retain their explicit legacy depth of two.

`native_runtime` selects and loads an explicitly supplied runtime bundle/cache.
It performs no product discovery and does not download implicitly. The dependency
direction is API → serving; the serving library does not depend on this API.

`serving::LocalOpenAiOptions` is the standalone HTTP entrypoint. Its conversion
preserves explicit queue/adaptive-concurrency/admission settings and loads through
`ModelLoadRequest`. `LoadedModelBackend::serve_http_with_shutdown` retains native
resources while HTTP requests drain and propagates listener/serving failures.
The serving library selects local execution for a stage-zero configuration with
no downstream peer; split configurations retain embedded stage-zero execution.

### Observing a model load

Set `ModelLoadRequest::open_events` with `ModelOpenEvents::observe` and match
`ModelOpenObservation::Event` for progress and runtime diagnostics. The final
`ModelOpenObservation::Summary` carries the load outcome, last observed sequence,
queue drops and decoder rejections. `summary.contradiction()` identifies a native
terminal event that conflicts with the enclosing load result. An empty stream or
missing terminal is possible, including with older runtimes, SafeTensors source
loads, and validation failures.
Use the `Result` of `ModelLoadRequest::load` to settle the application operation.

`ModelOpenEvents::Enabled(None)` selects the event-capable load path without
allocation of a reporter queue or drain thread. `Disabled` selects the regular
load path. `LocalOpenAiOptions::model_open_events` accepts the same options.

Skippy invokes the observer serially on a dedicated thread. The native callback
copies records to a bounded queue and never waits for the observer. The drain
thread polls during the load and makes a final drain on success or error. Skippy
joins the thread before returning, so a slow observer can delay return and
cause record loss. Keep the callback short and forward to the application's
nonblocking event ingress. Callback delivery stops on panic and is skipped if
the drain thread cannot start. On load unwind, the thread is joined without a
summary. Arrival order can differ from sequence order across native producers.

Mesh can replace `model_open_drain` and its native queue setup by assigning an
observer directly to the request. Translate the typed progress and diagnostic
events to the existing operational-event schema, and use `dropped`, `rejected`,
and `contradiction()` for the existing health and diagnostic reporting. Use the
request result to settle the existing success/failure lifecycle. `event.source`
is the complete native record, for the status, stage and session identities,
numeric summaries and raw codes that Mesh stores with each fact. The observer
can capture Mesh's operation identity and ingress, so storage and presentation
stay in Mesh. Match unknown or future observations with a wildcard. These types
are defined by `skippy-api` and can evolve independently of generated bindings.
[Embedding fixture](tests/model_open_observer.rs)

`package::inspection` owns local package inspection results and per-layer tensor/artifact accounting, including metadata-only v2 inspection and legacy offline inspection. The caller supplies a resolved directory, original reference and optional digest cache.

`serving::readiness` exposes Skippy binary-stage bind-address selection, wire readiness probes and size-scaled load deadlines. Probe cancellation joins its worker; Embedding applications retain coordinator claims, stage registration and shutdown orchestration.

`package::acquisition` owns package reference parsing, local artifact selection/verification and cached-snapshot selection. Callers provide an optional integrity-cache path; metadata-only verification always hashes metadata without that cache. `remote::PackageAcquisition` owns remote transfers, the process-wide download lock and exact stage artifact acquisition. Callers supply explicit hub/integrity cache paths and a lazy HF client factory. Complete cache hits never construct a client. The typed batch/file observer contract preserves progress ordering and resource cleanup; its default is silent. Blocking SDK calls run outside an active Tokio runtime through `skippy-model-hf::blocking`.

`materialized_cache` owns pin-aware stage-artifact pruning, source-index previews and source-based removal. Every operation takes an explicit cache root. Active pins preserve their artifact and index; removal errors propagate before an index is discarded. An embedding application supplies its existing cache directory.

`package::certification` owns two-stage package materialization checks and OpenAI model/chat/Responses smoke gates. The caller supplies the resolved package reference, acquisition policy and optional digest cache. Embedding applications retain catalog-name lookup. Missing runtime endpoints remain incomplete; package-only runs explicitly mark runtime gates not required.

Shared serving defaults are defined by `SingleStageOptions` and `InferenceOptions`,
using [`skippy-config`](../skippy-config/README.md). [`kv_cache`](src/kv_cache.rs)
owns publisher-declared live-KV dtype selection and model compatibility fallback;
[`speculative`](src/speculative.rs) owns installed sibling draft discovery and
conservative automatic pairing. Standalone Skippy and embedding applications consume these same
policies. Explicit caller settings take precedence.

### Serving profiles

Use `serving_profile::resolve` with an existing stage and frontend baseline,
shared defaults and model-specific overrides. `Setting::Inherit` leaves the
previous layer in effect, `Setting::Set` carries an explicit value, and
`Setting::Reset` restores the baseline. The result includes per-setting
provenance. Request defaults and speculative policy are typed replacements.
Mesh can translate its own configuration into these inputs.

Standalone `serve --print-effective-config` and the pre-load path use the
same stage validation. Single-model TOML, environment variables, CLI
precedence and the report fields remain compatible.

### Hardware inventory

Embedders can collect OS facts with `hardware::physical_hardware`, enumerate
a runtime they have already loaded with `hardware::runtime_devices`, and pass
both to `hardware::inventory`. The inventory accepts per-device limits,
reservations and measurements keyed by the reported physical stable ID. It
reports absent facts as optional values and exposes runtime-only devices
separately. A device name or enumeration order cannot establish identity.

`skippy --output json doctor --device-inventory` adds a `device_inventory`
object to the existing doctor report. A runtime probe failure is reported as
`device_inventory_issue`; OS discovery remains available. The probe uses an
installed runtime and does not download one.

### GPU placement

Use one `placement::DeviceAllocator` per shared serving process and bind each
`ModelLoadRequest` through `load_with_placement`. Supply an inventory that
maps stable IDs to selectors exposed by the selected runtime. A pinned request
fails if its device is missing, ambiguous, incompatible or out of capacity.
The load owns its reservation until the loaded backend is released.

For standalone serving, use the physical `stable_id` shown for a selectable
GPU by `skippy --output json doctor --device-inventory` with
`skippy serve --model-path MODEL --gpu-id ID`. A runtime-only accelerator uses
its reported runtime `identifier`. Skippy checks fit before loading.

To qualify on a two-GPU host, select a native runtime that sees both devices.
Use the same allocator for two concurrent local load requests with distinct
pins, request inference from both, and check the runtime's memory on each
GPU. Drop one backend and confirm the corresponding budget can be reserved
again. Use two model fixtures that fit the intended device budgets.

### Split execution

`split_execution::plan_direct_gguf` builds the same admitted stage
configurations as `skippy plan-split`. Embedders can supply worker IDs and
listen endpoints, an expected manifest digest, measured worker capacity,
and optional locked contiguous ranges. Capacity planning and native
admission must both succeed before a plan can start.

Implement `StageOperations` to bind the admitted worker identity to your
transport and artifact policy. `AdmittedSplit::start` checks the current
manifest and endpoints before starting downstream stages first.
`SplitExecution::observe` reports stage state. For binary endpoints an
adapter can use `start_binary_stage_ready_probe` for the existing readiness
handshake. Cancel the execution to release stages in reverse startup order.

## Resolving a package for stage planning

Use `PackageAcquisition::resolve_and_inspect_package` with the original local or Hugging Face reference. Mesh and other embedding hosts can replace their schema checks, cache fallback, and metadata inspection calls with this operation. Keep passing the existing explicit cache root, lazy Hub client factory, and progress observer to `PackageAcquisition`.

```rust
use skippy_api::package::acquisition::remote::PackageInspectionPolicy;

let package = acquisition.resolve_and_inspect_package(
    selected_package_ref,
    PackageInspectionPolicy::AllowUpstream,
)?;
```

Use `CacheOnly` for offline resolution. `AllowUpstream` fetches missing metadata and resolves a mutable revision upstream once when its cached manifest declares schema 1. A full Hub commit pin stays fixed. `PackageInspectionError::UnsupportedSchema` reports the schema and whether it came from a local package, the cache, or upstream. Missing offline Hub metadata produces `MetadataNotCached`. Malformed manifests and integrity failures surface as errors.

Use `package.package_dir` for graph planning and `package.package_id` for exact stage admission. Carry `package.identity.manifest_sha256` with the stage load and pass `package.identity.package_ref` to `resolve_package_v2_stage_to_local`. For Hub packages this reference contains the verified snapshot’s full commit hash, so later stage acquisition uses the same snapshot when a branch moves. Inspection verifies the manifest and carrier. Stage acquisition fetches and verifies the required layer artifacts after admission.
