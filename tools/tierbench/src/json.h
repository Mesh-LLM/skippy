#pragma once

#include <algorithm>
#include <format>
#include <optional>
#include <string>
#include <string_view>

namespace tierbench {

inline std::string quote(std::string_view text) {
    std::string out = "\"";
    std::ranges::for_each(text, [&](char c) {
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (static_cast<unsigned char>(c) < 0x20) {
                out += std::format("\\u{:04x}", static_cast<unsigned>(c));
            } else {
                out += c;
            }
        }
    });
    out += '"';
    return out;
}

template <class T>
std::string number_or_null(const std::optional<T>& value) {
    return value ? std::format("{}", *value) : std::string{"null"};
}

}
