# Production Rust embedding

Use `skippy-api` with `dynamic-native-runtime` from an immutable Skippy source
commit. An ordinary host opens a `ProductionRuntime`, loads local model options,
and consumes generation through an owning `LoadedModel`. HTTP is optional.
The [standalone consumer](../fixtures/production-embedding/README.md) contains a
complete local example and a separate specialist example.

## Runtime and model lifecycle

1. Supply a standalone native bundle directory and the SHA-256 of its
   `manifest.json` from trusted release evidence. `ProductionRuntime::open_bundle`
   checks that pin, the upstream and ordered-patch recipe, the native release and
   host compatibility, the declared file digests, and the ABI exported by the
   loaded library. Keep the runtime directory immutable for the process lifetime.
   One bundle is admitted per process. The runtime handle is a process-scoped
   proof of startup and can be reused to load multiple models.
2. Use `LocalModelOptions::new(run_id, model_id, path)` with an explicit local
   model path. Supply context, concurrency, output limits, device offload, disk
   cache budget, or hook policy when the host needs them. Skippy verifies the
   source, builds the runtime and backend, and owns the scheduler, prompt cache
   and native model. Run bundle and model loading on a blocking executor.
3. Start each chat stream with its own `InferenceRequestContext`. Read
   `next_event()` through EOF for a completed request. A finish reason travels
   in the stream and EOF marks producer closure. Cancel through the context or
   `ModelStream::cancel`. Cancellation is cooperative at native work boundaries.
   `cancel_and_drain` gives a cancellation-and-join point at stream EOF. Dropping
   the stream or an in-progress admission future signals cancellation. A
   dropped stream can leave a native worker unwinding asynchronously.
4. For graceful shutdown stop admitting new requests, wait for active streams
   through EOF or cancel and drain them, and consume the model with
   `shutdown()` on a blocking executor. Streams borrow the model handle.
   Background work can retain model resources until it exits after an early
   drop. Process-global dynamic libraries stay resident until process exit.

Serving telemetry defaults to off. Enable it per load with
`ServingTelemetryOptions` and a caller-supplied OTLP endpoint and capacity.
Telemetry is best effort. The host owns its metrics collector and its durable
operational-event store. Disk prompt caching is independent of source and
package-integrity verification and can fall back to cold prefill.

## Typed load observations

Set `ModelLoadObservations::Observe` to receive progress, phase and device
records, terminal diagnostics and a final summary. Set `Enabled` to choose
the load-with-events entry point without a sink. `Disabled` chooses normal
loading. Specialist callers can adopt typed observations on an existing
`ModelLoadRequest` through `load_observed`.

Each observed load has its own bounded queue and serial callback thread.
Native producers enqueue without waiting on the callback. Slow callbacks can
overflow the queue and delay load return while queued records drain. Keep
callbacks short and avoid waiting on the load they observe. The summary reports
dropped and rejected records. Sequence values correlate native records, and
sequence gaps can also reflect unrelated native events sharing the counter.
Unknown kinds become `Other`. The final summary is delivered after draining,
and the observer thread is joined before runtime loading returns on success or
failure. A callback panic stops delivery. If the thread cannot be created,
Skippy logs a warning and continues through the enabled loading path.

Use the returned `Result` to decide whether to publish the model. The callback
summary compares native terminal records with the runtime-open result. The
runtime can emit a success record before subsequent setup fails. A missing or
contradictory observation remains diagnostic, and backend construction can
still fail after a successful runtime open.

Mesh can map these records into its existing operational event schema, retain
its own identifiers and storage policy, and use the summary for diagnostics.
The typed path replaces its native queue worker, FFI constant matching and
terminal reconciliation.

## Verified package identity and admitted stages

`PackageAcquisition::resolve_and_inspect` accepts a local package directory or
`hf://namespace/repository@revision`. The host supplies the hub cache,
progress observer and lazy client factory. A verified cache hit does zero
client construction. `CacheOnly` also applies to subsequent
`ResolvedPackageIdentity::acquire_stage` calls. `AllowUpstream` permits
metadata fetches on a miss and one upstream manifest lookup for an obsolete
cached schema on a mutable revision. Explicit commit pins retain their
identity. Unsupported schemas report `UnsupportedPackageSchema` with their
source. Invalid metadata and checksum failures propagate as errors.

Inspection verifies the package-v2 root and metadata carrier without acquiring
layer artifacts. It returns an immutable HF commit reference or a canonical
local path paired with its checked manifest digest. Use the returned handle to
acquire the exact admitted closure. The old snapshots remain available. Mesh
can replace its cache-schema retry and snapshot retargeting with this operation
and keep its existing authentication, cache location and progress presentation.

Specialist staged hosts retain `stage_admission`, `stage_load` and
`serving::readiness`. Supply a verified package, agreed graph profiles, explicit
peer endpoints, and host resource policy. Discover and independently realize
the stage plans before building configurations. Start each binary worker and
wait for wire readiness before publishing stage zero. Use
`LoadedModel::load_prepared` for the in-process entry stage or retain
`ModelLoadRequest` and `LoadedModelBackend` for direct integration with
advanced capabilities. Drain the entry and shut it down before awaiting the
binary worker shutdowns. The host owns topology publication, placement, and
remote worker lifecycles.

## Compatibility and downstream dependencies

The dependency map uses the Mesh host manifest at the issue's baseline.
[Mesh host runtime manifest](https://github.com/Mesh-LLM/mesh-llm/blob/1ea1ecf754533a6a4fcc0eff8d3f33ff5b73762f/mesh/crates/mesh-llm-host-runtime/Cargo.toml)

| Mesh responsibility | Skippy dependency and migration |
|---|---|
| Ordinary local model serving and observations | Use `skippy-api` with `dynamic-native-runtime`. Replace construction of runtime and scheduler structs with `LocalModelOptions` and own the resulting handle. |
| Protocol-neutral request and plugin hooks | Use the request types re-exported by `skippy-api::serving::in_process`. Keep `skippy-inference-api` for additional modalities, hook extension traits, and frontend adapters as needed. Mesh owns plugin discovery, loading and policy. |
| Staged graph planning, admission, transport and worker controls | Keep `skippy-api`, plus `skippy-protocol` and `skippy-serving` where Mesh controls specialist workers, lifecycle hooks and transport. Use `skippy-coordinator` where Mesh chooses to reuse placement policy. |
| Prompt disk cache | Use `with_disk_cache` for an ordinary local load. Keep `skippy-cache` only for direct cache administration or specialist policy. |
| Native event reporting | Consume typed observations. The ordinary path needs no direct `skippy-runtime` or `skippy-ffi` dependency. Audit other direct use per capability. |
| Routing, identity, leases, product settings and operations | Mesh owns these, including its event schema, storage and presentation. |

The supported Rust embedding API evolves via explicit source revision
adoption. A new public capability requires an API adoption. Cache ownership and
scheduler defaults can change inside Skippy while the local fixture retains the
same source. The native loader binds the process to a verified runtime recipe
and ABI independently of Rust types. The package schema and content digests
govern data compatibility. Deploy staged peers from the same compatible
Skippy generation with the same admission and relevant artifact identities.
Mixed stage generations carry no compatibility guarantee.

The separate [static C++ facade](../skippy-api-rs/README.md) has its own API and
native link closure. Use the dynamic production contract for the fixture and
keep the static facade in a separate process or executable.
