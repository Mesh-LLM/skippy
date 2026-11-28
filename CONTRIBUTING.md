# Contributing to Skippy

Install Rust, Just, CMake, Ninja, and the native toolchain for the
backend you are building. Repository commands run from the root:

```console
just build
just test
just lint
```

Use `just build cpu` for a portable CPU runtime. macOS defaults to Metal;
Linux and Windows default to CPU unless a backend is supplied. CUDA builds
require the matching pinned toolkit and architecture list.

On Windows, `just build cpu` defaults to the machine's architecture. To build
the Windows ARM64 CPU runtime and CLI from x64, run
`scripts/build-windows.ps1 -Backend cpu -TargetArch arm64`. Install the Rust
`aarch64-pc-windows-msvc` target and the Visual Studio ARM64 MSVC and LLVM
Clang components. The Windows build places its runtime beside the executable
in Cargo's `<target-dir>/<triple>/<profile>` directory. Full CI and tagged
releases package Windows ARM64 CPU builds and verify the archive inputs.

Default tests are offline. Model-backed and accelerator qualification use
explicit certification commands and immutable fixtures. Do not make default
tests depend on a network, writable home directory, or undeclared system tools.

The production Cargo workspace is rooted here. `skippy-api-rs/` is a separate
facade workspace and must be checked independently. Native source is prepared
from `llama_cpp/upstream.txt` plus the ordered patch queues under
`llama_cpp/patches/`.

Follow [AGENTS.md](AGENTS.md) and the relevant component README before changing
native ABI, release, or CI behavior.
