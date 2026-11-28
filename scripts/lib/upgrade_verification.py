"""Run the existing CPU correctness gates before freezing an upgrade candidate."""

from .ci_process import command
from .upgrade_abi import verify_abi


def verify_offline(checkout):
    verify_abi(checkout)
    native = checkout / "target/llama-regeneration/native"
    env = {
        "LLAMA_WORKDIR": str(checkout / ".deps/llama.cpp"),
        "LLAMA_PIN_FILE": str(checkout / "llama_cpp/upstream.txt"),
        "LLAMA_PATCH_DIR": str(checkout / "llama_cpp/patches"),
        "LLAMA_STAGE_BUILD_DIR": str(native), "LLAMA_BUILD_DIR": str(native),
        "LLAMA_STAGE_BACKEND": "cpu", "LLAMA_STAGE_LINK_MODE": "static",
        "SKIPPY_REWRITER_SOURCE_ROOT": str(checkout / ".deps/llama.cpp"),
    }
    for recipe in ("lint", "test", "llama-check"):
        command(["just", "--justfile", str(checkout / "Justfile"), recipe],
                cwd=checkout, overrides=env, timeout=43200)
