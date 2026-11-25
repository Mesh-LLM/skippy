#pragma once

#include <cstdint>
#include <string>

namespace tierbench {

struct HostFacts {
    std::string node;
    std::string os;
    std::string os_version;
    std::string kernel;
    std::string machine;
    std::string cpu;
    std::uint64_t l1d_bytes = 0;
    std::uint64_t l2_bytes = 0;
    std::uint64_t l3_bytes = 0;
    std::uint64_t memory_bytes = 0;
    std::uint64_t page_size = 0;
};

HostFacts host_facts();

}
