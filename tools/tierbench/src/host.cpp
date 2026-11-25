#include "host.h"

#include <sys/utsname.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <format>
#include <fstream>
#include <iterator>
#include <ranges>
#include <string_view>

#if defined(__APPLE__)
#include <sys/sysctl.h>
#endif

namespace tierbench {
namespace {

namespace fs = std::filesystem;

std::string trim(std::string_view text) {
    auto first = text.find_first_not_of(" \t\n\"");
    if (first == std::string_view::npos) {
        return {};
    }
    auto last = text.find_last_not_of(" \t\n\"");
    return std::string{text.substr(first, last - first + 1)};
}

std::string read_file(const fs::path& path) {
    std::ifstream in(path);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

[[maybe_unused]] std::string field(std::string_view text, std::string_view key, char separator) {
    auto lines = text | std::views::split('\n')
               | std::views::transform([](auto&& line) { return std::string_view(line.begin(), line.end()); });
    auto line = std::ranges::find_if(lines, [&](std::string_view l) { return l.starts_with(key); });
    if (line == lines.end()) {
        return {};
    }
    std::string_view found = *line;
    auto at = found.find(separator);
    return at == std::string_view::npos ? std::string{} : trim(found.substr(at + 1));
}

[[maybe_unused]] std::uint64_t parse_size(std::string_view text) {
    std::uint64_t value = 0;
    auto [end, ec] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (ec != std::errc{}) {
        return 0;
    }
    std::string_view suffix{end, static_cast<std::size_t>(text.data() + text.size() - end)};
    if (suffix == "K") return value << 10;
    if (suffix == "M") return value << 20;
    if (suffix == "G") return value << 30;
    return value;
}

std::string node_name() {
    if (const char* node = std::getenv("JIANYANG_NODE"); node && *node) {
        return node;
    }
    std::array<char, 256> name{};
    return gethostname(name.data(), name.size()) == 0 ? std::string{name.data()} : std::string{};
}

#if defined(__APPLE__)

std::uint64_t sysctl_number(const char* name) {
    std::uint64_t value = 0;
    std::size_t size = sizeof value;
    return sysctlbyname(name, &value, &size, nullptr, 0) == 0 ? value : 0;
}

std::string sysctl_text(const char* name) {
    std::size_t size = 0;
    if (sysctlbyname(name, nullptr, &size, nullptr, 0) != 0 || size == 0) {
        return {};
    }
    std::string text(size, '\0');
    if (sysctlbyname(name, text.data(), &size, nullptr, 0) != 0) {
        return {};
    }
    text.resize(strnlen(text.data(), size));
    return text;
}

std::uint64_t first_nonzero(std::initializer_list<const char*> names) {
    auto values = names | std::views::transform(sysctl_number);
    auto found = std::ranges::find_if(values, [](std::uint64_t v) { return v != 0; });
    return found == values.end() ? 0 : *found;
}

void platform_facts(HostFacts& facts) {
    facts.os_version = sysctl_text("kern.osproductversion");
    facts.cpu = sysctl_text("machdep.cpu.brand_string");
    facts.l1d_bytes = first_nonzero({"hw.perflevel0.l1dcachesize", "hw.l1dcachesize"});
    facts.l2_bytes = first_nonzero({"hw.perflevel0.l2cachesize", "hw.l2cachesize"});
    facts.l3_bytes = sysctl_number("hw.l3cachesize");
    facts.memory_bytes = sysctl_number("hw.memsize");
}

#else

std::uint64_t cache_bytes(unsigned level) {
    fs::path base = "/sys/devices/system/cpu/cpu0/cache";
    auto indices = std::views::iota(0, 8)
                 | std::views::transform([&](int i) { return base / std::format("index{}", i); });
    auto found = std::ranges::find_if(indices, [&](const fs::path& dir) {
        return trim(read_file(dir / "level")) == std::to_string(level)
            && trim(read_file(dir / "type")) != "Instruction";
    });
    return found == indices.end() ? 0 : parse_size(trim(read_file(*found / "size")));
}

void platform_facts(HostFacts& facts) {
    facts.os_version = field(read_file("/etc/os-release"), "PRETTY_NAME", '=');
    facts.cpu = field(read_file("/proc/cpuinfo"), "model name", ':');
    facts.l1d_bytes = cache_bytes(1);
    facts.l2_bytes = cache_bytes(2);
    facts.l3_bytes = cache_bytes(3);
    facts.memory_bytes = static_cast<std::uint64_t>(sysconf(_SC_PHYS_PAGES))
                       * static_cast<std::uint64_t>(sysconf(_SC_PAGESIZE));
}

#endif

}

HostFacts host_facts() {
    HostFacts facts;
    facts.node = node_name();
    utsname name{};
    if (uname(&name) == 0) {
        facts.os = name.sysname;
        facts.kernel = name.release;
        facts.machine = name.machine;
    }
    facts.page_size = static_cast<std::uint64_t>(sysconf(_SC_PAGESIZE));
    platform_facts(facts);
    return facts;
}

}
