#include "host.h"
#include "json.h"
#include "tierbench.h"

#include <dlfcn.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <expected>
#include <filesystem>
#include <format>
#include <iterator>
#include <limits>
#include <memory>
#include <numeric>
#include <optional>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace tierbench {
namespace {

namespace fs = std::filesystem;
using namespace std::literals;
using Clock = std::chrono::steady_clock;

template <class T>
using Result = std::expected<T, std::string>;

constexpr std::string_view usage = R"(usage: tierbench [options]
  --plugins DIR     load every .so/.dylib in DIR (default: plugins)
  --list            list the loaded plugins and their cases, then exit
  --case TEXT       run only cases whose "plugin/case" contains TEXT
  --min-bytes SIZE  smallest transfer, e.g. 4K (default 4K)
  --max-bytes SIZE  largest transfer, e.g. 1G (default 1G)
  --min-tokens N    smallest token count for token-sized cases (default 1)
  --max-tokens N    largest token count, e.g. 16K (default 16K)
  --reps N          measured repetitions per size (default 10)
  --warmups N       unrecorded repetitions before them (default 2)
  --target-ms N     each repetition runs at least this long (default 50)
  --scratch DIR     directory on the storage under test (default: the temp directory)
  --gpu N           GPU index passed to plugins (default 0)
  --out FILE        append JSON rows to FILE instead of stdout
)";

struct Config {
    fs::path plugins = "plugins";
    fs::path scratch = fs::temp_directory_path();
    std::string filter;
    std::uint64_t min_bytes = 4096;
    std::uint64_t max_bytes = std::uint64_t{1} << 30;
    std::uint64_t min_tokens = 1;
    std::uint64_t max_tokens = 16384;
    std::uint32_t reps = 10;
    std::uint32_t warmups = 2;
    std::chrono::milliseconds target{50};
    std::uint32_t gpu = 0;
    std::optional<fs::path> out;
    bool list = false;
};

constexpr std::array value_flags{"--plugins"sv,    "--scratch"sv,    "--case"sv, "--min-bytes"sv,
                                 "--max-bytes"sv,  "--min-tokens"sv, "--max-tokens"sv, "--reps"sv,
                                 "--warmups"sv,    "--target-ms"sv,  "--gpu"sv,  "--out"sv};
constexpr std::array switch_flags{"--list"sv, "--help"sv};

bool one_of(std::span<const std::string_view> options, std::string_view text) {
    return std::ranges::find(options, text) != options.end();
}

Result<std::uint64_t> parse_bytes(std::string_view text) {
    std::uint64_t value = 0;
    auto [end, ec] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (ec != std::errc{} || end == text.data()) {
        return std::unexpected(std::format("not a size: {}", text));
    }
    std::string_view suffix{end, static_cast<std::size_t>(text.data() + text.size() - end)};
    constexpr std::array<std::pair<std::string_view, unsigned>, 5> units{
        {{""sv, 0u}, {"K"sv, 10u}, {"M"sv, 20u}, {"G"sv, 30u}, {"T"sv, 40u}}};
    auto unit = std::ranges::find(units, suffix, [](const auto& u) { return u.first; });
    if (unit == units.end()) {
        return std::unexpected(std::format("unknown size suffix: {}", text));
    }
    if (value > (std::numeric_limits<std::uint64_t>::max() >> unit->second)) {
        return std::unexpected(std::format("size too large: {}", text));
    }
    return value << unit->second;
}

Result<std::uint32_t> parse_count(std::string_view text) {
    std::uint32_t value = 0;
    auto [end, ec] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (ec != std::errc{} || end != text.data() + text.size()) {
        return std::unexpected(std::format("not a count: {}", text));
    }
    return value;
}

