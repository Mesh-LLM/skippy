#include "gpu_common.h"

#if defined(TIERBENCH_HIP)
#include <hipblas/hipblas.h>
#else
#include <cublas_v2.h>
#endif

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <format>
#include <memory>
#include <random>
#include <ranges>
#include <string>
#include <vector>

namespace {

using namespace tierbench_gpu;

#if defined(TIERBENCH_HIP)
using BlasHandle = hipblasHandle_t;
using BlasStatus = hipblasStatus_t;
using DataType = hipDataType;
constexpr BlasStatus blas_success = HIPBLAS_STATUS_SUCCESS;
constexpr DataType f16 = HIP_R_16F;
constexpr DataType bf16 = HIP_R_16BF;
constexpr const char* plugin_name = "hip_prefill";
constexpr const char* blas_name = "hipblasGemmEx";
BlasStatus blas_create(BlasHandle* handle) { return hipblasCreate(handle); }
void blas_destroy(BlasHandle handle) { static_cast<void>(hipblasDestroy(handle)); }
BlasStatus blas_stream(BlasHandle handle, GPU(Stream_t) stream) { return hipblasSetStream(handle, stream); }
BlasStatus blas_gemm(BlasHandle handle, int m, int n, int k, const void* a, int lda, const void* b, int ldb, void* c,
                     int ldc, DataType type) {
    float alpha = 1.0f;
    float beta = 0.0f;
    return hipblasGemmEx(handle, HIPBLAS_OP_N, HIPBLAS_OP_N, m, n, k, &alpha, a, type, lda, b, type, ldb, &beta, c,
                         type, ldc, HIPBLAS_COMPUTE_32F, HIPBLAS_GEMM_DEFAULT);
}
#else
using BlasHandle = cublasHandle_t;
using BlasStatus = cublasStatus_t;
using DataType = cudaDataType;
constexpr BlasStatus blas_success = CUBLAS_STATUS_SUCCESS;
constexpr DataType f16 = CUDA_R_16F;
constexpr DataType bf16 = CUDA_R_16BF;
constexpr const char* plugin_name = "cuda_prefill";
constexpr const char* blas_name = "cublasGemmEx";
BlasStatus blas_create(BlasHandle* handle) { return cublasCreate(handle); }
void blas_destroy(BlasHandle handle) { static_cast<void>(cublasDestroy(handle)); }
BlasStatus blas_stream(BlasHandle handle, GPU(Stream_t) stream) { return cublasSetStream(handle, stream); }
BlasStatus blas_gemm(BlasHandle handle, int m, int n, int k, const void* a, int lda, const void* b, int ldb, void* c,
                     int ldc, DataType type) {
    float alpha = 1.0f;
    float beta = 0.0f;
    return cublasGemmEx(handle, CUBLAS_OP_N, CUBLAS_OP_N, m, n, k, &alpha, a, type, lda, b, type, ldb, &beta, c, type,
                        ldc, CUBLAS_COMPUTE_32F, CUBLAS_GEMM_DEFAULT);
}
#endif

enum class Precision { f16, bf16 };

struct Gemm {
    int m = 0;
    int n = 0;
    int k = 0;
    DataType type{};
    Timer timer;
    BlasHandle handle{};
    Owned a{nullptr, release_device};
    Owned b{nullptr, release_device};
    Owned c{nullptr, release_device};

