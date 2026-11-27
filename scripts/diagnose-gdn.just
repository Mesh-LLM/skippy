prepare-build:
    just llama-prepare
    LLAMA_STAGE_BACKEND=cuda LLAMA_STAGE_CUDA_ARCHITECTURES=121 LLAMA_STAGE_LINK_MODE=static LLAMA_STAGE_FULL_REPLAY=ON scripts/build-llama.sh --build-only --target test-backend-ops
