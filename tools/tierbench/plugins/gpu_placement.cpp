#include "gpu_common.h"

#include <algorithm>
#include <bit>
#include <cstdint>
#include <cstdio>
#include <format>
#include <memory>
#include <ranges>
#include <string>

namespace {

using namespace tierbench_gpu;

#if defined(TIERBENCH_HIP)
constexpr const char* plugin_name = "hip-placement";
#else
constexpr const char* plugin_name = "cuda-placement";
#endif

constexpr unsigned read_passes = 8;
constexpr std::uint64_t large = std::uint64_t{1} << 34;

enum class Placement { device, mapped, managed, system };

constexpr Memory memory_of(Placement where) {
    switch (where) {
    case Placement::device: return Memory::device;
    case Placement::mapped: return Memory::mapped;
    case Placement::managed: return Memory::managed;
    case Placement::system: return Memory::pageable;
    }
    return Memory::device;
}

struct Placed {
    std::size_t bytes = 0;
    std::size_t host_page = page;
    unsigned blocks = 0;
    Timer timer;
    Owned memory{nullptr, release_pageable};
    void* view = nullptr; // the address kernels use for `memory`
    Owned device{nullptr, release_device};
    Owned sink{nullptr, release_device};
};

void teardown(void* state) {
    delete static_cast<Placed*>(state);
}

std::unique_ptr<Placed> start(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto device = static_cast<int>(env->gpu_index);
    if (!ok(GPU(SetDevice)(device), error, "set device")) {
        return nullptr;
    }
    auto work = std::make_unique<Placed>();
    work->bytes = static_cast<std::size_t>(bytes);
    work->host_page = env->page_size ? static_cast<std::size_t>(env->page_size) : page;
    work->blocks = static_cast<unsigned>(std::max(attribute(gpu_attr_sm_count, device), 1)) * 8;
    work->sink = allocate(Memory::device, 64, error);
    if (!work->sink || !start_timer(work->timer, error)) {
        return nullptr;
    }
    return work;
}

enum class Access { kernel, copy };

bool place(Placement where, Access access, Placed& work, const tierbench_env* env, tierbench_error* error) {
    if (access == Access::kernel && where == Placement::system
        && !attribute(gpu_attr_pageable_access, static_cast<int>(env->gpu_index))) {
        error->unsupported = 1;
        std::snprintf(error->message, sizeof error->message, "this GPU cannot address pageable host memory");
        return false;
    }
    work.memory = allocate(memory_of(where), work.bytes, error);
    if (!work.memory) {
        return false;
    }
    if (where != Placement::mapped) {
        work.view = work.memory.get();
        return true;
    }
    return ok(GPU(HostGetDevicePointer)(&work.view, work.memory.get(), 0), error, "device pointer");
}

template <Placement Where>
void* setup_access(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    if (!std::has_single_bit(bytes)) {
        error->unsupported = 1;
        std::snprintf(error->message, sizeof error->message, "kernel access needs a power-of-two size");
        return nullptr;
    }
    auto work = start(bytes, env, error);
    return work && place(Where, Access::kernel, *work, env, error) ? work.release() : nullptr;
}

int run_read(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Placed*>(state);
    return timed(work.timer, iterations, work.bytes * read_passes, 0, out, error, [&] {
        return tierbench_gpu_global_read(work.view, work.bytes, read_passes, work.blocks, work.sink.get(),
                                         work.timer.stream);
    });
}

int run_write(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Placed*>(state);
    return timed(work.timer, iterations, work.bytes, 0, out, error, [&] {
        return tierbench_gpu_global_write(work.view, work.bytes, work.blocks, work.timer.stream);
    });
}

template <Placement Where, Access How>
void* setup_transfer(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto work = start(bytes, env, error);
    if (!work || !place(Where, How, *work, env, error)) {
        return nullptr;
    }
    work->device = allocate(Memory::device, work->bytes, error);
    return work->device ? work.release() : nullptr;
}

volatile unsigned char host_sink = 0;

auto host_pages(const Placed& work) {
    return std::views::iota(std::size_t{0}, (work.bytes + work.host_page - 1) / work.host_page)
         | std::views::transform([&work](std::size_t i) { return i * work.host_page; });
}

// the host uses what a save produced, so pages the GPU pulled across come back
void host_read(const Placed& work) {
    auto bytes = static_cast<const volatile unsigned char*>(work.memory.get());
    unsigned char acc = 0;
    std::ranges::for_each(host_pages(work), [&](std::size_t at) { acc ^= bytes[at]; });
    host_sink = acc;
}

// the host produced what a restore consumes, so its pages start on the host
void host_write(Placed& work) {
    auto bytes = static_cast<volatile unsigned char*>(work.memory.get());
    std::ranges::for_each(host_pages(work), [&](std::size_t at) { bytes[at] = static_cast<unsigned char>(at); });
}

template <class Copy>
GPU(Error_t) save(Placed& work, Copy copy) {
    auto status = copy();
    if (status == GPU(Success)) {
        status = GPU(StreamSynchronize)(work.timer.stream);
    }
    if (status == GPU(Success)) {
        host_read(work);
    }
    return status;
}

template <class Copy>
GPU(Error_t) restore(Placed& work, Copy copy) {
    host_write(work);
    auto status = copy();
    return status == GPU(Success) ? GPU(StreamSynchronize)(work.timer.stream) : status;
}

template <class Step>
int per_iteration(Placed& work, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error, Step step) {
    auto each = std::views::iota(std::uint64_t{0}, iterations);
    auto failed = std::ranges::find_if(each, [&](std::uint64_t) { return !ok(step(), error, "save or restore"); });
    if (failed != each.end()) {
        return 1;
    }
    out->bytes_per_iteration = work.bytes;
    return 0;
}

GPU(Error_t) pull(const void* source, void* target, Placed& work) {
    return static_cast<GPU(Error_t)>(tierbench_gpu_pull(source, target, work.bytes, work.timer.stream));
}

int run_save_memcpy(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Placed*>(state);
    return per_iteration(work, iterations, out, error, [&] {
        return save(work, [&] {
            return GPU(MemcpyAsync)(work.memory.get(), work.device.get(), work.bytes, GPU(MemcpyDefault),
                                    work.timer.stream);
        });
    });
}

int run_save_kernel(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Placed*>(state);
    return per_iteration(work, iterations, out, error,
                         [&] { return save(work, [&] { return pull(work.device.get(), work.view, work); }); });
}

int run_restore_memcpy(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Placed*>(state);
    return per_iteration(work, iterations, out, error, [&] {
        return restore(work, [&] {
            return GPU(MemcpyAsync)(work.device.get(), work.memory.get(), work.bytes, GPU(MemcpyDefault),
                                    work.timer.stream);
        });
    });
}

int run_restore_kernel(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Placed*>(state);
    return per_iteration(work, iterations, out, error,
                         [&] { return restore(work, [&] { return pull(work.view, work.device.get(), work); }); });
}

constexpr tierbench_case cases[] = {
    {"read-device", "kernel-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_access<Placement::device>, run_read, teardown},
    {"read-mapped", "kernel-load-mapped-pinned", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_access<Placement::mapped>, run_read, teardown},
    {"read-managed", "kernel-load-managed", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_access<Placement::managed>, run_read, teardown},
    {"read-system", "kernel-load-pageable", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_access<Placement::system>, run_read, teardown},
    {"write-device", "kernel-store", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_access<Placement::device>, run_write, teardown},
    {"write-mapped", "kernel-store-mapped-pinned", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_access<Placement::mapped>, run_write, teardown},
    {"write-managed", "kernel-store-managed", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_access<Placement::managed>, run_write, teardown},
    {"write-system", "kernel-store-pageable", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_access<Placement::system>, run_write, teardown},
    {"save-memcpy-pageable", "memcpy-sync-pageable", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_transfer<Placement::system, Access::copy>, run_save_memcpy, teardown},
    {"save-memcpy-pinned", "memcpy-sync-pinned", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_transfer<Placement::mapped, Access::copy>, run_save_memcpy, teardown},
    {"save-kernel-mapped", "kernel-copy-sync-mapped-pinned", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page,
     large, setup_transfer<Placement::mapped, Access::kernel>, run_save_kernel, teardown},
    {"save-kernel-managed", "kernel-copy-sync-managed", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_transfer<Placement::managed, Access::kernel>, run_save_kernel, teardown},
    {"save-kernel-system", "kernel-copy-sync-pageable", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_transfer<Placement::system, Access::kernel>, run_save_kernel, teardown},
    {"restore-memcpy-pageable", "memcpy-sync-pageable", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_transfer<Placement::system, Access::copy>, run_restore_memcpy, teardown},
    {"restore-memcpy-pinned", "memcpy-sync-pinned", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_transfer<Placement::mapped, Access::copy>, run_restore_memcpy, teardown},
    {"restore-kernel-mapped", "kernel-copy-sync-mapped-pinned", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page,
     large, setup_transfer<Placement::mapped, Access::kernel>, run_restore_kernel, teardown},
    {"restore-kernel-managed", "kernel-copy-sync-managed", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page,
     large, setup_transfer<Placement::managed, Access::kernel>, run_restore_kernel, teardown},
    {"restore-kernel-system", "kernel-copy-sync-pageable", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page,
     large, setup_transfer<Placement::system, Access::kernel>, run_restore_kernel, teardown},
};

std::string facts_text;

const char* facts(const tierbench_env* env) {
    auto device = static_cast<int>(env->gpu_index);
    gpu_device_prop props{};
    if (GPU(GetDeviceProperties)(&props, device) != GPU(Success)) {
        facts_text = std::format(R"({{"api":"{}","error":"device query failed"}})", gpu_api_name);
        return facts_text.c_str();
    }
    auto flag = [&](gpu_device_attr attr) { return attribute(attr, device) ? "true" : "false"; };
    facts_text = std::format(
        R"({{"api":"{}","gpu":"{}","integrated":{},"global_memory_bytes":{},"pageable_memory_access":{},)"
        R"("pageable_uses_host_page_tables":{},"concurrent_managed_access":{},"registered_host_pointer":{},)"
        R"("managed_access_from_host":{}}})",
        gpu_api_name, props.name, flag(gpu_attr_integrated), props.totalGlobalMem, flag(gpu_attr_pageable_access),
        flag(gpu_attr_pageable_host_tables), flag(gpu_attr_concurrent_managed), flag(gpu_attr_registered_host_pointer),
        flag(gpu_attr_managed_from_host));
    return facts_text.c_str();
}

constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, plugin_name, facts,
                                  static_cast<std::uint32_t>(std::size(cases)), cases};

}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) {
    return &plugin;
}
