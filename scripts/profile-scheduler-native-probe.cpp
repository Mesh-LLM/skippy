// Diagnostic caller only: links the supplied runtime without modifying it.
#include "skippy.h"
#include "ggml-backend.h"
#include "json.hpp"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

using json = nlohmann::ordered_json;

static void check(skippy_status status, skippy_error * error) {
    const std::string message = error ? error->message : "native error without detail";
    skippy_error_free(error);
    if (status != SKIPPY_STATUS_OK) throw std::runtime_error(message);
}

static llama_token iteration(skippy_session * session, const llama_token * tokens,
                             size_t count, const skippy_sampling_config & sampling, bool sample) {
    skippy_iteration_request request{};
    request.session = session;
    request.token_ids = tokens;
    request.token_count = count;
    request.sampling = &sampling;
    request.sample_last = sample;
    size_t output_count = 0, index = 0;
    llama_token predicted = -1;
    skippy_error * error = nullptr;
    const auto status = skippy_iteration_batch_sampled(&request, 1, nullptr, nullptr,
        nullptr, nullptr, &index, &predicted, 1, &output_count, &error);
    check(status, error);
    if (output_count != (sample ? 1u : 0u) || (sample && index != 0)) {
        throw std::runtime_error("unexpected sampled row mapping");
    }
    return predicted;
}

static void prefill(skippy_session * session, const llama_token * tokens, size_t count) {
    skippy_error * error = nullptr;
    size_t bytes = 0;
    const auto status = skippy_prefill_chunk(session, tokens, count, nullptr, 0,
                                            nullptr, 0, &bytes, &error);
    check(status, error);
    if (bytes != 0) throw std::runtime_error("unexpected activation export");
}

static json capture(skippy_session * session, const llama_vocab * vocab, llama_token token,
                    const std::filesystem::path & directory, size_t step) {
    const auto count = llama_vocab_n_tokens(vocab);
    const float * logits = llama_get_logits_ith(skippy_session_llama_context(session), -1);
    if (!logits || token < 0 || token >= count) throw std::runtime_error("invalid logits/token");
    std::vector<int32_t> order(static_cast<size_t>(count));
    std::iota(order.begin(), order.end(), 0);
    for (int32_t i = 0; i < count; ++i) {
        if (!std::isfinite(logits[i])) throw std::runtime_error("nonfinite raw logits");
    }
    std::partial_sort(order.begin(), order.begin() + 20, order.end(),
        [&](int32_t a, int32_t b) { return logits[a] != logits[b] ? logits[a] > logits[b] : a < b; });
    double total = 0;
    for (int32_t i = 0; i < count; ++i) total += std::exp(double(logits[i]) - logits[order[0]]);
    const double log_z = double(logits[order[0]]) + std::log(total);
    const auto filename = "logits-" + std::to_string(step) + ".f32";
    std::ofstream raw(directory / filename, std::ios::binary);
    raw.write(reinterpret_cast<const char *>(logits), count * sizeof(float));
    raw.close();
    if (!raw) throw std::runtime_error("raw logits write failed");
    json top = json::array();
    for (size_t i = 0; i < 20; ++i) top.push_back({{"token", order[i]}, {"logit", logits[order[i]]},
                                                {"logprob", double(logits[order[i]]) - log_z}});
    return {{"step", step}, {"token", token}, {"raw_file", filename}, {"vocab_size", count},
            {"sampled_logprob", double(logits[token]) - log_z}, {"top", top},
            {"position", skippy_session_position(session)}};
}

