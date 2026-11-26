# tierbench

A small runner that measures how fast data moves through a machine's memory tiers: CPU caches, DRAM, GPU memory (including on-chip shared memory) and storage. Each measurement lives in a plugin, a shared library the runner loads with `dlopen` when it starts. Adding a measurement means building one more `.so` and dropping it into the plugins folder; the runner is never rebuilt.

## Layout

| Path | What it is |
|---|---|
| `include/tierbench.h` | the plugin ABI, in C so a plugin can be C, C++, CUDA, HIP or Swift (`@_cdecl`) |
| `src/` | the runner: loads plugins, sweeps sizes, times runs, writes one JSON row per repetition |
| `plugins/cpu_memory.cpp` | read, write and copy bandwidth, and dependent-load latency, from L1 out to DRAM |
| `plugins/storage_posix.cpp` | durable writes, direct reads and page-cache reads against the scratch directory |
| `plugins/gpu_plugin.cpp`, `gpu_kernels.cu`, `gpu_api.h` | one source for CUDA and HIP: copies, zero-copy, GPU memory and shared memory |
| `plugins/gpu_placement.cpp` | CUDA and HIP: where a buffer lives (device, mapped pinned, managed or pageable host memory), as kernels read and write it and as a checkpoint saves and restores it |
| `plugins/gpu_prefill.cpp` | prefill compute: half and bfloat16 matrix multiplies through cuBLAS or hipBLAS, swept by token count |
| `plugins/metal_prefill.mm` | prefill compute on Apple GPUs through MPS |
| `plugins/metal_memory.mm` | Apple GPUs: blits, kernel reads from private, shared and no-copy buffers, threadgroup memory, GPU latency and `MTLIOCommandQueue` loads |

## Build

From the repository root:

```sh
just tierbench
just tierbench cuda
just tierbench rocm "" gfx1151
```

The first builds the CPU, storage and, on macOS, Metal plugins. `cuda` adds the CUDA plugins, built for the GPU in the machine unless a CUDA architecture list is given second. `rocm` adds the HIP plugins and needs the gfx target `rocminfo` reports. Everything lands in `target/tierbench`, with the plugins in `target/tierbench/plugins`.

## Run

```sh
cd target/tierbench
./tierbench --list
./tierbench --scratch /path/on/the/nvme --out rows.jsonl
```

Rows go to `--out`, or to stdout without it. A median per size goes to stderr. `--case cuda/shared` runs only the cases whose `plugin/case` contains that text. `--help` lists every option.

For each size, the runner:

1. sets the case up
2. raises the iteration count until one run lasts at least `--target-ms`
3. runs `--warmups` unrecorded repetitions
4. records `--reps` repetitions

A run that fails is recorded with its error, and the case moves on to the next size. A size the hardware cannot do is recorded as `unsupported`, not as a failure.

The scratch directory must be on the storage you want to measure. The default temp directory may be in RAM.

## Writing a plugin

Export one C function that returns a static descriptor:

```cpp
#include "tierbench.h"

namespace {
void* setup(uint64_t bytes, const tierbench_env* env, tierbench_error* error);
int run(void* state, uint64_t iterations, tierbench_measure* out, tierbench_error* error);
void teardown(void* state);

constexpr tierbench_case cases[] = {
    {"my-case", "what-it-uses", TIERBENCH_HOST, TIERBENCH_GPU, 4096, 1ull << 30, setup, run, teardown},
};
const char* facts(const tierbench_env*) { return "{}"; }
constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, "mine", facts, 1, cases};
}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) { return &plugin; }
```

```sh
c++ -std=c++23 -O2 -shared -fPIC -Iinclude mine.cpp -o build/plugins/mine.so
```

The contract:

- **`unit`** says what a case's size means: `TIERBENCH_BYTES` for a transfer size, `TIERBENCH_TOKENS` for a token count. The runner sweeps token cases from `--min-tokens` to `--max-tokens`.
- **`setup`** gets the size and returns state that the plugin owns. On failure it returns `NULL` with `error->message` set. If the size cannot run on this hardware, it also sets `error->unsupported`.
- **`run`** performs `iterations` transfers and returns once all of them have finished, including device work.
  - It sets `out->bytes_per_iteration`: 0 for a latency case, where the runner reports ns per iteration instead.
  - A compute case also sets `out->ops_per_iteration`, and the runner reports TFLOP/s.
  - It may also set `out->device_ns` and `out->has_device_ns`, for example from GPU events.