Result<Config> parse(std::span<char* const> argv) {
    std::vector<std::string_view> args(argv.begin(), argv.end());
    auto indices = std::views::iota(std::size_t{0}, args.size());
    auto stray = std::ranges::find_if(indices, [&](std::size_t i) {
        if (args[i].starts_with("--")) {
            return !one_of(value_flags, args[i]) && !one_of(switch_flags, args[i]);
        }
        return i == 0 || !one_of(value_flags, args[i - 1]);
    });
    if (stray != indices.end()) {
        return std::unexpected(std::format("unexpected argument: {}", args[*stray]));
    }

    auto value = [&](std::string_view flag) -> Result<std::optional<std::string_view>> {
        auto at = std::ranges::find(args, flag);
        if (at == args.end()) {
            return std::optional<std::string_view>{};
        }
        if (std::next(at) == args.end()) {
            return std::unexpected(std::format("{} needs a value", flag));
        }
        return std::optional{*std::next(at)};
    };
    auto assign = [&](std::string_view flag, auto parse_value, auto& target) -> Result<void> {
        return value(flag).and_then([&](std::optional<std::string_view> found) -> Result<void> {
            if (!found) {
                return {};
            }
            return parse_value(*found).transform([&](auto parsed) { target = std::move(parsed); });
        });
    };
    auto as_path = [](std::string_view s) -> Result<fs::path> { return fs::path{s}; };
    auto as_text = [](std::string_view s) -> Result<std::string> { return std::string{s}; };
    auto as_out = [](std::string_view s) -> Result<std::optional<fs::path>> { return std::optional{fs::path{s}}; };
    auto as_ms = [](std::string_view s) {
        return parse_count(s).transform([](std::uint32_t ms) { return std::chrono::milliseconds{ms}; });
    };

    Config config;
    config.list = std::ranges::find(args, "--list"sv) != args.end();
    return assign("--plugins", as_path, config.plugins)
        .and_then([&] { return assign("--scratch", as_path, config.scratch); })
        .and_then([&] { return assign("--case", as_text, config.filter); })
        .and_then([&] { return assign("--min-bytes", parse_bytes, config.min_bytes); })
        .and_then([&] { return assign("--max-bytes", parse_bytes, config.max_bytes); })
        .and_then([&] { return assign("--min-tokens", parse_bytes, config.min_tokens); })
        .and_then([&] { return assign("--max-tokens", parse_bytes, config.max_tokens); })
        .and_then([&] { return assign("--reps", parse_count, config.reps); })
        .and_then([&] { return assign("--warmups", parse_count, config.warmups); })
        .and_then([&] { return assign("--target-ms", as_ms, config.target); })
        .and_then([&] { return assign("--gpu", parse_count, config.gpu); })
        .and_then([&] { return assign("--out", as_out, config.out); })
        .and_then([&]() -> Result<Config> {
            if (config.min_bytes == 0 || config.min_bytes > config.max_bytes) {
                return std::unexpected("--min-bytes must be at least 1 and at most --max-bytes"s);
            }
            if (config.min_tokens == 0 || config.min_tokens > config.max_tokens) {
                return std::unexpected("--min-tokens must be at least 1 and at most --max-tokens"s);
            }
            if (config.reps == 0) {
                return std::unexpected("--reps must be at least 1"s);
            }
            return config;
        });
}

struct CloseLibrary {
    void operator()(void* library) const { dlclose(library); }
};
using Library = std::unique_ptr<void, CloseLibrary>;

struct Case {
    std::string name;
    std::string mechanism;
    std::string_view from;
    std::string_view to;
    tierbench_unit unit;
    std::uint64_t min_size;
    std::uint64_t max_size;
    const tierbench_case* raw;
};

struct Plugin {
    Library library; // declared first, so it is unloaded after everything that points into it
    fs::path path;
    std::string name;
    const tierbench_plugin* raw;
    std::vector<Case> cases;
};

std::optional<std::string_view> tier_name(tierbench_tier tier) {
    switch (tier) {
    case TIERBENCH_HOST: return "host"sv;
    case TIERBENCH_GPU: return "gpu"sv;
    case TIERBENCH_STORAGE: return "storage"sv;
    }
    return std::nullopt;
}

std::optional<std::string_view> unit_name(tierbench_unit unit) {
    switch (unit) {
    case TIERBENCH_BYTES: return "bytes"sv;
    case TIERBENCH_TOKENS: return "tokens"sv;
    }
    return std::nullopt;
}

