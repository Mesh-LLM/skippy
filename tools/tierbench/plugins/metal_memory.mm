#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "tierbench.h"

#include <fcntl.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <bit>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <format>
#include <memory>
#include <numeric>
#include <random>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr NSUInteger group_threads = 256;
constexpr NSUInteger read_groups = 1024;
constexpr std::uint32_t read_passes = 16;
constexpr std::uint32_t chase_stride = 32;

constexpr const char* kernels = R"metal(
#include <metal_stdlib>
using namespace metal;

constant uint never = 0x9e3779b9u;

kernel void global_read(device const uint4* data [[buffer(0)]], constant uint& mask [[buffer(1)]],
                        constant uint& passes [[buffer(2)]], device uint* sink [[buffer(3)]],
                        uint id [[thread_position_in_grid]], uint stride [[threads_per_grid]]) {
    uint acc = 0;
    for (uint pass = 0; pass < passes; ++pass) {
        for (uint i = id; i <= mask; i += stride) {
            uint4 v = data[(i + pass * 32) & mask];
            acc ^= v.x ^ v.y ^ v.z ^ v.w;
        }
    }
    if (acc == never) {
        sink[0] = acc;
    }
}

kernel void global_chase(device const uint* next [[buffer(0)]], constant uint& stride [[buffer(1)]],
                         constant ulong& steps [[buffer(2)]], device uint* sink [[buffer(3)]]) {
    uint at = 0;
    for (ulong step = 0; step < steps; ++step) {
        at = next[at * stride];
    }
    sink[0] = at;
}