int main(int argc, char ** argv) {
    try {
        const auto abi = skippy_abi_version();
        if (argc == 2 && std::string(argv[1]) == "--identity") {
            std::cout << json({{"abi", {abi.major, abi.minor, abi.patch}},
                              {"runtime_config_bytes", sizeof(skippy_runtime_config)}}).dump() << '\n';
            return 0;
        }
        if (argc != 5) throw std::runtime_error("usage: probe MODEL PROMPT_FILE MODE PRIVATE_OUTPUT");
        const std::string mode = argv[3];
        if (mode != "whole" && mode != "whole-logprobs" && mode != "split-iteration" && mode != "split-prefill") {
            throw std::runtime_error("unregistered mode");
        }
        const bool split = mode.rfind("split-", 0) == 0;
        const auto directory = std::filesystem::path(argv[4]);
        if (!std::filesystem::create_directory(directory)) throw std::runtime_error("output must be new");
        std::ifstream input(argv[2], std::ios::binary);
        if (!input) throw std::runtime_error("prompt unavailable");
        const std::string prompt((std::istreambuf_iterator<char>(input)), {});
        ggml_backend_load_all();
        auto config = skippy_runtime_config_default();
        config.ctx_size = 8192;
        config.lane_count = 16;
        config.n_batch = config.n_ubatch = 512;
        config.n_threads = config.n_threads_batch = 16;
        config.n_gpu_layers = 999;
        config.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        config.recurrent_rollback_tokens = 0;
        skippy_model * model = nullptr;
        skippy_session * session = nullptr;
        skippy_error * error = nullptr;
        auto status = skippy_model_open(argv[1], &config, &model, &error);
        check(status, error); error = nullptr;
        std::vector<llama_token> tokens(8192);
        size_t count = 0;
        status = skippy_tokenize(model, prompt.c_str(), true, tokens.data(), tokens.size(), &count, &error);
        check(status, error); error = nullptr;
        if (count != 4096) throw std::runtime_error("prompt must resolve to4096 tokens");
        tokens.resize(count);
        status = skippy_session_create(model, &session, &error);
        check(status, error); error = nullptr;
        skippy_sampling_config sampling{};
        sampling.version = 1;
        sampling.flags = SKIPPY_SAMPLING_FLAG_ENABLED | SKIPPY_SAMPLING_FLAG_IGNORE_EOS;
        sampling.seed = 42;
        sampling.top_p = sampling.repeat_penalty = 1;
        sampling.ignore_eos = 1;
        sampling.reasoning_budget_tokens = -1;
        if (mode != "whole") {
            sampling.flags |= SKIPPY_SAMPLING_FLAG_LOGPROBS;
            sampling.top_logprobs = 5;
        }
        status = skippy_session_configure_chat_sampling(session, &sampling, "{\"grammar\":\"\"}", count, &error);
        check(status, error); error = nullptr;
        json report = {{"mode", mode}, {"abi", {abi.major, abi.minor, abi.patch}},
                       {"prompt_ids", tokens}, {"chunks", json::array()}, {"steps", json::array()}};
        std::ofstream(directory / "ready.json") << report.dump(2) << '\n';
        std::string permission;
        if (!std::getline(std::cin, permission) || permission != "GO") {
            throw std::runtime_error("host native/thermal admission not granted");
        }
        const size_t prefix = split ? count - 1 : count;
        llama_token token = -1;
        for (size_t offset = 0; offset < prefix;) {
            const size_t n = std::min(size_t(512), prefix - offset);
            const bool sample = !split && offset + n == prefix;
            if (mode == "split-prefill") prefill(session, tokens.data() + offset, n);
            else token = iteration(session, tokens.data() + offset, n, sampling, sample);
            report["chunks"].push_back(n);
            offset += n;
        }
        if (split) token = iteration(session, &tokens.back(), 1, sampling, true);
        const auto * vocab = llama_model_get_vocab(skippy_model_native_model(model));
        std::vector<llama_token> generated;
        for (size_t step = 0; step < 24; ++step) {
            if (step) token = iteration(session, &token, 1, sampling, true);
            report["steps"].push_back(capture(session, vocab, token, directory, step));
            generated.push_back(token);
            std::ofstream(directory / "probe.json") << report.dump(2) << '\n';
        }
        std::vector<char> text(32768);
        size_t text_bytes = 0;
        status = skippy_detokenize(model, generated.data(), generated.size(), text.data(),
                                   text.size(), &text_bytes, &error);
        check(status, error); error = nullptr;
        report["generated_ids"] = generated;
        report["text"] = std::string(text.data(), text_bytes);
        if (mode != "whole") {
            std::vector<skippy_token_logprobs> recorded(24);
            size_t recorded_count = 0;
            status = skippy_session_take_token_logprobs(session, recorded.data(), recorded.size(),
                                                       &recorded_count, &error);
            check(status, error); error = nullptr;
            if (recorded_count != 24) throw std::runtime_error("missing sampled logprob records");
            for (size_t i = 0; i < recorded_count; ++i) {
                if (recorded[i].token != generated[i]) throw std::runtime_error("recorded token mismatch");
                report["steps"][i]["recorded_logprob"] = recorded[i].logprob;
            }
        }
        report["complete"] = true;
        std::ofstream(directory / "probe.json") << report.dump(2) << '\n';
        status = skippy_session_free(session, &error); check(status, error); error = nullptr;
        status = skippy_model_free(model, &error); check(status, error);
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "PROBE_FAILURE " << error.what() << '\n';
        return 1;
    }
}
