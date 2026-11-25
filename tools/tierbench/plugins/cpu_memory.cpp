#include "tierbench.h"

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <iterator>
#include <latch>
#include <memory>
#include <numeric>
#include <random>
#include <ranges>
#include <span>
#include <thread>
#include <vector>

#if defined(__APPLE__)
#include <pthread.h>
#include <pthread/qos.h>
#endif

namespace {

constexpr std::size_t page = 4096;

struct Free {
    void operator()(void* p) const { std::free(p); }
};

template <class T>
using Buffer = std::unique_ptr<T[], Free>;

template <class T>
Buffer<T> allocate(std::size_t count) {
    auto bytes = (count * sizeof(T) + page - 1) / page * page;
    return Buffer<T>{static_cast<T*>(std::aligned_alloc(page, bytes))};
}

void fail(tierbench_error* error, const char* text) {
    std::snprintf(error->message, sizeof error->message, "%s", text);
}

void clobber() {
    asm volatile("" ::: "memory");
}

volatile std::uint64_t sink;
std::atomic<std::uint64_t> team_sink{0};

struct Words {
    std::size_t count = 0;
    Buffer<std::uint64_t> source;
    Buffer<std::uint64_t> target;

    std::size_t bytes() const { return count * sizeof(std::uint64_t); }
    explicit operator bool() const { return source && target; }
};

Words make_words(std::uint64_t bytes) {
    auto count = static_cast<std::size_t>(bytes / sizeof(std::uint64_t));
    Words words{count, allocate<std::uint64_t>(count), allocate<std::uint64_t>(count)};
    if (words) {
        std::span source{words.source.get(), count};
        std::iota(source.begin(), source.end(), std::uint64_t{1});
        std::ranges::fill(std::span{words.target.get(), count}, 0);
    }
    return words;
}

enum class Op { read, write, copy };

template <Op O>
std::uint64_t pass(Words& words, std::uint64_t i) {
    clobber();
    if constexpr (O == Op::read) {
        std::span source{words.source.get(), words.count};
        return std::reduce(source.begin(), source.end(), i, std::bit_xor<>{});
    } else if constexpr (O == Op::write) {
        std::ranges::fill(std::span{words.target.get(), words.count}, i);
        return 0;
    } else {
        std::memcpy(words.target.get(), words.source.get(), words.bytes());
        return 0;
    }
}

template <Op O>
std::uint64_t passes(Words& words, std::uint64_t iterations) {
    auto each = std::views::iota(std::uint64_t{0}, iterations);
    return std::accumulate(each.begin(), each.end(), std::uint64_t{0},
                           [&](std::uint64_t acc, std::uint64_t i) { return acc ^ pass<O>(words, i); });
}

void* setup_words(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    auto words = std::make_unique<Words>(make_words(bytes));
    if (!*words) {
        fail(error, "allocation failed");
        return nullptr;
    }
    return words.release();
}

void teardown_words(void* state) {
    delete static_cast<Words*>(state);
}

template <Op O>
int run_single(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error*) {
    auto& words = *static_cast<Words*>(state);
    sink = passes<O>(words, iterations);
    out->bytes_per_iteration = words.bytes();
    return 0;
}

// each thread works on its own `bytes`-sized buffers, so the size sweep crosses each core's caches
struct Team {
    std::vector<Words> slots;
};

unsigned team_size(unsigned wanted) {
    return wanted == 0 ? std::max(std::thread::hardware_concurrency(), 1u) : wanted;
}

template <unsigned Threads>
void* setup_team(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    auto available = std::max(std::thread::hardware_concurrency(), 1u);
    if (Threads > available) {
        error->unsupported = 1;
        std::snprintf(error->message, sizeof error->message, "%u threads; this machine has %u", Threads, available);
        return nullptr;
    }
    auto team = std::make_unique<Team>();
    std::ranges::generate_n(std::back_inserter(team->slots), team_size(Threads), [&] { return make_words(bytes); });
    if (!std::ranges::all_of(team->slots, [](const Words& words) { return static_cast<bool>(words); })) {
        fail(error, "allocation failed");
        return nullptr;
    }
    return team.release();
}

void teardown_team(void* state) {
    delete static_cast<Team*>(state);
}

void prefer_performance_cores() {
#if defined(__APPLE__)
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
#endif
}

template <Op O>
int run_team(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error*) {
    auto& team = *static_cast<Team*>(state);
    std::latch start{static_cast<std::ptrdiff_t>(team.slots.size())};
    {
        std::vector<std::jthread> workers;
        workers.reserve(team.slots.size());
        std::ranges::transform(team.slots, std::back_inserter(workers), [&](Words& words) {
            return std::jthread([&] {
                prefer_performance_cores();
                start.arrive_and_wait();
                team_sink.fetch_xor(passes<O>(words, iterations), std::memory_order_relaxed);
            });
        });
    }
    out->bytes_per_iteration = team.slots.front().bytes() * team.slots.size();
    return 0;
}

struct alignas(64) Node {
    Node* next;
};

struct Chain {
    std::size_t count;
    Buffer<Node> nodes;
    Node* at = nullptr;
};

void* setup_chain(std::uint64_t bytes, const tierbench_env*, tierbench_error* error) {
    auto count = static_cast<std::size_t>(bytes / sizeof(Node));
    auto state = std::make_unique<Chain>(Chain{count, allocate<Node>(count)});
    if (!state->nodes) {
        fail(error, "allocation failed");
        return nullptr;
    }
    std::vector<std::size_t> order(count);
    std::iota(order.begin(), order.end(), std::size_t{0});
    std::ranges::shuffle(order, std::mt19937_64{42});
    std::span nodes{state->nodes.get(), count};
    std::ranges::for_each(std::views::iota(std::size_t{0}, count), [&](std::size_t k) {
        nodes[order[k]].next = &nodes[order[(k + 1) % count]];
    });
    state->at = &nodes[order[0]];
    return state.release();
}

void teardown_chain(void* state) {
    delete static_cast<Chain*>(state);
}

int run_chase(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error*) {
    auto& chain = *static_cast<Chain*>(state);
    auto steps = std::views::iota(std::uint64_t{0}, iterations);
    chain.at = std::accumulate(steps.begin(), steps.end(), chain.at, [](Node* at, std::uint64_t) { return at->next; });
    sink = reinterpret_cast<std::uintptr_t>(chain.at);
    out->bytes_per_iteration = 0;
    return 0;
}

constexpr std::uint64_t large = std::uint64_t{1} << 34;

template <Op O, unsigned Threads>
constexpr tierbench_case team_case(const char* name) {
    return {name, "per-thread", TIERBENCH_HOST, TIERBENCH_HOST, TIERBENCH_BYTES, 1024, large, setup_team<Threads>, run_team<O>,
            teardown_team};
}

constexpr tierbench_case cases[] = {
    {"read", "load", TIERBENCH_HOST, TIERBENCH_HOST, TIERBENCH_BYTES, 1024, large, setup_words, run_single<Op::read>, teardown_words},
    {"write", "store", TIERBENCH_HOST, TIERBENCH_HOST, TIERBENCH_BYTES, 1024, large, setup_words, run_single<Op::write>, teardown_words},
    {"copy", "memcpy", TIERBENCH_HOST, TIERBENCH_HOST, TIERBENCH_BYTES, 1024, large, setup_words, run_single<Op::copy>, teardown_words},
    {"latency", "dependent-load", TIERBENCH_HOST, TIERBENCH_HOST, TIERBENCH_BYTES, 1024, large, setup_chain, run_chase, teardown_chain},
    team_case<Op::read, 2>("read-x2"),
    team_case<Op::read, 4>("read-x4"),
    team_case<Op::read, 8>("read-x8"),
    team_case<Op::read, 0>("read-all"),
    team_case<Op::write, 2>("write-x2"),
    team_case<Op::write, 4>("write-x4"),
    team_case<Op::write, 8>("write-x8"),
    team_case<Op::write, 0>("write-all"),
    team_case<Op::copy, 2>("copy-x2"),
    team_case<Op::copy, 4>("copy-x4"),
    team_case<Op::copy, 8>("copy-x8"),
    team_case<Op::copy, 0>("copy-all"),
};

const char* facts(const tierbench_env*) {
    return "{}";
}

constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, "cpu_memory", facts,
                                  static_cast<std::uint32_t>(std::size(cases)), cases};

}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) {
    return &plugin;
}
