#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShaders/MetalPerformanceShaders.h>

#include "tierbench.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <format>
#include <memory>
#include <random>
#include <ranges>
#include <span>
#include <string>
#include <string_view>

namespace {

void say(tierbench_error* error, std::string_view text) {
    std::snprintf(error->message, sizeof error->message, "%.*s", static_cast<int>(text.size()), text.data());
}

std::string describe(NSError* error) {
    return error ? std::string{error.localizedDescription.UTF8String} : std::string{"unknown Metal error"};
}

struct Gpu {
    id<MTLDevice> device = nil;
    id<MTLCommandQueue> queue = nil;
};

Gpu* acquire(tierbench_error* error) {
    static Gpu gpu = [] {
        Gpu made;
        made.device = MTLCreateSystemDefaultDevice();
        made.queue = made.device ? [made.device newCommandQueue] : nil;
        return made;
    }();
    if (!gpu.queue) {
        say(error, "no Metal device");
        return nullptr;
    }
    return &gpu;
}

enum class Precision { f16, f32 };

struct Gemm {
    Gpu* gpu = nullptr;
    NSUInteger m = 0;
    NSUInteger n = 0;
    NSUInteger k = 0;
    MPSMatrix* left = nil;
    MPSMatrix* right = nil;
    MPSMatrix* result = nil;
    MPSMatrixMultiplication* multiply = nil;
};

// random values in [0.125, 1): zeros would let the GPU idle and boost its clocks
MPSMatrix* make_matrix(Gpu& gpu, NSUInteger rows, NSUInteger columns, Precision precision, bool fill) {
    auto type = precision == Precision::f16 ? MPSDataTypeFloat16 : MPSDataTypeFloat32;
    auto row_bytes = [MPSMatrixDescriptor rowBytesForColumns:columns dataType:type];
    id<MTLBuffer> buffer = [gpu.device newBufferWithLength:rows * row_bytes options:MTLResourceStorageModeShared];
    if (!buffer) {
        return nil;
    }
    if (fill) {
        std::mt19937 random{42};
        if (precision == Precision::f16) {
            std::uniform_int_distribution<unsigned> bits{0x3000u, 0x3BFFu};
            std::span values{static_cast<std::uint16_t*>(buffer.contents), buffer.length / sizeof(std::uint16_t)};
            std::ranges::generate(values, [&] { return static_cast<std::uint16_t>(bits(random)); });
        } else {
            std::uniform_real_distribution<float> value{0.125f, 1.0f};
            std::span values{static_cast<float*>(buffer.contents), buffer.length / sizeof(float)};
            std::ranges::generate(values, [&] { return value(random); });
        }
    }
    auto descriptor = [MPSMatrixDescriptor matrixDescriptorWithRows:rows
                                                             columns:columns
                                                            rowBytes:row_bytes
                                                            dataType:type];
    return [[MPSMatrix alloc] initWithBuffer:buffer descriptor:descriptor];
}

template <Precision P, NSUInteger Width>
void* setup_gemm(std::uint64_t tokens, const tierbench_env*, tierbench_error* error) {
    auto gpu = acquire(error);
    if (!gpu) {
        return nullptr;
    }
    @autoreleasepool {
        auto gemm = std::make_unique<Gemm>();
        gemm->gpu = gpu;
        gemm->m = static_cast<NSUInteger>(tokens);
        gemm->n = Width;
        gemm->k = Width;
        gemm->left = make_matrix(*gpu, gemm->m, gemm->k, P, true);
        gemm->right = make_matrix(*gpu, gemm->k, gemm->n, P, true);
        gemm->result = make_matrix(*gpu, gemm->m, gemm->n, P, false);
        gemm->multiply = [[MPSMatrixMultiplication alloc] initWithDevice:gpu->device
                                                           transposeLeft:NO
                                                          transposeRight:NO
                                                              resultRows:gemm->m
                                                           resultColumns:gemm->n
                                                         interiorColumns:gemm->k
                                                                   alpha:1.0
                                                                    beta:0.0];
        if (!gemm->left || !gemm->right || !gemm->result || !gemm->multiply) {
            say(error, "matrix allocation failed");
            return nullptr;
        }
        return gemm.release();
    }
}

void teardown(void* state) {
    delete static_cast<Gemm*>(state);
}

template <Precision P>
int run_gemm(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& gemm = *static_cast<Gemm*>(state);
    @autoreleasepool {
        id<MTLCommandBuffer> commands = [gemm.gpu->queue commandBuffer];
        std::ranges::for_each(std::views::iota(std::uint64_t{0}, iterations), [&](std::uint64_t) {
            [gemm.multiply encodeToCommandBuffer:commands
                                      leftMatrix:gemm.left
                                     rightMatrix:gemm.right
                                    resultMatrix:gemm.result];
        });
        [commands commit];
        [commands waitUntilCompleted];
        if (commands.status != MTLCommandBufferStatusCompleted) {
            say(error, describe(commands.error));
            return 1;
        }
        std::uint64_t m = gemm.m;
        std::uint64_t n = gemm.n;
        std::uint64_t k = gemm.k;
        auto element = P == Precision::f16 ? sizeof(std::uint16_t) : sizeof(float);
        out->ops_per_iteration = 2 * m * n * k;
        out->bytes_per_iteration = (m * k + k * n + m * n) * element;
        out->device_ns = static_cast<std::uint64_t>((commands.GPUEndTime - commands.GPUStartTime) * 1e9);
        out->has_device_ns = 1;
        return 0;
    }
}

constexpr std::uint64_t max_tokens = 16384;

constexpr tierbench_case cases[] = {
    {"gemm-f16-4096", "MPSMatrixMultiplication", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_TOKENS, 1, max_tokens,
     setup_gemm<Precision::f16, 4096>, run_gemm<Precision::f16>, teardown},
    {"gemm-f16-8192", "MPSMatrixMultiplication", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_TOKENS, 1, max_tokens,
     setup_gemm<Precision::f16, 8192>, run_gemm<Precision::f16>, teardown},
    {"gemm-f32-4096", "MPSMatrixMultiplication", TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_TOKENS, 1, max_tokens,
     setup_gemm<Precision::f32, 4096>, run_gemm<Precision::f32>, teardown},
};

std::string facts_text;

const char* facts(const tierbench_env*) {
    tierbench_error ignored{};
    auto gpu = acquire(&ignored);
    facts_text = gpu ? std::format(R"({{"api":"metal","gpu":"{}","library":"MetalPerformanceShaders"}})",
                                   gpu->device.name.UTF8String)
                     : std::string{R"({"api":"metal","error":"no Metal device"})"};
    return facts_text.c_str();
}

constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, "metal_prefill", facts,
                                  static_cast<std::uint32_t>(std::size(cases)), cases};

}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) {
    return &plugin;
}
