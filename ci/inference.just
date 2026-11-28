# Qualification controller and execution recipes. Changes select inference
# qualification, without rebuilding the separately packaged runtimes.

[positional-arguments]
inference-plan profile source output="target/ci-evidence/inference-plan.json":
    @"{{ automation_python }}" scripts/inference-plan.py plan "$@"

[positional-arguments]
inference-dispatch profile source output="target/ci-evidence/inference" *configuration:
    @"{{ automation_python }}" scripts/inference-dispatch.py dispatch "$@"

[positional-arguments]
inference-execute profile model mode:
    @"{{ automation_python }}" scripts/inference-execute.py "$@"

[positional-arguments]
inference-cancel ledger="target/ci-evidence/inference/jobs.json":
    @"{{ automation_python }}" scripts/inference-dispatch.py cancel "$@"

inference-build:
    @"{{ automation_python }}" scripts/inference-build.py

inference-build-uncached:
    @command -v hf uv nvidia-smi
    @SCCACHE_BASEDIRS="$PWD" LLAMA_STAGE_BACKEND=cuda LLAMA_STAGE_CUDA_ARCHITECTURES=121 LLAMA_STAGE_LINK_MODE=static just llama-build
    @LLAMA_STAGE_BACKEND=cuda LLAMA_STAGE_CUDA_ARCHITECTURES=121 LLAMA_STAGE_LINK_MODE=static cargo build --locked -p skippy-cli -p skippy-correctness
