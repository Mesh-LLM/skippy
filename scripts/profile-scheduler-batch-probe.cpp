// Diagnostic caller only. The supplied native library is never modified.
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
    const std::string message = error ? error->message : "native failure without detail";
    skippy_error_free(error);
    if (status != SKIPPY_STATUS_OK) throw std::runtime_error(message);
}

struct Lane {
    skippy_session * session = nullptr;
    std::vector<llama_token> generated;
};

static json capture(skippy_session * session, int32_t row, const llama_vocab * vocab,
                    llama_token token, const std::filesystem::path & directory, size_t step) {
    const int32_t count = llama_vocab_n_tokens(vocab);
    const float * logits = llama_get_logits_ith(skippy_session_llama_context(session), row);
    if (!logits || token < 0 || token >= count) throw std::runtime_error("invalid subject logits");
    std::vector<int32_t> order(static_cast<size_t>(count));
    std::iota(order.begin(), order.end(), 0);
    for (int32_t i = 0; i < count; ++i) {
        if (!std::isfinite(logits[i])) throw std::runtime_error("nonfinite subject logits");
    }
    std::partial_sort(order.begin(), order.begin()+20, order.end(),
        [&](int32_t a, int32_t b) { return logits[a] != logits[b] ? logits[a] > logits[b] : a < b; });
    const auto filename = "subject-logits-" + std::to_string(step) + ".f32";
    std::ofstream raw(directory/filename, std::ios::binary);
    raw.write(reinterpret_cast<const char *>(logits), count * sizeof(float)); raw.close();
    if (!raw) throw std::runtime_error("subject logits write failed");
    json top = json::array();
    for (size_t i = 0; i < 20; ++i) top.push_back({{"token", order[i]}, {"logit", logits[order[i]]}});
    return {{"step", step}, {"token", token}, {"position", skippy_session_position(session)},
            {"batch_row", row}, {"raw_file", filename}, {"vocab_size", count}, {"top", top}};
}

static void execute(std::vector<Lane> & lanes, const std::vector<size_t> & indexes,
                    const std::vector<llama_token> * prompt, const skippy_sampling_config & sampling,
                    const llama_vocab * vocab, const std::filesystem::path & directory, json & report) {
    std::vector<skippy_iteration_request> requests(indexes.size());
    std::vector<llama_token> inputs(indexes.size()), predicted(indexes.size());
    std::vector<size_t> output_indexes(indexes.size());
    json positions = json::array();
    for (size_t i = 0; i < indexes.size(); ++i) {
        auto & lane = lanes.at(indexes[i]);
        if (!lane.session || (!prompt && lane.generated.empty())) throw std::runtime_error("inactive decode lane");
        if (lane.generated.size() >= 32) throw std::runtime_error("finished lane decoded again");
        if (!prompt) inputs[i] = lane.generated.back();
        requests[i].session = lane.session;
        requests[i].token_ids = prompt ? prompt->data() : &inputs[i];
        requests[i].token_count = prompt ? prompt->size() : 1;
        requests[i].sampling = &sampling;
        requests[i].sample_last = true;
        positions.push_back(skippy_session_position(lane.session));
    }
    skippy_error * error = nullptr; size_t count = 0;
    const auto status = skippy_iteration_batch_sampled(requests.data(), requests.size(), nullptr, nullptr, nullptr,
          nullptr, output_indexes.data(), predicted.data(), predicted.size(), &count, &error);
    check(status, error);
    if (count != indexes.size()) throw std::runtime_error("sample count mismatch");
    for (size_t i = 0; i < indexes.size(); ++i) {
        if (output_indexes[i] != i) throw std::runtime_error("sample row mapping mismatch");
        auto & lane = lanes[indexes[i]];
        if (indexes[i] == 0) {
            // Native source samples offsets[request+1]-1: sparse output translation is internal.
            const int32_t row = prompt ? static_cast<int32_t>(prompt->size()-1) : static_cast<int32_t>(i);
            report["subject_steps"].push_back(capture(lane.session, row, vocab, predicted[i], directory, lane.generated.size()));
        }
        lane.generated.push_back(predicted[i]);
    }
    report["calls"].push_back({{"phase", prompt ? "prefill" : "decode"}, {"lanes", indexes},
                              {"input_positions", positions}, {"tokens_per_lane", prompt ? prompt->size() : 1}});
    std::ofstream(directory/"probe.json") << report.dump(2) << '\n';
}

