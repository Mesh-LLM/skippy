"""Build each comparison arm the same way and start its server with the settings it supports."""

import re
from pathlib import Path

from .ci_artifacts import digest
from .ci_process import capture, command

PORT = 9337
REQUIRED = ("--startup-timeout-secs", "--bind-addr", "--model-id", "--ctx-size",
            "--n-gpu-layers", "--model-path")
METAL = {"LLAMA_STAGE_BACKEND": "metal", "LLAMA_STAGE_LINK_MODE": "static"}


def checkout(arm):
    path = Path("..") / arm["path"]
    if capture(["git", "-C", str(path), "rev-parse", "HEAD"]).strip() != arm["sha"]:
        raise ValueError(f'{arm["label"]} checkout differs from its admitted commit')
    command(["git", "-C", str(path), "diff", "--exit-code", "HEAD"], timeout=30)
    return path.resolve()


def build(arm, log):
    """Every arm is a release build: the comparison measures what ships, not a debug harness."""
    path = checkout(arm)
    with log.open("wb") as stream:
        if arm["kind"] == "skippy":
            command(["just", "llama-build"], timeout=7200, overrides=METAL, cwd=path,
                    stdout=stream, stderr=stream)
            command(["cargo", "build", "--locked", "--release", "-p", "skippy-cli"], timeout=7200,
                    overrides=METAL, cwd=path, stdout=stream, stderr=stream)
            binary = path / "target/release/skippy"
        else:
            build_dir = path / "build"
            command(["cmake", "-S", str(path), "-B", str(build_dir), "-G", "Ninja",
                     "-DCMAKE_BUILD_TYPE=Release", "-DGGML_METAL=ON", "-DLLAMA_BUILD_TESTS=OFF",
                     "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_SERVER=ON"], timeout=1800,
                    cwd=path, stdout=stream, stderr=stream)
            command(["cmake", "--build", str(build_dir), "--target", "llama-server", "--parallel"],
                    timeout=7200, cwd=path, stdout=stream, stderr=stream)
            binary = build_dir / "bin/llama-server"
    return {"label": arm["label"], "kind": arm["kind"], "sha": arm["sha"], "binary": str(binary),
            "binary_sha256": digest(binary)}


def capabilities(built):
    """What an arm's server accepts, so an older commit runs with what it had, recorded as such."""
    args = [built["binary"], "serve", "--help"] if built["kind"] == "skippy" else [built["binary"], "--help"]
    text = command(args, timeout=60, capture_stdout=True)
    require_capabilities(built, text)
    return text


def supports(help_text, flag):
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


def require_capabilities(built, help_text):
    missing = [flag for flag in REQUIRED if not supports(help_text, flag)] if built["kind"] == "skippy" else []
    if missing:
        raise ValueError(f'{built["label"]} unsupported: required server options missing: {", ".join(missing)}')


def server_args(built, help_text, model, coordinate, context, server):
    require_capabilities(built, help_text)
    lanes, pool = server["lanes"], server["pool_tokens"]
    if built["kind"] == "llama-server":
        total = pool if pool is not None else context * lanes
        args = [built["binary"], "-m", str(model), "--host", "127.0.0.1", "--port", str(PORT),
                "-ngl", "999", "-np", str(lanes), "-c", str(total), "-kvu", "--alias", coordinate]
        return args, {"lanes": lanes, "total_context": total}, []
    args = [built["binary"], "serve", "--startup-timeout-secs", "300", "--ctx-size", str(context),
            "--n-gpu-layers", "999", "--bind-addr", f"127.0.0.1:{PORT}", "--model-path", str(model),
            "--model-id", coordinate]
    applied, missing = {"ctx_size": context}, []
    wanted = [("--generation-concurrency", lanes), ("--speculative-strategy", "disabled")]
    if pool is not None:
        wanted.append(("--kv-pool-tokens", pool))
    for flag, value in wanted:
        if supports(help_text, flag):
            args += [flag, str(value)]
            applied[flag.removeprefix("--").replace("-", "_")] = value
        else:
            missing.append(flag)
    return args, applied, missing
