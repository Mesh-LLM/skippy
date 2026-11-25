#pragma once

#include "gpu_api.h"
#include "tierbench.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <ranges>

namespace tierbench_gpu {

constexpr std::size_t page = 4096;
inline bool ok(GPU(Error_t) status, tierbench_error* error, const char* what) {
    if (status == GPU(Success)) {
        return true;
    }
    std::snprintf(error->message, sizeof error->message, "%s: %s", what, GPU(GetErrorString)(status));
    return false;
}

inline bool ok(int status, tierbench_error* error, const char* what) {
    return ok(static_cast<GPU(Error_t)>(status), error, what);
}

inline void release_pageable(void* p) { std::free(p); }
inline void release_pinned(void* p) { static_cast<void>(gpu_free_host(p)); }
inline void release_device(void* p) { static_cast<void>(GPU(Free)(p)); }

using Owned = std::unique_ptr<void, void (*)(void*)>;

enum class Memory { pageable, pinned, mapped, device };

inline Owned allocate(Memory kind, std::size_t bytes, tierbench_error* error) {
    void* p = nullptr;
    switch (kind) {
    case Memory::pageable:
        p = std::aligned_alloc(page, (bytes + page - 1) / page * page);
        if (!p) {
            std::snprintf(error->message, sizeof error->message, "host allocation failed");
            return {nullptr, release_pageable};
        }
        std::memset(p, 1, bytes);
        return {p, release_pageable};
    case Memory::pinned:
    case Memory::mapped:
        if (!ok(kind == Memory::pinned ? gpu_malloc_pinned(&p, bytes) : gpu_malloc_mapped(&p, bytes), error,
                "pinned host allocation")) {
            return {nullptr, release_pinned};
        }
        std::memset(p, 1, bytes);
        return {p, release_pinned};
    case Memory::device: {
        if (!ok(GPU(Malloc)(&p, bytes), error, "device allocation")) {
            return {nullptr, release_device};
        }
        Owned owned{p, release_device};
        if (!ok(GPU(Memset)(p, 1, bytes), error, "device memset")) {
            return {nullptr, release_device};
        }
        return owned;
    }
    }
    return {nullptr, release_pageable};
}

struct Timer {
    GPU(Stream_t) stream{};
    GPU(Event_t) start{};
    GPU(Event_t) stop{};

    Timer() = default;
    Timer(const Timer&) = delete;
    Timer& operator=(const Timer&) = delete;
    ~Timer() {
        if (stop) static_cast<void>(GPU(EventDestroy)(stop));
        if (start) static_cast<void>(GPU(EventDestroy)(start));
        if (stream) static_cast<void>(GPU(StreamDestroy)(stream));
    }
};

inline int attribute(gpu_device_attr attr, int device) {
    int value = 0;
    return GPU(DeviceGetAttribute)(&value, attr, device) == GPU(Success) ? value : 0;
}

inline bool start_timer(Timer& timer, tierbench_error* error) {
    return ok(GPU(StreamCreate)(&timer.stream), error, "stream") && ok(GPU(EventCreate)(&timer.start), error, "event")
        && ok(GPU(EventCreate)(&timer.stop), error, "event");
}

template <class Issue>
int timed(Timer& timer, std::uint64_t launches, std::uint64_t bytes_per_iteration, std::uint64_t ops_per_iteration,
          tierbench_measure* out, tierbench_error* error, Issue issue) {
    if (!ok(GPU(EventRecord)(timer.start, timer.stream), error, "record start")) {
        return 1;
    }
    auto each = std::views::iota(std::uint64_t{0}, launches);
    auto failed = std::ranges::find_if(each, [&](std::uint64_t) { return !ok(issue(), error, "launch"); });
    float ms = 0;
    if (failed != each.end() || !ok(GPU(EventRecord)(timer.stop, timer.stream), error, "record stop")
        || !ok(GPU(EventSynchronize)(timer.stop), error, "synchronize")
        || !ok(GPU(EventElapsedTime)(&ms, timer.start, timer.stop), error, "elapsed time")) {
        return 1;
    }
    out->bytes_per_iteration = bytes_per_iteration;
    out->ops_per_iteration = ops_per_iteration;
    out->device_ns = static_cast<std::uint64_t>(static_cast<double>(ms) * 1e6);
    out->has_device_ns = 1;
    return 0;
}

}