Result<Plugin> load(const fs::path& path) {
    Library library{dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL)};
    if (!library) {
        const char* why = dlerror();
        return std::unexpected(std::format("{}: {}", path.string(), why ? why : "dlopen failed"));
    }
    auto entry = reinterpret_cast<const tierbench_plugin* (*)()>(dlsym(library.get(), "tierbench_plugin_v1"));
    if (!entry) {
        return std::unexpected(std::format("{}: no tierbench_plugin_v1 symbol", path.string()));
    }
    const tierbench_plugin* raw = entry();
    if (!raw || raw->abi_version != TIERBENCH_ABI_VERSION) {
        return std::unexpected(std::format("{}: plugin ABI version is not {}", path.string(), TIERBENCH_ABI_VERSION));
    }
    if (!raw->name || !raw->facts || (raw->case_count > 0 && !raw->cases)) {
        return std::unexpected(std::format("{}: plugin descriptor is incomplete", path.string()));
    }
    std::span<const tierbench_case> cases{raw->cases, raw->case_count};
    auto incomplete = std::ranges::find_if(cases, [](const tierbench_case& c) {
        return !c.name || !c.mechanism || !c.setup || !c.run || !c.teardown || c.min_size == 0
            || c.min_size > c.max_size || !tier_name(c.from) || !tier_name(c.to) || !unit_name(c.unit);
    });
    if (incomplete != cases.end()) {
        return std::unexpected(std::format("{}: case {} is incomplete", path.string(),
                                           std::ranges::distance(cases.begin(), incomplete)));
    }
    Plugin plugin{std::move(library), path, raw->name, raw, {}};
    std::ranges::transform(cases, std::back_inserter(plugin.cases), [](const tierbench_case& c) {
        return Case{c.name, c.mechanism, *tier_name(c.from), *tier_name(c.to), c.unit, c.min_size, c.max_size, &c};
    });
    return plugin;
}

Result<std::vector<fs::path>> discover(const fs::path& dir) {
    std::error_code error;
    fs::directory_iterator entries{dir, error};
    if (error) {
        return std::unexpected(std::format("cannot read {}: {}", dir.string(), error.message()));
    }
    auto is_plugin = [](const fs::directory_entry& entry) {
        std::error_code ignored;
        auto extension = entry.path().extension();
        return entry.is_regular_file(ignored) && (extension == ".so" || extension == ".dylib");
    };
    std::vector<fs::path> paths;
    std::ranges::copy(entries | std::views::filter(is_plugin)
                          | std::views::transform([](const fs::directory_entry& e) { return e.path(); }),
                      std::back_inserter(paths));
    std::ranges::sort(paths);
    return paths;
}

struct Teardown {
    void (*release)(void*);
    void operator()(void* state) const { release(state); }
};
using State = std::unique_ptr<void, Teardown>;

struct Sample {
    std::chrono::nanoseconds wall;
    tierbench_measure measure;
};

std::string message_of(const tierbench_error& error) {
    return std::string{error.message, strnlen(error.message, sizeof error.message)};
}

Result<Sample> measure(const Case& c, void* state, std::uint64_t iterations) {
    tierbench_measure out{};
    tierbench_error error{};
    auto start = Clock::now();
    int status = c.raw->run(state, iterations, &out, &error);
    auto wall = std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start);
    if (status != 0) {
        auto text = message_of(error);
        return std::unexpected(text.empty() ? std::format("run returned {}", status) : text);
    }
    return Sample{std::max(wall, std::chrono::nanoseconds{1}), out};
}

Result<std::uint64_t> calibrate(const Case& c, void* state, std::uint64_t iterations,
                                std::chrono::nanoseconds target, unsigned attempts) {
    return measure(c, state, iterations).and_then([&](const Sample& sample) -> Result<std::uint64_t> {
        if (sample.wall >= target || attempts == 0) {
            return iterations;
        }
        double scale = std::clamp(static_cast<double>(target.count())
                                      / std::max(static_cast<double>(sample.wall.count()), 1000.0),
                                  2.0, 100.0);
        return calibrate(c, state, static_cast<std::uint64_t>(std::ceil(static_cast<double>(iterations) * scale)),
                         target, attempts - 1);
    });
}

