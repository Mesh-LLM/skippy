set positional-arguments

build source output:
    cmake -S "$1" -B "$2" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_DEPLOYMENT_TARGET=13.3 -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DGGML_CCACHE=OFF -DLLAMA_BUILD_COMMON=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=OFF -DLLAMA_BUILD_SERVER=OFF -DLLAMA_BUILD_MTMD=OFF -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF
    cmake --build "$2" --parallel 8 --target ggml-metal

regression source bundle output:
    c++ -std=c++17 -O2 -ffp-contract=off -Wall -Wextra -Werror -I"$1/ggml/include" -I"$1/vendor/nlohmann" "{{justfile_directory()}}/profile-scheduler-q4-scale-regression.cpp" -L"$2/lib" -Wl,-rpath,"$2/lib" -lggml -lggml-base -lggml-metal -o "$3"