    Gemm() = default;
    Gemm(const Gemm&) = delete;
    Gemm& operator=(const Gemm&) = delete;
    ~Gemm() {
        if (handle) {
            blas_destroy(handle);
        }
    }
};

// random values in [0.125, 1) as raw half or bfloat16 bits: zeros would let the GPU idle and boost its clocks
Owned upload_random(std::size_t elements, Precision precision, tierbench_error* error) {
    std::vector<std::uint16_t> host(elements);
    std::mt19937 random{42};
    std::uniform_int_distribution<unsigned> bits{precision == Precision::f16 ? 0x3000u : 0x3E00u,
                                                 precision == Precision::f16 ? 0x3BFFu : 0x3F7Fu};
    std::ranges::generate(host, [&] { return static_cast<std::uint16_t>(bits(random)); });
    auto bytes = elements * sizeof(std::uint16_t);
    auto device = allocate(Memory::device, bytes, error);
    if (device && !ok(GPU(Memcpy)(device.get(), host.data(), bytes, GPU(MemcpyHostToDevice)), error, "upload")) {
        return {nullptr, release_device};
    }
    return device;
}

template <Precision P, int Width>
void* setup_gemm(std::uint64_t tokens, const tierbench_env* env, tierbench_error* error) {
    if (!ok(GPU(SetDevice)(static_cast<int>(env->gpu_index)), error, "set device")) {
        return nullptr;
    }
    auto gemm = std::make_unique<Gemm>();
    gemm->m = static_cast<int>(tokens);
    gemm->n = Width;
    gemm->k = Width;
    gemm->type = P == Precision::f16 ? f16 : bf16;
    if (!start_timer(gemm->timer, error)) {
        return nullptr;
    }
    if (blas_create(&gemm->handle) != blas_success || blas_stream(gemm->handle, gemm->timer.stream) != blas_success) {
        std::snprintf(error->message, sizeof error->message, "BLAS handle could not be created");
        return nullptr;
    }
    auto m = static_cast<std::size_t>(gemm->m);
    gemm->a = upload_random(m * Width, P, error);
    gemm->b = gemm->a ? upload_random(std::size_t{Width} * Width, P, error) : Owned{nullptr, release_device};
    gemm->c = gemm->b ? allocate(Memory::device, m * Width * sizeof(std::uint16_t), error)
                      : Owned{nullptr, release_device};
    return gemm->c ? gemm.release() : nullptr;
}

void teardown(void* state) {
    delete static_cast<Gemm*>(state);
}

int run_gemm(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& gemm = *static_cast<Gemm*>(state);
    auto m = static_cast<std::uint64_t>(gemm.m);
    auto n = static_cast<std::uint64_t>(gemm.n);
    auto k = static_cast<std::uint64_t>(gemm.k);
    auto ops = 2 * m * n * k;
    auto bytes = (m * k + k * n + m * n) * sizeof(std::uint16_t);
    BlasStatus last = blas_success;
    int result = timed(gemm.timer, iterations, bytes, ops, out, error, [&] {
        // row-major C = A x B, issued as column-major C^T = B^T x A^T
        last = blas_gemm(gemm.handle, gemm.n, gemm.m, gemm.k, gemm.b.get(), gemm.n, gemm.a.get(), gemm.k,
                         gemm.c.get(), gemm.n, gemm.type);
        return last == blas_success ? GPU(Success) : GPU(ErrorUnknown);
    });
    if (result != 0 && last != blas_success) {
        std::snprintf(error->message, sizeof error->message, "%s returned status %d", blas_name,
                      static_cast<int>(last));
    }
    return result;
}

constexpr std::uint64_t max_tokens = 16384;
constexpr const char* mechanism = "blas-gemm-f32-accumulate";

constexpr tierbench_case cases[] = {
    {"gemm-f16-4096", mechanism, TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_TOKENS, 1, max_tokens,
     setup_gemm<Precision::f16, 4096>, run_gemm, teardown},
    {"gemm-f16-8192", mechanism, TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_TOKENS, 1, max_tokens,
     setup_gemm<Precision::f16, 8192>, run_gemm, teardown},
    {"gemm-bf16-4096", mechanism, TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_TOKENS, 1, max_tokens,
     setup_gemm<Precision::bf16, 4096>, run_gemm, teardown},
    {"gemm-bf16-8192", mechanism, TIERBENCH_GPU, TIERBENCH_GPU, TIERBENCH_TOKENS, 1, max_tokens,
     setup_gemm<Precision::bf16, 8192>, run_gemm, teardown},
};

std::string facts_text;

const char* facts(const tierbench_env* env) {
    auto device = static_cast<int>(env->gpu_index);
    gpu_device_prop props{};
    if (GPU(GetDeviceProperties)(&props, device) != GPU(Success)) {
        facts_text = std::format(R"({{"api":"{}","error":"device query failed"}})", gpu_api_name);
        return facts_text.c_str();
    }
    facts_text = std::format(R"({{"api":"{}","blas":"{}","gpu":"{}","sm_count":{}}})", gpu_api_name, blas_name,
                             props.name, attribute(gpu_attr_sm_count, device));
    return facts_text.c_str();
}

constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, plugin_name, facts,
                                  static_cast<std::uint32_t>(std::size(cases)), cases};

}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) {
    return &plugin;
}