struct Run {
    std::string prefix;
    std::FILE* sink;
    std::size_t failures = 0;
};

std::string utc_now() {
    auto now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm tm{};
    gmtime_r(&now, &tm);
    std::array<char, 32> text{};
    std::strftime(text.data(), text.size(), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return text.data();
}

std::string host_fields(const HostFacts& h) {
    return std::format(R"("node":{},"os":{},"os_version":{},"kernel":{},"machine":{},"cpu":{},)"
                       R"("l1d_bytes":{},"l2_bytes":{},"l3_bytes":{},"memory_bytes":{},"page_size":{})",
                       quote(h.node), quote(h.os), quote(h.os_version), quote(h.kernel), quote(h.machine),
                       quote(h.cpu), h.l1d_bytes, h.l2_bytes, h.l3_bytes, h.memory_bytes, h.page_size);
}

std::string row(const Run& run, const Plugin& plugin, std::string_view facts, const Case& c, std::uint64_t size,
                std::uint32_t rep, std::uint64_t iterations, const Result<Sample>& sample,
                std::string_view failure = "error") {
    auto head = std::format(
        R"({{{},"plugin":{},"plugin_facts":{},"case":{},"mechanism":{},"from":{},"to":{},"size":{},"unit":{},)"
        R"("rep":{},"iterations":{},)",
        run.prefix, quote(plugin.name), quote(facts), quote(c.name), quote(c.mechanism), quote(c.from), quote(c.to),
        size, quote(*unit_name(c.unit)), rep, iterations);
    if (!sample) {
        return head + std::format(R"("wall_ns":null,"ns_per_iteration":null,"gbps":null,"device_ns":null,)"
                                  R"("device_gbps":null,"tflops":null,"device_tflops":null,"status":{},"error":{}}})",
                                  quote(failure), quote(sample.error()));
    }
    const auto& s = *sample;
    auto wall = static_cast<double>(s.wall.count());
    auto moved = static_cast<double>(s.measure.bytes_per_iteration) * static_cast<double>(iterations);
    auto gbps = s.measure.bytes_per_iteration ? std::optional{moved / wall} : std::nullopt;
    auto device_ns = s.measure.has_device_ns ? std::optional{s.measure.device_ns} : std::nullopt;
    auto device_gbps = device_ns && *device_ns && s.measure.bytes_per_iteration
                         ? std::optional{moved / static_cast<double>(*device_ns)}
                         : std::nullopt;
    auto ops = static_cast<double>(s.measure.ops_per_iteration) * static_cast<double>(iterations);
    auto tflops = s.measure.ops_per_iteration ? std::optional{ops / wall / 1e3} : std::nullopt;
    auto device_tflops = device_ns && *device_ns && s.measure.ops_per_iteration
                           ? std::optional{ops / static_cast<double>(*device_ns) / 1e3}
                           : std::nullopt;
    return head + std::format(R"("wall_ns":{},"ns_per_iteration":{},"gbps":{},"device_ns":{},"device_gbps":{},)"
                              R"("tflops":{},"device_tflops":{},"status":"ok","error":null}})",
                              s.wall.count(), wall / static_cast<double>(iterations), number_or_null(gbps),
                              number_or_null(device_ns), number_or_null(device_gbps), number_or_null(tflops),
                              number_or_null(device_tflops));
}

void emit(Run& run, const std::string& line) {
    std::fputs(line.c_str(), run.sink);
    std::fputc('\n', run.sink);
    std::fflush(run.sink);
}

enum class Rate { bandwidth, latency, compute };

struct Rates {
    std::vector<double> values;
    Rate kind = Rate::bandwidth;
};

Rate rate_of(const tierbench_measure& measure) {
    if (measure.ops_per_iteration) {
        return Rate::compute;
    }
    return measure.bytes_per_iteration ? Rate::bandwidth : Rate::latency;
}

double rate_value(Rate kind, const Sample& sample, std::uint64_t iterations) {
    auto per_ns = static_cast<double>(iterations) / static_cast<double>(sample.wall.count());
    switch (kind) {
    case Rate::compute: return static_cast<double>(sample.measure.ops_per_iteration) * per_ns / 1e3;
    case Rate::bandwidth: return static_cast<double>(sample.measure.bytes_per_iteration) * per_ns;
    case Rate::latency: return 1.0 / per_ns;
    }
    return 0;
}

Rates run_size(Run& run, const Plugin& plugin, std::string_view facts, const Case& c, std::uint64_t size,
               const Config& config, const tierbench_env& env) {
    Rates rates;
    auto fail = [&](std::uint32_t rep, std::uint64_t iterations, std::string why) {
        ++run.failures;
        emit(run, row(run, plugin, facts, c, size, rep, iterations, std::unexpected(std::move(why))));
    };
    tierbench_error error{};
    State state{c.raw->setup(size, &env, &error), Teardown{c.raw->teardown}};
    if (!state) {
        auto why = message_of(error);
        if (error.unsupported) {
            emit(run, row(run, plugin, facts, c, size, 0, 0, std::unexpected(why), "unsupported"));
        } else {
            fail(0, 0, why.empty() ? "setup failed"s : why);
        }
        return rates;
    }
    auto iterations = calibrate(c, state.get(), 1, config.target, 24);
    if (!iterations) {
        fail(0, 0, iterations.error());
        return rates;
    }
    auto attempts = std::views::iota(std::uint32_t{0}, config.warmups + config.reps);
    auto stop_after_failure = [&](std::uint32_t attempt) {
        auto sample = measure(c, state.get(), *iterations);
        auto rep = attempt < config.warmups ? 0 : attempt - config.warmups;
        if (!sample) {
            fail(rep, *iterations, sample.error());
            return true;
        }
        if (attempt >= config.warmups) {
            emit(run, row(run, plugin, facts, c, size, rep, *iterations, sample));
            rates.kind = rate_of(sample->measure);
            rates.values.push_back(rate_value(rates.kind, *sample, *iterations));
        }
        return false;
    };
    static_cast<void>(std::ranges::find_if(attempts, stop_after_failure));
    return rates;
}

std::string human_size(tierbench_unit unit, std::uint64_t size) {
    if (unit == TIERBENCH_TOKENS) {
        return std::format("{}tok", size);
    }
    constexpr std::array suffixes{"B"sv, "K"sv, "M"sv, "G"sv, "T"sv};
    auto shifts = std::views::iota(std::size_t{0}, suffixes.size()) | std::views::reverse;
    auto largest = std::ranges::find_if(shifts, [&](std::size_t i) {
        return size >= (std::uint64_t{1} << (10 * i)) && size % (std::uint64_t{1} << (10 * i)) == 0;
    });
    std::size_t i = largest == shifts.end() ? 0 : *largest;
    return std::format("{}{}", size >> (10 * i), suffixes[i]);
}

void summarize(const Plugin& plugin, const Case& c, std::uint64_t size, Rates rates) {
    if (rates.values.empty()) {
        return;
    }
    auto middle = rates.values.begin() + static_cast<std::ptrdiff_t>(rates.values.size() / 2);
    std::ranges::nth_element(rates.values, middle);
    constexpr std::array labels{"GB/s"sv, "ns/op"sv, "TFLOP/s"sv};
    std::fputs(std::format("{:<36} {:>8} {:>12.3f} {} (n={})\n", plugin.name + "/" + c.name,
                           human_size(c.unit, size), *middle, labels[static_cast<std::size_t>(rates.kind)],
                           rates.values.size())
                   .c_str(),
               stderr);
}

auto ladder(const Case& c, const Config& config) {
    auto tokens = c.unit == TIERBENCH_TOKENS;
    auto low = std::max(c.min_size, tokens ? config.min_tokens : config.min_bytes);
    auto high = std::min(c.max_size, tokens ? config.max_tokens : config.max_bytes);
    return std::views::iota(0u, 63u) | std::views::transform([](unsigned shift) { return std::uint64_t{1} << shift; })
         | std::views::filter([=](std::uint64_t size) { return size >= low && size <= high; });
}

void list(const std::vector<Plugin>& plugins) {
    std::ranges::for_each(plugins, [](const Plugin& plugin) {
        std::fputs(std::format("{} ({})\n", plugin.name, plugin.path.string()).c_str(), stdout);
        std::ranges::for_each(plugin.cases, [](const Case& c) {
            std::fputs(std::format("  {:<24} {:<24} {} -> {}  {}..{}\n", c.name, c.mechanism, c.from, c.to,
                                   human_size(c.unit, c.min_size), human_size(c.unit, c.max_size))
                           .c_str(),
                       stdout);
        });
    });
}

void complain(std::string_view text) {
    std::fputs(std::format("tierbench: {}\n", text).c_str(), stderr);
}

struct CloseFile {
    void operator()(std::FILE* file) const { std::fclose(file); }
};

int run_all(const Config& config) {
    auto paths = discover(config.plugins);
    if (!paths) {
        complain(paths.error());
        return 2;
    }
    std::vector<Plugin> plugins;
    std::size_t load_failures = 0;
    std::ranges::for_each(*paths, [&](const fs::path& path) {
        auto plugin = load(path);
        if (plugin) {
            plugins.push_back(std::move(*plugin));
        } else {
            ++load_failures;
            complain(plugin.error());
        }
    });
    if (plugins.empty()) {
        complain(std::format("no plugins loaded from {}", config.plugins.string()));
        return 2;
    }
    if (config.list) {
        list(plugins);
        return load_failures == 0 ? 0 : 1;
    }

    auto matches = [&](const Plugin& plugin, const Case& c) {
        return config.filter.empty() || (plugin.name + "/" + c.name).find(config.filter) != std::string::npos;
    };
    auto selected_cases = std::accumulate(plugins.begin(), plugins.end(), std::size_t{0},
                                          [&](std::size_t total, const Plugin& plugin) {
                                              return total + static_cast<std::size_t>(std::ranges::count_if(
                                                                 plugin.cases,
                                                                 [&](const Case& c) { return matches(plugin, c); }));
                                          });
    if (selected_cases == 0) {
        complain(std::format("no case matches \"{}\"; loaded:", config.filter));
        list(plugins);
        return 2;
    }

    auto host = host_facts();
    auto scratch = config.scratch.string();
    tierbench_env env{scratch.c_str(), config.gpu, host.page_size};
    std::unique_ptr<std::FILE, CloseFile> owned;
    if (config.out) {
        owned.reset(std::fopen(config.out->c_str(), "a"));
        if (!owned) {
            complain(std::format("cannot open {}: {}", config.out->string(), std::strerror(errno)));
            return 2;
        }
    }
    auto started = utc_now();
    Run run{std::format(R"("schema_version":2,"tool":"tierbench","run":{},"started_at":{},{})",
                        quote(started + "-" + host.node), quote(started), host_fields(host)),
            owned ? owned.get() : stdout};

    std::ranges::for_each(plugins, [&](const Plugin& plugin) {
        const char* text = plugin.raw->facts(&env);
        std::string facts = text ? text : "{}";
        auto selected = plugin.cases | std::views::filter([&](const Case& c) { return matches(plugin, c); });
        std::ranges::for_each(selected, [&](const Case& c) {
            std::fputs(std::format("{}/{}: {}\n", plugin.name, c.name, c.mechanism).c_str(), stderr);
            std::ranges::for_each(ladder(c, config), [&](std::uint64_t size) {
                summarize(plugin, c, size, run_size(run, plugin, facts, c, size, config, env));
            });
        });
    });
    return run.failures + load_failures == 0 ? 0 : 1;
}

}
}

int main(int argc, char** argv) {
    using namespace tierbench;
    using namespace std::literals;
    std::span<char* const> args{argv + 1, static_cast<std::size_t>(std::max(argc - 1, 0))};
    auto help = std::ranges::find(args, "--help"sv, [](const char* arg) { return std::string_view{arg}; });
    if (help != args.end()) {
        std::fputs(usage.data(), stdout);
        return 0;
    }
    auto config = parse(args);
    if (!config) {
        complain(config.error());
        std::fputs(usage.data(), stderr);
        return 2;
    }
    return run_all(*config);
}
