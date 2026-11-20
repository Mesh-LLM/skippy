# Independent production consumer

This fixture has its own Cargo workspace. The local executable consumes
`skippy-api` and Tokio. The `staged` feature additionally uses the public
specialist stage protocol and serving surfaces. Both use the production
dynamic runtime and its checked-in bindings.

From the Skippy root, run `just embedding-contracts` for the API checks and
`just embedding-consumer-contracts` for the independent build and tests.
Registry dependencies must already be present in Cargo's cache. The consumer
runner puts a Git archive of one immutable source object inside a temporary
workspace and disables native rebuild fallback. The offline development gate
freezes the current Skippy source into a local object without updating HEAD or
the working index. To check a pinned snapshot offline, pass a repository and a
full commit to `just embedding-consumer-contracts`. Model files and a runtime
bundle are required only for qualification.

## Runtime qualification

Use a local Git mirror or repository containing the complete commit and an
already produced standalone bundle from the same native recipe. Supply a
trusted manifest digest from the producer or verified release evidence. Supply
a local split-certified chat GGUF with at least two layers and a chat template.
Both fixture executables request CPU execution. Choose two distinct unused
loopback endpoints.

```sh
export SKIPPY_FIXTURE_BUNDLE=/absolute/path/to/unpacked-runtime
export SKIPPY_FIXTURE_MANIFEST_SHA256=<trusted-manifest-sha256>
export SKIPPY_FIXTURE_MODEL=/absolute/path/to/model.gguf
export SKIPPY_FIXTURE_ENTRY=127.0.0.1:19331
export SKIPPY_FIXTURE_WORKER=127.0.0.1:19332
just embedding-qualify /absolute/path/to/skippy.git <full-source-commit>
```

The runner fails if required inputs are missing. The consumer logs the pinned
source commit and the verified runtime manifest, release, ABI and recipe
identities. The gate exercises typed model-load diagnostics, streamed output,
cancellation and drain, and an admitted two-stage chain with explicit worker
readiness and shutdown. It also checks rejection of a mismatched runtime pin
and a stage admission descriptor from an incompatible generation.

See the [production contract](../../docs/PRODUCTION_EMBEDDING.md) for lifecycle
and ownership guarantees.
