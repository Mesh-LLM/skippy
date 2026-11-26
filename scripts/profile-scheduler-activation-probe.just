set positional-arguments

build source bundle output:
    c++ -std=c++17 -O2 -Wall -Wextra -Werror -I"$1/include" -I"$1/ggml/include" -I"$1/vendor/nlohmann" "{{justfile_directory()}}/profile-scheduler-activation-probe.cpp" -L"$2/lib" -Wl,-rpath,"$2/lib" -lllama -lggml -lggml-base -o "$3"