int main(int argc, char ** argv) {
    try {
        const auto abi = skippy_abi_version();
        if (argc == 2 && std::string(argv[1]) == "--identity") {
            std::cout << json({{"abi", {abi.major, abi.minor, abi.patch}},
                              {"runtime_config_bytes", sizeof(skippy_runtime_config)}}).dump() << '\n'; return 0;
        }
        if (argc != 5) throw std::runtime_error("usage: batch-probe MODEL PROMPT MODE PRIVATE_OUTPUT");
        const std::string mode = argv[3];
        if (mode != "solo" && mode != "resident-serial" && mode != "resident-batched" && mode != "staggered") {
            throw std::runtime_error("unregistered mode");
        }
        const auto directory = std::filesystem::path(argv[4]);
        if (!std::filesystem::create_directory(directory)) throw std::runtime_error("output must be new");
        std::ifstream input(argv[2], std::ios::binary);
        if (!input) throw std::runtime_error("prompt unavailable");
        const std::string prompt((std::istreambuf_iterator<char>(input)), {});
        ggml_backend_load_all();
        auto config = skippy_runtime_config_default();
        config.ctx_size = 8192; config.lane_count = 16;
        config.n_batch = config.n_ubatch = 512;
        config.n_threads = config.n_threads_batch = 16; config.n_gpu_layers = 999;
        config.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED; config.recurrent_rollback_tokens = 0;
        skippy_model * model = nullptr; skippy_error * error = nullptr;
        auto status = skippy_model_open(argv[1], &config, &model, &error); check(status, error); error = nullptr;
        std::vector<llama_token> tokens(8192); size_t token_count = 0;
        status = skippy_tokenize(model, prompt.c_str(), true, tokens.data(), tokens.size(), &token_count, &error);
        check(status, error); error = nullptr;
        if (token_count != 512) throw std::runtime_error("prompt must resolve to512 tokens");
        tokens.resize(token_count);
        skippy_sampling_config sampling{}; sampling.version = 1;
        sampling.flags = SKIPPY_SAMPLING_FLAG_ENABLED | SKIPPY_SAMPLING_FLAG_IGNORE_EOS;
        sampling.seed = 42; sampling.top_p = sampling.repeat_penalty = 1;
        sampling.ignore_eos = 1; sampling.reasoning_budget_tokens = -1;
        std::vector<Lane> lanes(mode == "solo" ? 1 : 16);
        const auto * vocab = llama_model_get_vocab(skippy_model_native_model(model));
        json report = {{"mode", mode}, {"abi", {abi.major, abi.minor, abi.patch}},
                       {"prompt_ids", tokens}, {"calls", json::array()}, {"subject_steps", json::array()}};
        std::ofstream(directory/"ready.json") << report.dump(2) << '\n';
        std::string permission;
        if (!std::getline(std::cin, permission) || permission != "GO") throw std::runtime_error("native admission not granted");
        auto create = [&](size_t index) {
            auto rc = skippy_session_create(model, &lanes[index].session, &error); check(rc, error); error = nullptr;
            rc = skippy_session_configure_chat_sampling(lanes[index].session, &sampling, "{\"grammar\":\"\"}", token_count, &error);
            check(rc, error); error = nullptr;
        };
        auto decode = [&](bool individual) {
            std::vector<size_t> active;
            for (size_t i = 0; i < lanes.size(); ++i) {
                if (lanes[i].session && !lanes[i].generated.empty() && lanes[i].generated.size() < 32) active.push_back(i);
            }
            if (individual) {
                for (size_t i : active) execute(lanes, {i}, nullptr, sampling, vocab, directory, report);
            } else if (!active.empty()) execute(lanes, active, nullptr, sampling, vocab, directory, report);
            return !active.empty();
        };
        for (size_t i = 0; i < lanes.size(); ++i) {
            create(i);
            execute(lanes, {i}, &tokens, sampling, vocab, directory, report);
            if (mode == "staggered" && i >= 1) decode(false);
        }
        while (decode(mode == "resident-serial" || mode == "solo")) {}
        report["lanes"] = json::array();
        for (size_t i = 0; i < lanes.size(); ++i) {
            auto & lane = lanes[i];
            if (lane.generated.size() != 32) throw std::runtime_error("incomplete lane");
            std::vector<char> text(32768); size_t bytes = 0;
            status = skippy_detokenize(model, lane.generated.data(), lane.generated.size(), text.data(), text.size(), &bytes, &error);
            check(status, error); error = nullptr;
            report["lanes"].push_back({{"lane", i}, {"generated_ids", lane.generated}, {"text", std::string(text.data(), bytes)}});
            status = skippy_session_free(lane.session, &error); check(status, error); error = nullptr;
        }
        report["complete"] = true;
        std::ofstream(directory/"probe.json") << report.dump(2) << '\n';
        status = skippy_model_free(model, &error); check(status, error);
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "BATCH_PROBE_FAILURE " << error.what() << '\n'; return 1;
    }
}
