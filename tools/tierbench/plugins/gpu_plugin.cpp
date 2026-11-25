#include "gpu_common.h"

#include <algorithm>
#include <bit>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <format>
#include <memory>
#include <numeric>
#include <random>
#include <ranges>
#include <string>
#include <vector>

namespace {

using namespace tierbench_gpu;

constexpr unsigned read_passes = 64;

struct Work {
    std::size_t bytes = 0;
    unsigned blocks = 0;
    Timer timer;
    Owned source{nullptr, release_pageable};
    Owned target{nullptr, release_pageable};
    Owned sink{nullptr, release_device};
    const void* device_source = nullptr; // what the GPU reads; for mapped memory this differs from source
};

std::unique_ptr<Work> start_work(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto device = static_cast<int>(env->gpu_index);
    if (!ok(GPU(SetDevice)(device), error, "set device")) {
        return nullptr;
    }
    auto work = std::make_unique<Work>();
    work->bytes = static_cast<std::size_t>(bytes);
    work->blocks = static_cast<unsigned>(std::max(attribute(gpu_attr_sm_count, device), 1)) * 8;
    work->sink = allocate(Memory::device, 64, error);
    if (!work->sink || !ok(GPU(StreamCreate)(&work->timer.stream), error, "stream")
        || !ok(GPU(EventCreate)(&work->timer.start), error, "event")
        || !ok(GPU(EventCreate)(&work->timer.stop), error, "event")) {
        return nullptr;
    }
    return work;
}

void teardown(void* state) {
    delete static_cast<Work*>(state);
}

template <Memory Source, Memory Target>
void* setup_copy(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto work = start_work(bytes, env, error);
    if (!work) {
        return nullptr;
    }
    work->source = allocate(Source, work->bytes, error);
    work->target = work->source ? allocate(Target, work->bytes, error) : Owned{nullptr, release_pageable};
    return work->source && work->target ? work.release() : nullptr;
}

int run_copy(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    return timed(work.timer, iterations, work.bytes, 0, out, error, [&] {
        return GPU(MemcpyAsync)(work.target.get(), work.source.get(), work.bytes, GPU(MemcpyDefault), work.timer.stream);
    });
}

void* setup_zero_copy(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto work = start_work(bytes, env, error);
    if (!work) {
        return nullptr;
    }
    work->source = allocate(Memory::mapped, work->bytes, error);
    void* view = nullptr;
    if (!work->source || !ok(GPU(HostGetDevicePointer)(&view, work->source.get(), 0), error, "device pointer")) {
        return nullptr;
    }
    work->device_source = view;
    work->target = allocate(Memory::device, work->bytes, error);
    return work->target ? work.release() : nullptr;
}

int run_zero_copy(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    return timed(work.timer, iterations, work.bytes, 0, out, error, [&] {
        return tierbench_gpu_pull(work.device_source, work.target.get(), work.bytes, work.timer.stream);
    });
}

void* setup_global_read(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    if (!std::has_single_bit(bytes)) {
        error->unsupported = 1;
        std::snprintf(error->message, sizeof error->message, "global-read needs a power-of-two size");
        return nullptr;
    }
    auto work = start_work(bytes, env, error);
    if (!work) {
        return nullptr;
    }
    work->source = allocate(Memory::device, work->bytes, error);
    return work->source ? work.release() : nullptr;
}

int run_global_read(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    return timed(work.timer, iterations, work.bytes * read_passes, 0, out, error, [&] {
        return tierbench_gpu_global_read(work.source.get(), work.bytes, read_passes, work.blocks, work.sink.get(),
                                         work.timer.stream);
    });
}

// a single cycle through `count` slots in random order, so every load depends on the one before it
std::vector<unsigned> cycle(std::size_t count) {
    std::vector<unsigned> order(count);
    std::iota(order.begin(), order.end(), 0u);
    std::ranges::shuffle(order, std::mt19937{42});
    std::vector<unsigned> next(count);
    std::ranges::for_each(std::views::iota(std::size_t{0}, count),
                          [&](std::size_t k) { next[order[k]] = order[(k + 1) % count]; });
    return next;
}

Owned upload(const std::vector<unsigned>& host, tierbench_error* error) {
    auto bytes = host.size() * sizeof(unsigned);
    auto device = allocate(Memory::device, bytes, error);
    if (device && !ok(GPU(Memcpy)(device.get(), host.data(), bytes, GPU(MemcpyHostToDevice)), error, "upload")) {
        return {nullptr, release_device};
    }
    return device;
}

constexpr unsigned chase_stride = 32; // one load per 128-byte line

void* setup_global_chase(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto work = start_work(bytes, env, error);
    if (!work) {
        return nullptr;
    }
    auto slots = work->bytes / (chase_stride * sizeof(unsigned));
    auto next = cycle(slots);
    std::vector<unsigned> strided(slots * chase_stride, 0u);
    std::ranges::for_each(std::views::iota(std::size_t{0}, slots),
                          [&](std::size_t k) { strided[k * chase_stride] = next[k]; });
    work->source = upload(strided, error);
    return work->source ? work.release() : nullptr;
}

int run_global_chase(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    return timed(work.timer, 1, 0, 0, out, error, [&] {
        return tierbench_gpu_global_chase(static_cast<const unsigned*>(work.source.get()), chase_stride, iterations,
                                          work.sink.get(), work.timer.stream);
    });
}

std::unique_ptr<Work> begin_shared(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto limit = static_cast<std::uint64_t>(attribute(gpu_attr_shared_optin, static_cast<int>(env->gpu_index)));
    if (bytes > limit || !std::has_single_bit(bytes)) {
        error->unsupported = 1;
        std::snprintf(error->message, sizeof error->message, "%llu bytes of shared memory per block; this GPU allows %llu",
                      static_cast<unsigned long long>(bytes), static_cast<unsigned long long>(limit));
        return nullptr;
    }
    auto work = start_work(bytes, env, error);
    if (!work || !ok(tierbench_gpu_shared_prepare(static_cast<unsigned>(bytes)), error, "shared memory opt-in")) {
        return nullptr;
    }
    return work;
}

void* setup_shared_read(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    return begin_shared(bytes, env, error).release();
}

int run_shared_read(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    return timed(work.timer, iterations, work.bytes * read_passes * work.blocks, 0, out, error, [&] {
        return tierbench_gpu_shared_read(static_cast<unsigned>(work.bytes), read_passes, work.blocks, work.sink.get(),
                                         work.timer.stream);
    });
}

void* setup_shared_chase(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto work = begin_shared(bytes, env, error);
    if (!work) {
        return nullptr;
    }
    work->source = upload(cycle(work->bytes / sizeof(unsigned)), error);
    return work->source ? work.release() : nullptr;
}

int run_shared_chase(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    return timed(work.timer, 1, 0, 0, out, error, [&] {
        return tierbench_gpu_shared_chase(static_cast<const unsigned*>(work.source.get()),
                                          static_cast<unsigned>(work.bytes), iterations, work.sink.get(),
                                          work.timer.stream);
    });
}

constexpr std::uint64_t large = std::uint64_t{1} << 34;
constexpr std::uint64_t shared_max = std::uint64_t{1} << 18;

constexpr tierbench_case cases[] = {
    {"h2d-pageable", "memcpy-pageable", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_copy<Memory::pageable, Memory::device>, run_copy, teardown},
    {"h2d-pinned", "memcpy-pinned", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_copy<Memory::pinned, Memory::device>, run_copy, teardown},
    {"d2h-pageable", "memcpy-pageable", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_copy<Memory::device, Memory::pageable>, run_copy, teardown},
    {"d2h-pinned", "memcpy-pinned", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_copy<Memory::device, Memory::pinned>, run_copy, teardown},
    {"d2d", "memcpy-device", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_copy<Memory::device, Memory::device>, run_copy, teardown},
    {"h2d-zero-copy", "kernel-reads-mapped-host", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large, setup_zero_copy,
     run_zero_copy, teardown},
    {"global-read", "kernel-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, large, setup_global_read, run_global_read,
     teardown},
    {"global-latency", "kernel-dependent-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, large, setup_global_chase,
     run_global_chase, teardown},
    {"shared-read", "shared-memory-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, 1024, shared_max, setup_shared_read,
     run_shared_read, teardown},
    {"shared-latency", "shared-memory-dependent-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, 1024, shared_max,
     setup_shared_chase, run_shared_chase, teardown},
};

std::string facts_text;

const char* facts(const tierbench_env* env) {
    auto device = static_cast<int>(env->gpu_index);
    gpu_device_prop props{};
    int driver = 0;
    int runtime = 0;
    if (GPU(GetDeviceProperties)(&props, device) != GPU(Success) || GPU(DriverGetVersion)(&driver) != GPU(Success)
        || GPU(RuntimeGetVersion)(&runtime) != GPU(Success)) {
        facts_text = std::format(R"({{"api":"{}","error":"device query failed"}})", gpu_api_name);
        return facts_text.c_str();
    }
#if defined(TIERBENCH_HIP)
    std::string arch = props.gcnArchName;
#else
    std::string arch = std::format("sm_{}{}", props.major, props.minor);
#endif
    facts_text = std::format(
        R"({{"api":"{}","gpu":"{}","arch":"{}","integrated":{},"global_memory_bytes":{},"sm_count":{},)"
        R"("shared_per_block_optin":{},"driver_version":{},"runtime_version":{}}})",
        gpu_api_name, props.name, arch, attribute(gpu_attr_integrated, device) ? "true" : "false", props.totalGlobalMem,
        attribute(gpu_attr_sm_count, device), attribute(gpu_attr_shared_optin, device), driver, runtime);
    return facts_text.c_str();
}

constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, gpu_api_name, facts,
                                  static_cast<std::uint32_t>(std::size(cases)), cases};

}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) {
    return &plugin;
}
