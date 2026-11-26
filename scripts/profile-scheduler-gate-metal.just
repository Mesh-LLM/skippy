set positional-arguments

build source bundle output:
    c++ -std=c++17 -O2 -ffp-contract=off -Wall -Wextra -Werror -I"$1/include" -I"$1/ggml/include" -I"$1/vendor/nlohmann" "{{justfile_directory()}}/profile-scheduler-gate-metal.cpp" -L"$2/lib" -Wl,-rpath,"$2/lib" -lllama -lggml -lggml-base -lggml-metal -o "$3"
