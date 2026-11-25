#include "gpu_api.h"

namespace {

constexpr unsigned threads = 256;
constexpr unsigned never = 0x9e3779b9u;

__global__ void pull(const uint4* __restrict__ source, uint4* __restrict__ target, std::size_t count) {
    std::size_t index = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index < count) {
        target[index] = source[index];
    }
}

// count is a power of two; each pass starts 32 elements later, so no pass repeats another's loads
__global__ void global_read(const uint4* __restrict__ data, std::size_t count, unsigned passes, unsigned* sink) {
    unsigned acc = 0;
    std::size_t first = static_cast<std::size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    std::size_t stride = static_cast<std::size_t>(gridDim.x) * blockDim.x;
    for (unsigned pass = 0; pass < passes; ++pass) {
        for (std::size_t i = first; i < count; i += stride) {
            uint4 v = data[(i + static_cast<std::size_t>(pass) * 32) & (count - 1)];
            acc ^= v.x ^ v.y ^ v.z ^ v.w;
        }
    }
    if (acc == never) {
        *sink = acc;
    }
}

__global__ void global_chase(const unsigned* __restrict__ next, unsigned stride, unsigned long long steps,
                             unsigned* sink) {
    unsigned at = 0;
    for (unsigned long long step = 0; step < steps; ++step) {
        at = next[static_cast<std::size_t>(at) * stride];
    }
    *sink = at;
}

__global__ void shared_read(unsigned words, unsigned passes, unsigned* sink) {
    extern __shared__ unsigned tile[];
    for (unsigned i = threadIdx.x; i < words; i += blockDim.x) {
        tile[i] = i * 2654435761u;
    }
    __syncthreads();
    unsigned acc = 0;
    for (unsigned pass = 0; pass < passes; ++pass) {
        for (unsigned i = threadIdx.x; i < words; i += blockDim.x) {
            acc ^= tile[(i + pass * 32) & (words - 1)];
        }
    }
    if (acc == never) {
        *sink = acc;
    }
}

__global__ void shared_chase(const unsigned* __restrict__ order, unsigned words, unsigned long long steps,
                             unsigned* sink) {
    extern __shared__ unsigned chain[];
    for (unsigned i = threadIdx.x; i < words; i += blockDim.x) {
        chain[i] = order[i];
    }
    __syncthreads();
    if (threadIdx.x != 0) {
        return;
    }
    unsigned at = 0;
    for (unsigned long long step = 0; step < steps; ++step) {
        at = chain[at];
    }
    *sink = at;
}

GPU(Stream_t) as_stream(void* stream) {
    return static_cast<GPU(Stream_t)>(stream);
}

int last_error() {
    return static_cast<int>(GPU(GetLastError)());
}

}

extern "C" int tierbench_gpu_pull(const void* source, void* target, std::size_t bytes, void* stream) {
    auto count = bytes / sizeof(uint4);
    auto blocks = static_cast<unsigned>((count + threads - 1) / threads);
    pull<<<blocks, threads, 0, as_stream(stream)>>>(static_cast<const uint4*>(source), static_cast<uint4*>(target),
                                                    count);
    return last_error();
}

extern "C" int tierbench_gpu_global_read(const void* data, std::size_t bytes, unsigned passes, unsigned blocks,
                                         void* sink, void* stream) {
    global_read<<<blocks, threads, 0, as_stream(stream)>>>(static_cast<const uint4*>(data), bytes / sizeof(uint4),
                                                           passes, static_cast<unsigned*>(sink));
    return last_error();
}

extern "C" int tierbench_gpu_global_chase(const unsigned* next, unsigned stride, unsigned long long steps, void* sink,
                                          void* stream) {
    global_chase<<<1, 1, 0, as_stream(stream)>>>(next, stride, steps, static_cast<unsigned*>(sink));
    return last_error();
}

extern "C" int tierbench_gpu_shared_prepare(unsigned bytes) {
    auto status = GPU(FuncSetAttribute)(reinterpret_cast<const void*>(&shared_read),
                                        GPU(FuncAttributeMaxDynamicSharedMemorySize), static_cast<int>(bytes));
    if (status != GPU(Success)) {
        return static_cast<int>(status);
    }
    return static_cast<int>(GPU(FuncSetAttribute)(reinterpret_cast<const void*>(&shared_chase),
                                                  GPU(FuncAttributeMaxDynamicSharedMemorySize),
                                                  static_cast<int>(bytes)));
}

extern "C" int tierbench_gpu_shared_read(unsigned bytes, unsigned passes, unsigned blocks, void* sink, void* stream) {
    shared_read<<<blocks, threads, bytes, as_stream(stream)>>>(bytes / sizeof(unsigned), passes,
                                                               static_cast<unsigned*>(sink));
    return last_error();
}

extern "C" int tierbench_gpu_shared_chase(const unsigned* order, unsigned bytes, unsigned long long steps, void* sink,
                                          void* stream) {
    shared_chase<<<1, threads, bytes, as_stream(stream)>>>(order, bytes / sizeof(unsigned), steps,
                                                           static_cast<unsigned*>(sink));
    return last_error();
}
