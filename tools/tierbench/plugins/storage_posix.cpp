#include "tierbench.h"

#include <fcntl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

#if defined(__linux__)
#include <sys/vfs.h>
#endif

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <format>
#include <memory>
#include <ranges>
#include <span>
#include <string>

namespace {

constexpr std::size_t page = 4096;

struct Free {
    void operator()(void* p) const { std::free(p); }
};

void fail(tierbench_error* error, const char* what) {
    std::snprintf(error->message, sizeof error->message, "%s: %s", what, std::strerror(errno));
}

enum class Mode { write_sync, read_direct, read_cached };

std::atomic<unsigned> serial{0};

struct File {
    std::string path;
    int fd = -1;
    std::size_t bytes = 0;
    std::unique_ptr<std::byte[], Free> buffer;

    File() = default;
    File(const File&) = delete;
    File& operator=(const File&) = delete;
    ~File() {
        if (fd >= 0) {
            close(fd);
        }
        if (!path.empty()) {
            unlink(path.c_str());
        }
    }
};

bool sync_file(int fd) {
#if defined(__APPLE__)
    return fcntl(fd, F_FULLFSYNC) == 0;
#else
    return fsync(fd) == 0;
#endif
}

bool bypass_cache([[maybe_unused]] int fd) {
#if defined(__APPLE__)
    return fcntl(fd, F_NOCACHE, 1) == 0;
#else
    return true;
#endif
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

bool read_all(int fd, std::byte* data, std::size_t size, off_t offset) {
    if (size == 0) {
        return true;
    }
    auto got = pread(fd, data, size, offset);
    if (got <= 0) {
        return false;
    }
    auto done = static_cast<std::size_t>(got);
    return read_all(fd, data + done, size - done, offset + static_cast<off_t>(done));
}

template <Mode M>
void* setup(std::uint64_t bytes, const tierbench_env* env, tierbench_error* error) {
    auto file = std::make_unique<File>();
    file->bytes = static_cast<std::size_t>(bytes);
    file->buffer.reset(static_cast<std::byte*>(std::aligned_alloc(page, (file->bytes + page - 1) / page * page)));
    if (!file->buffer) {
        fail(error, "allocation");
        return nullptr;
    }
    std::ranges::fill(std::span{file->buffer.get(), file->bytes}, std::byte{0x5a});
    file->path = std::format("{}/tierbench-{}-{}.dat", env->scratch_dir, getpid(), serial.fetch_add(1));
    int flags = O_RDWR | O_CREAT | O_TRUNC;
#if defined(O_DIRECT)
    if constexpr (M == Mode::read_direct) {
        flags |= O_DIRECT;
    }
#endif
    file->fd = open(file->path.c_str(), flags, 0600);
    if (file->fd < 0) {
        file->path.clear();
        error->unsupported = M == Mode::read_direct && errno == EINVAL;
        fail(error, "open in the scratch directory");
        return nullptr;
    }
    if (M == Mode::read_direct && !bypass_cache(file->fd)) {
        fail(error, "F_NOCACHE");
        return nullptr;
    }
    if (M != Mode::write_sync
        && (!write_all(file->fd, file->buffer.get(), file->bytes, 0) || !sync_file(file->fd))) {
        fail(error, "prefill");
        return nullptr;
    }
    return file.release();
}

void teardown(void* state) {
    delete static_cast<File*>(state);
}

template <Mode M>
int run(void* state, std::uint64_t iterations, tierbench_measure* out, tierbench_error* error) {
    auto& file = *static_cast<File*>(state);
    auto attempts = std::views::iota(std::uint64_t{0}, iterations);
    auto failed = std::ranges::find_if(attempts, [&](std::uint64_t) {
        if constexpr (M == Mode::write_sync) {
            return !write_all(file.fd, file.buffer.get(), file.bytes, 0) || !sync_file(file.fd);
        } else {
            return !read_all(file.fd, file.buffer.get(), file.bytes, 0);
        }
    });
    if (failed != attempts.end()) {
        fail(error, M == Mode::write_sync ? "write" : "read");
        return 1;
    }
    out->bytes_per_iteration = file.bytes;
    return 0;
}

#if defined(__APPLE__)
constexpr const char* sync_name = "pwrite+F_FULLFSYNC";
constexpr const char* direct_name = "pread+F_NOCACHE";
#else
constexpr const char* sync_name = "pwrite+fsync";
constexpr const char* direct_name = "pread+O_DIRECT";
#endif

constexpr std::uint64_t large = std::uint64_t{1} << 34;

constexpr tierbench_case cases[] = {
    {"write-sync", sync_name, TIERBENCH_HOST, TIERBENCH_STORAGE, TIERBENCH_BYTES, page, large, setup<Mode::write_sync>,
     run<Mode::write_sync>, teardown},
    {"read-direct", direct_name, TIERBENCH_STORAGE, TIERBENCH_HOST, TIERBENCH_BYTES, page, large, setup<Mode::read_direct>,
     run<Mode::read_direct>, teardown},
    {"read-cached", "pread+page-cache", TIERBENCH_STORAGE, TIERBENCH_HOST, TIERBENCH_BYTES, page, large, setup<Mode::read_cached>,
     run<Mode::read_cached>, teardown},
};

std::string facts_text;

const char* facts(const tierbench_env* env) {
    struct statfs info{};
    if (statfs(env->scratch_dir, &info) != 0) {
        facts_text = std::format(R"({{"scratch_dir":"{}","error":"statfs failed"}})", env->scratch_dir);
        return facts_text.c_str();
    }
#if defined(__APPLE__)
    facts_text = std::format(R"({{"scratch_dir":"{}","filesystem":"{}","device":"{}","block_size":{}}})",
                             env->scratch_dir, info.f_fstypename, info.f_mntfromname, info.f_bsize);
#else
    facts_text = std::format(R"({{"scratch_dir":"{}","filesystem_magic":"0x{:x}","block_size":{}}})",
                             env->scratch_dir, static_cast<unsigned long>(info.f_type), info.f_bsize);
#endif
    return facts_text.c_str();
}

constexpr tierbench_plugin plugin{TIERBENCH_ABI_VERSION, "storage_posix", facts,
                                  static_cast<std::uint32_t>(std::size(cases)), cases};

}

extern "C" const tierbench_plugin* tierbench_plugin_v1(void) {
    return &plugin;
}
