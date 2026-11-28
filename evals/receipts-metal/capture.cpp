// Captures actual Q8_0 Metal outputs, not reference-generated expected values.
// stdout: little-endian f32 weights, input, decode output, prefill output.
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-alloc.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

constexpr int WIDTH = 256;
constexpr int ROWS = 16;
constexpr int POSITIONS = 17; // crosses the current small-batch/matmul boundary

static void require(bool ok, const char * message) {
    if (!ok) {
        std::fprintf(stderr, "%s\n", message);
        std::exit(1);
    }
}

static void write_f32(const std::vector<float> & values) {
    for (float value : values) {
        require(std::isfinite(value), "non-finite captured value");
    }
    // Apple Silicon is little-endian; this tool only builds on macOS.
    require(std::fwrite(values.data(), sizeof(float), values.size(), stdout) == values.size(),
            "could not write capture");
}

static std::vector<float> compute(ggml_backend_t backend, const std::vector<uint8_t> & weights,
                                   const std::vector<float> & input, int positions) {
    const ggml_init_params params{1024 * 1024, nullptr, true};
    auto * ctx = ggml_init(params);
    require(ctx != nullptr, "could not allocate graph context");
    auto * w = ggml_new_tensor_2d(ctx, GGML_TYPE_Q8_0, WIDTH, ROWS);
    auto * x = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, WIDTH, positions);
    auto * y = ggml_mul_mat(ctx, w, x);
    auto * graph = ggml_new_graph(ctx);
    ggml_build_forward_expand(graph, y);
    require(ggml_backend_supports_op(backend, y), "Metal does not support the capture operation");
    auto * buffer = ggml_backend_alloc_ctx_tensors(ctx, backend);
    require(buffer != nullptr, "could not allocate Metal tensors");
    ggml_backend_tensor_set(w, weights.data(), 0, weights.size());
    ggml_backend_tensor_set(x, input.data(), 0, WIDTH * positions * sizeof(float));
    require(ggml_backend_graph_compute(backend, graph) == GGML_STATUS_SUCCESS, "Metal compute failed");
    std::vector<float> output(ROWS * positions);
    ggml_backend_tensor_get(y, output.data(), 0, output.size() * sizeof(float));
    ggml_backend_buffer_free(buffer);
    ggml_free(ctx);
    return output;
}

int main() {
    ggml_backend_load_all();
    auto * backend = ggml_backend_init_by_name("MTL0", nullptr);
    require(backend != nullptr, "Metal unavailable: this is a failure, not a skipped qualification");
    std::fprintf(stderr, "backend=%s tensor_disable=%s K=%d M=%d N=%d\n",
                 ggml_backend_name(backend), std::getenv("GGML_METAL_TENSOR_DISABLE") ? "set" : "unset",
                 WIDTH, ROWS, POSITIONS);
    std::vector<float> weights(WIDTH * ROWS), input(WIDTH * POSITIONS);
    for (size_t i = 0; i < weights.size(); ++i) {
        weights[i] = 0.05f * std::sin(i * 1.731f) + 0.02f * std::cos(i * 0.213f);
    }
    for (size_t i = 0; i < input.size(); ++i) {
        input[i] = std::sin(i * 2.173f) + 0.7f * std::cos(i * 0.137f);
    }
    std::vector<uint8_t> quantized(ggml_row_size(GGML_TYPE_Q8_0, WIDTH) * ROWS);
    require(ggml_quantize_chunk(GGML_TYPE_Q8_0, weights.data(), quantized.data(),
                               0, ROWS, WIDTH, nullptr) == quantized.size(), "quantization failed");
    ggml_get_type_traits(GGML_TYPE_Q8_0)->to_float(quantized.data(), weights.data(), weights.size());
    write_f32(weights);
    write_f32(input);
    write_f32(compute(backend, quantized, input, 1));
    write_f32(compute(backend, quantized, input, POSITIONS));
    require(std::fflush(stdout) == 0, "could not flush capture");
    ggml_backend_free(backend);
}