- **`teardown`** frees the state. The runner calls it exactly once for each successful `setup`.
- **`facts`** returns a JSON object describing the device. The runner stores it in every row as `plugin_facts`.

The runner checks every descriptor when it loads it. A plugin with the wrong ABI version or a missing function is refused, and the other plugins still run.

## Rows

One JSON object per line. Each row carries:

- the run, its start time and the host: node, OS, kernel, CPU, cache sizes, memory, page size
- the plugin and its facts
- the case, mechanism, `from` and `to` tiers, `size`, `unit` (`bytes` or `tokens`), `rep` and `iterations`
- `wall_ns`, `ns_per_iteration`, `gbps`, `device_ns`, `device_gbps`, `tflops` and `device_tflops`
- `status` (`ok`, `error` or `unsupported`) and `error`

`gbps` is decimal: bytes per nanosecond.

To see where each cache level ends, read `l1d_bytes`, `l2_bytes` and `l3_bytes` against `size`. Apple's system-level cache is not reported, so `l3_bytes` is 0 on Apple silicon.

## Threads and copies

`read`, `write` and `copy` use one core. The `-x2`, `-x4`, `-x8` and `-all` variants run that many threads, each on its own buffers of the given size. Their `gbps` is the sum across all the threads, so the size sweep shows each core's caches with every core busy, and the largest sizes show what the whole CPU can pull from DRAM. On macOS the threads ask for performance cores.

`copy` counts the bytes copied once. The memory traffic is at least twice that: one read and one write.

## Prefill

A prompt is processed as matrix multiplies: each layer multiplies `tokens x width` activations by `width x width` weights. The prefill cases time that multiply with the vendor library, at widths of 4096 and 8192, from 1 token up to `--max-tokens`.

At a few tokens the multiply is limited by reading the weights, so its `gbps` sits near the memory ceiling. As the token count grows it becomes limited by compute instead, and `tflops` levels off at the GPU's practical peak. That peak divided by the model's FLOPs per token (about 2 x active parameters) is the prefill ceiling to set beside llama.cpp's and Skippy's prompt rates.

## What the GPU cases measure

| Case | What it times |
|---|---|
| `h2d-*`, `d2h-*`, `d2d` | `cudaMemcpyAsync` / `hipMemcpyAsync` between pageable host memory, pinned host memory and device memory |
| `h2d-zero-copy` | a kernel reading mapped pinned host memory in place |
| `global-read` | a kernel reading device memory 64 times over. The size sweep shows the L2 against DRAM. |
| `global-latency` | one thread following a random cycle through device memory, one load per 128-byte line: L1, then L2, then DRAM |
| `shared-read` | every block reading its own shared-memory tile 64 times, conflict-free. `gbps` is the whole GPU's aggregate. |
| `shared-latency` | one thread following a random cycle through shared memory |

The shared-memory sizes go up to the GPU's opt-in limit per block. Larger sizes are recorded as `unsupported`.

## Where a buffer lives

The `cuda-placement` and `hip-placement` cases ask whether a buffer the GPU uses, such as a KV pool or a recurrent-state checkpoint, could live in host-visible memory. On an integrated GPU such as the GB10 every placement is the same DRAM, so what differs is how the GPU reaches it.

| Case | What it times |
|---|---|
| `read-device`, `read-mapped`, `read-managed`, `read-system` | a kernel reading the buffer 8 times over, from `cudaMalloc`, mapped pinned host, managed, or plain `aligned_alloc` memory |
| `write-*` | a kernel writing the buffer once, in the same four placements |
| `save-memcpy-pageable`, `save-memcpy-pinned` | device to host with `cudaMemcpyAsync`, waiting for each copy as a checkpoint save does |
| `save-kernel-mapped`, `save-kernel-system` | the same save as a copy kernel writing straight into host memory |
| `restore-*` | the reverse of each save |

`read-system`, `write-system` and the `-kernel-system` cases need a GPU that can address pageable memory. Elsewhere they are recorded as `unsupported`. The plugin's facts carry `pageable_memory_access`, `pageable_uses_host_page_tables` and the managed-memory attributes, which say how the GPU reaches host memory. Read them beside the rows.

Managed memory is first touched on the host, so the warmup repetitions include any migration to the GPU.

Kernels use plain loops: ranges are not available in device code.