kernel void group_read(constant uint& words [[buffer(0)]], constant uint& passes [[buffer(1)]],
                       device uint* sink [[buffer(2)]], threadgroup uint* tile [[threadgroup(0)]],
                       uint tid [[thread_index_in_threadgroup]], uint count [[threads_per_threadgroup]]) {
    for (uint i = tid; i < words; i += count) {
        tile[i] = i * 2654435761u;
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    uint acc = 0;
    for (uint pass = 0; pass < passes; ++pass) {
        for (uint i = tid; i < words; i += count) {
            acc ^= tile[(i + pass * 32) & (words - 1)];
        }
    }
    if (acc == never) {
        sink[0] = acc;
    }
}

kernel void group_chase(device const uint* order [[buffer(0)]], constant uint& words [[buffer(1)]],
                        constant ulong& steps [[buffer(2)]], device uint* sink [[buffer(3)]],
                        threadgroup uint* chain [[threadgroup(0)]], uint tid [[thread_index_in_threadgroup]],
                        uint count [[threads_per_threadgroup]]) {
    for (uint i = tid; i < words; i += count) {
        chain[i] = order[i];
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    if (tid != 0) {
        return;
    }
    uint at = 0;
    for (ulong step = 0; step < steps; ++step) {
        at = chain[at];
    }
    sink[0] = at;
}
)metal";

std::string describe(NSError* error) {
    return error ? std::string{error.localizedDescription.UTF8String} : std::string{"unknown Metal error"};
}

void say(tierbench_error* error, std::string_view text) {
    std::snprintf(error->message, sizeof error->message, "%.*s", static_cast<int>(text.size()), text.data());
}

struct Gpu {
    id<MTLDevice> device = nil;
    id<MTLCommandQueue> queue = nil;
    id<MTLComputePipelineState> global_read = nil;
    id<MTLComputePipelineState> global_chase = nil;
    id<MTLComputePipelineState> group_read = nil;
    id<MTLComputePipelineState> group_chase = nil;
};

struct Prepared {
    std::unique_ptr<Gpu> gpu;
    std::string error;
};

Prepared prepare() {
    @autoreleasepool {
        auto gpu = std::make_unique<Gpu>();
        gpu->device = MTLCreateSystemDefaultDevice();
        if (!gpu->device) {
            return {nullptr, "no Metal device"};
        }
        gpu->queue = [gpu->device newCommandQueue];
        NSError* error = nil;
        id<MTLLibrary> library = [gpu->device newLibraryWithSource:[NSString stringWithUTF8String:kernels]
                                                          options:nil
                                                            error:&error];
        if (!library) {
            return {nullptr, describe(error)};
        }
        auto pipeline = [&](const char* name) -> id<MTLComputePipelineState> {
            id<MTLFunction> function = [library newFunctionWithName:[NSString stringWithUTF8String:name]];
            NSError* why = nil;
            id<MTLComputePipelineState> state =
                function ? [gpu->device newComputePipelineStateWithFunction:function error:&why] : nil;
            if (!state) {
                error = why;
            }
            return state;
        };
        gpu->global_read = pipeline("global_read");
        gpu->global_chase = pipeline("global_chase");
        gpu->group_read = pipeline("group_read");
        gpu->group_chase = pipeline("group_chase");
        if (!gpu->queue || !gpu->global_read || !gpu->global_chase || !gpu->group_read || !gpu->group_chase) {
            return {nullptr, describe(error)};
        }
        return {std::move(gpu), {}};
    }
}

Gpu* acquire(tierbench_error* error) {
    static Prepared prepared = prepare();
    if (!prepared.gpu) {
        say(error, prepared.error);
    }
    return prepared.gpu.get();
}

std::atomic<unsigned> serial{0};

struct Work {
    Gpu* gpu = nullptr;
    std::size_t bytes = 0;
    id<MTLBuffer> source = nil;
    id<MTLBuffer> target = nil;
    id<MTLBuffer> sink = nil;
    id<MTLIOCommandQueue> io = nil;
    id<MTLIOFileHandle> file = nil;
    std::string path;

    ~Work() {
        if (!path.empty()) {
            unlink(path.c_str());
        }
    }
};

enum class Storage { shared, device_only, nocopy };

void repeat(std::uint64_t times, auto&& body) {
    std::ranges::for_each(std::views::iota(std::uint64_t{0}, times), [&](std::uint64_t) { body(); });
}

template <class Encode>
int submit(Work& work, std::uint64_t bytes_per_iteration, tierbench_measure* out, tierbench_error* error,
           Encode encode) {
    @autoreleasepool {
        id<MTLCommandBuffer> buffer = [work.gpu->queue commandBuffer];
        encode(buffer);
        [buffer commit];
        [buffer waitUntilCompleted];
        if (buffer.status != MTLCommandBufferStatusCompleted) {
            say(error, describe(buffer.error));
            return 1;
        }
        out->bytes_per_iteration = bytes_per_iteration;
        out->device_ns = static_cast<std::uint64_t>((buffer.GPUEndTime - buffer.GPUStartTime) * 1e9);
        out->has_device_ns = 1;
        return 0;
    }
}

id<MTLBuffer> make_buffer(Work& work, Storage storage, std::size_t bytes, tierbench_error* error) {
    id<MTLDevice> device = work.gpu->device;
    id<MTLBuffer> buffer = nil;
    switch (storage) {
    case Storage::shared:
        buffer = [device newBufferWithLength:bytes options:MTLResourceStorageModeShared];
        if (buffer) {
            std::memset(buffer.contents, 1, bytes);
        }
        break;
    case Storage::device_only:
        buffer = [device newBufferWithLength:bytes options:MTLResourceStorageModePrivate];
        if (buffer) {
            tierbench_measure ignored{};
            if (submit(work, 0, &ignored, error, [&](id<MTLCommandBuffer> commands) {
                    id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
                    [blit fillBuffer:buffer range:NSMakeRange(0, bytes) value:1];
                    [blit endEncoding];
                }) != 0) {
                return nil;
            }
        }
        break;
    case Storage::nocopy: {
        auto page = static_cast<std::size_t>(getpagesize());
        void* host = std::aligned_alloc(page, bytes);
        if (!host) {
            break;
        }
        std::memset(host, 1, bytes);
        buffer = [device newBufferWithBytesNoCopy:host
                                           length:bytes
                                          options:MTLResourceStorageModeShared
                                      deallocator:^(void* pointer, NSUInteger) { std::free(pointer); }];
        if (!buffer) {
            std::free(host);
        }
        break;
    }
    }
    if (!buffer && error->message[0] == '\0') {
        say(error, "buffer allocation failed");
    }
    return buffer;
}

std::unique_ptr<Work> start(std::uint64_t bytes, tierbench_error* error) {
    auto gpu = acquire(error);
    if (!gpu) {
        return nullptr;
    }
    auto work = std::make_unique<Work>();
    work->gpu = gpu;
    work->bytes = static_cast<std::size_t>(bytes);
    work->sink = make_buffer(*work, Storage::shared, 64, error);
    return work->sink ? std::move(work) : nullptr;
}

bool power_of_two(std::uint64_t bytes, tierbench_error* error) {
    if (std::has_single_bit(bytes)) {
        return true;
    }
    error->unsupported = 1;
    say(error, "this case needs a power-of-two size");
    return false;
}

void teardown(void* state) {
    delete static_cast<Work*>(state);
}

template <Storage From, Storage To>
void* setup_blit(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    auto work = start(bytes, error);
    if (!work || !(work->source = make_buffer(*work, From, work->bytes, error))
        || !(work->target = make_buffer(*work, To, work->bytes, error))) {
        return nullptr;
    }
    return work.release();
}

int run_blit(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    return submit(work, work.bytes, out, error, [&](id<MTLCommandBuffer> commands) {
        id<MTLBlitCommandEncoder> blit = [commands blitCommandEncoder];
        repeat(iterations, [&] {
            [blit copyFromBuffer:work.source sourceOffset:0 toBuffer:work.target destinationOffset:0 size:work.bytes];
        });
        [blit endEncoding];
    });
}

template <Storage S>
void* setup_read(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    if (!power_of_two(bytes, error)) {
        return nullptr;
    }
    if (S == Storage::nocopy && bytes % static_cast<std::uint64_t>(getpagesize()) != 0) {
        error->unsupported = 1;
        say(error, "a no-copy buffer must be a whole number of pages");
        return nullptr;
    }
    auto work = start(bytes, error);
    if (!work || !(work->source = make_buffer(*work, S, work->bytes, error))) {
        return nullptr;
    }
    return work.release();
}

int run_read(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    auto count = work.bytes / 16;
    auto mask = static_cast<std::uint32_t>(count - 1);
    auto passes = read_passes;
    auto grid = std::min<NSUInteger>(count, NSUInteger{1} << 20);
    return submit(work, work.bytes * read_passes, out, error, [&](id<MTLCommandBuffer> commands) {
        id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
        [encoder setComputePipelineState:work.gpu->global_read];
        [encoder setBuffer:work.source offset:0 atIndex:0];
        [encoder setBytes:&mask length:sizeof mask atIndex:1];
        [encoder setBytes:&passes length:sizeof passes atIndex:2];
        [encoder setBuffer:work.sink offset:0 atIndex:3];
        repeat(iterations, [&] {
            [encoder dispatchThreads:MTLSizeMake(grid, 1, 1) threadsPerThreadgroup:MTLSizeMake(group_threads, 1, 1)];
        });
        [encoder endEncoding];
    });
}

// a single cycle through `count` slots in random order, so every load depends on the one before it
std::vector<std::uint32_t> cycle(std::size_t count) {
    std::vector<std::uint32_t> order(count);
    std::iota(order.begin(), order.end(), 0u);
    std::ranges::shuffle(order, std::mt19937{42});
    std::vector<std::uint32_t> next(count);
    std::ranges::for_each(std::views::iota(std::size_t{0}, count),
                          [&](std::size_t k) { next[order[k]] = order[(k + 1) % count]; });
    return next;
}

void* setup_chase(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    auto work = start(bytes, error);
    if (!work || !(work->source = make_buffer(*work, Storage::shared, work->bytes, error))) {
        return nullptr;
    }
    auto slots = work->bytes / (chase_stride * sizeof(std::uint32_t));
    auto next = cycle(slots);
    std::span strided{static_cast<std::uint32_t*>(work->source.contents), work->bytes / sizeof(std::uint32_t)};
    std::ranges::fill(strided, 0u);
    std::ranges::for_each(std::views::iota(std::size_t{0}, slots),
                          [&](std::size_t k) { strided[k * chase_stride] = next[k]; });
    return work.release();
}

int run_chase(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    auto stride = chase_stride;
    std::uint64_t steps = iterations;
    return submit(work, 0, out, error, [&](id<MTLCommandBuffer> commands) {
        id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
        [encoder setComputePipelineState:work.gpu->global_chase];
        [encoder setBuffer:work.source offset:0 atIndex:0];
        [encoder setBytes:&stride length:sizeof stride atIndex:1];
        [encoder setBytes:&steps length:sizeof steps atIndex:2];
        [encoder setBuffer:work.sink offset:0 atIndex:3];
        [encoder dispatchThreads:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
        [encoder endEncoding];
    });
}

std::unique_ptr<Work> start_group(std::uint64_t bytes, tierbench_error* error) {
    if (!power_of_two(bytes, error)) {
        return nullptr;
    }
    auto work = start(bytes, error);
    if (!work) {
        return nullptr;
    }
    auto limit = static_cast<std::uint64_t>(work->gpu->device.maxThreadgroupMemoryLength);
    if (bytes > limit) {
        error->unsupported = 1;
        say(error, std::format("{} bytes of threadgroup memory; this GPU allows {}", bytes, limit));
        return nullptr;
    }
    return work;
}

void* setup_group_read(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    return start_group(bytes, error).release();
}

int run_group_read(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    auto words = static_cast<std::uint32_t>(work.bytes / sizeof(std::uint32_t));
    auto passes = read_passes * 4;
    return submit(work, work.bytes * passes * read_groups, out, error, [&](id<MTLCommandBuffer> commands) {
        id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
        [encoder setComputePipelineState:work.gpu->group_read];
        [encoder setBytes:&words length:sizeof words atIndex:0];
        [encoder setBytes:&passes length:sizeof passes atIndex:1];
        [encoder setBuffer:work.sink offset:0 atIndex:2];
        [encoder setThreadgroupMemoryLength:work.bytes atIndex:0];
        repeat(iterations, [&] {
            [encoder dispatchThreadgroups:MTLSizeMake(read_groups, 1, 1)
                    threadsPerThreadgroup:MTLSizeMake(group_threads, 1, 1)];
        });
        [encoder endEncoding];
    });
}

void* setup_group_chase(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    auto work = start_group(bytes, error);
    if (!work || !(work->source = make_buffer(*work, Storage::shared, work->bytes, error))) {
        return nullptr;
    }
    auto next = cycle(work->bytes / sizeof(std::uint32_t));
    std::ranges::copy(next, static_cast<std::uint32_t*>(work->source.contents));
    return work.release();
}

int run_group_chase(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    auto words = static_cast<std::uint32_t>(work.bytes / sizeof(std::uint32_t));
    std::uint64_t steps = iterations;
    return submit(work, 0, out, error, [&](id<MTLCommandBuffer> commands) {
        id<MTLComputeCommandEncoder> encoder = [commands computeCommandEncoder];
        [encoder setComputePipelineState:work.gpu->group_chase];
        [encoder setBuffer:work.source offset:0 atIndex:0];
        [encoder setBytes:&words length:sizeof words atIndex:1];
        [encoder setBytes:&steps length:sizeof steps atIndex:2];
        [encoder setBuffer:work.sink offset:0 atIndex:3];
        [encoder setThreadgroupMemoryLength:work.bytes atIndex:0];
        [encoder dispatchThreadgroups:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(group_threads, 1, 1)];
        [encoder endEncoding];
    });
}

bool write_all(int fd, const std::byte* data, std::size_t size, off_t offset) {
    if (size == 0) {
        return true;
    }
    auto written = pwrite(fd, data, size, offset);
    if (written <= 0) {
        return false;
    }
    auto done = static_cast<std::size_t>(written);
    return write_all(fd, data + done, size - done, offset + static_cast<off_t>(done));
}

void* setup_io(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto work = start(bytes, error);
    if (!work || !(work->target = make_buffer(*work, Storage::device_only, work->bytes, error))) {
        return nullptr;
    }
    id<MTLBuffer> staging = make_buffer(*work, Storage::shared, work->bytes, error);
    if (!staging) {
        return nullptr;
    }
    work->path = std::format("{}/tierbench-metal-{}-{}.dat", env->scratch_dir, getpid(), serial.fetch_add(1));
    int fd = open(work->path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    bool written = fd >= 0 && write_all(fd, static_cast<const std::byte*>(staging.contents), work->bytes, 0)
                && fcntl(fd, F_FULLFSYNC) == 0;
    if (fd >= 0) {
        close(fd);
    }
    if (!written) {
        say(error, std::format("cannot write {}: {}", work->path, std::strerror(errno)));
        return nullptr;
    }
    @autoreleasepool {
        NSError* why = nil;
        id<MTLDevice> device = work->gpu->device;
        work->io = [device newIOCommandQueueWithDescriptor:[MTLIOCommandQueueDescriptor new] error:&why];
        if (work->io) {
            NSURL* url = [NSURL fileURLWithPath:@(work->path.c_str())];
            if (@available(macOS 14.0, *)) {
                work->file = [device newIOFileHandleWithURL:url error:&why];
            } else {
                work->file = [device newIOHandleWithURL:url error:&why];
            }
        }
        if (!work->io || !work->file) {
            say(error, describe(why));
            return nullptr;
        }
    }
    return work.release();
}

int run_io(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& work = *static_cast<Work*>(state);
    @autoreleasepool {
        id<MTLIOCommandBuffer> commands = [work.io commandBuffer];
        repeat(iterations, [&] {
            [commands loadBuffer:work.target offset:0 size:work.bytes sourceHandle:work.file sourceHandleOffset:0];
        });
        [commands commit];
        [commands waitUntilCompleted];
        if (commands.status != MTLIOStatusComplete) {
            say(error, describe(commands.error));
            return 1;
        }
    }
    out->bytes_per_iteration = work.bytes;
    return 0;
}

constexpr std::uint64_t page = 4096;
constexpr std::uint64_t large = std::uint64_t{1} << 34;
constexpr std::uint64_t group_max = std::uint64_t{1} << 16;

constexpr tierbench_case cases[] = {
    {"blit-shared-to-private", "blit", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_blit<Storage::shared, Storage::device_only>, run_blit, teardown},
    {"blit-private-to-shared", "blit", TIERBENCH_GPU, TIERBENCH_HOST, TIERBENCH_BYTES, page, large,
     setup_blit<Storage::device_only, Storage::shared>, run_blit, teardown},
    {"blit-private-to-private", "blit", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_blit<Storage::device_only, Storage::device_only>, run_blit, teardown},
    {"read-private", "kernel-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, large, setup_read<Storage::device_only>,
     run_read, teardown},
    {"read-shared", "kernel-load", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large, setup_read<Storage::shared>, run_read,
     teardown},
    {"read-nocopy", "kernel-load-bytes-no-copy", TIERBENCH_HOST, TIERBENCH_GPU, TIERBENCH_BYTES, page, large,
     setup_read<Storage::nocopy>, run_read, teardown},
    {"latency", "kernel-dependent-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, page, std::uint64_t{1} << 33, setup_chase,
     run_chase, teardown},
    {"threadgroup-read", "threadgroup-memory-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, 1024, group_max, setup_group_read,
     run_group_read, teardown},
    {"threadgroup-latency", "threadgroup-memory-dependent-load", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_BYTES, 1024, group_max,
     setup_group_chase, run_group_chase, teardown},
    {"mtlio-load", "MTLIOCommandQueue", TIERBENCH_STORAGE, TIERBENCH_GPU, TIERBENCH_BYTES, page, large, setup_io, run_io, teardown},
};

std::string facts_text;

const char* facts(const tierbench_env*) {
    tierbench_error ignored{};
    Gpu* gpu = acquire(&ignored);
    if (!gpu) {
        facts_text = std::format(R"({{"api":"metal","error":"{}"}})", ignored.message);
        return facts_text.c_str();
    }
    facts_text = std::format(
        R"({{"api":"metal","gpu":"{}","unified_memory":{},"recommended_working_set_bytes":{},"max_threadgroup_memory":{}}})",
        gpu->device.name.UTF8String, gpu->device.hasUnifiedMemory ? "true" : "false",
        gpu->device.recommendedMaxWorkingSetSize, gpu->device.maxThreadgroupMemoryLength);
    return facts_text.c_str();
}

constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, "metal", facts,
                                  static_cast<std::uint32_t>(std::size(cases)), cases};

}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) {
    return &plugin;
}
