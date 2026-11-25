"""Build each comparison arm the same way and start its server with the settings it supports."""

import re
from pathlib import Path

from .ci_artifacts import digest
from .ci_process import capture, command
from .performance_backends import BACKENDS
from .performance_plan import ROOT

PORT = 9337
REQUIRED = ("--startup-timeout-secs", "--bind-addr", "--model-id", "--ctx-size",
            "--n-gpu-layers", "--model-path")
# llama.cpp files its library's INFO lines under trace, and the offload line the
# startup check reads is one of them.
REFERENCE_VERBOSITY = 4
TOOLKIT_ENV = ("PATH", "CUDACXX", "NVCC", "CUDAToolkit_ROOT", "CUDA_HOME", "CUDA_PATH",
               "CUDA_LIBRARY_PATH", "LIBRARY_PATH", "LD_LIBRARY_PATH")


def checkout(arm):
    path = Path("..") / arm["path"]
    if capture(["git", "-C", str(path), "rev-parse", "HEAD"]).strip() != arm["sha"]:
        raise ValueError(f'{arm["label"]} checkout differs from its admitted commit')
    command(["git", "-C", str(path), "diff", "--exit-code", "HEAD"], timeout=30)
    return path.resolve()


def toolkit_overrides(script):
    """The toolkit environment Skippy's own build exports, so the reference arm configures
    the same native toolkit rather than whatever an unpackaged CMake happens to find."""
    text = capture(["bash", "-c", f'source "{script}" && configure_cuda_toolkit_env && env -0'])
    found = {key: value for item in text.split("\0") if "=" in item
             for key, value in (item.split("=", 1),) if key in TOOLKIT_ENV}
    if not found:
        raise ValueError("the native toolkit environment did not resolve; the reference build cannot find it")
    return found


def resolve_setting(entry, key):
    """A table value, or a callable the host resolves at build time, such as a GPU architecture."""
    value = entry[key]
    return value() if callable(value) else value


def build(arm, log, backend="metal"):
    """Every arm is a release build: the comparison measures what ships, not a debug harness."""
    entry = BACKENDS[backend]
    skippy_env = resolve_setting(entry, "skippy_env")
    path = checkout(arm)
    with log.open("wb") as stream:
        if arm["kind"] == "skippy":
            command(["just", "llama-build"], timeout=7200, overrides=skippy_env, cwd=path,
                    stdout=stream, stderr=stream)
            command(["cargo", "build", "--locked", "--release", "-p", "skippy-cli"], timeout=7200,
                    overrides=skippy_env, cwd=path, stdout=stream, stderr=stream)
            binary = path / "target/release/skippy"
        else:
            overrides = None
            if entry["toolkit_script"]:
                overrides = toolkit_overrides(ROOT / entry["toolkit_script"])
            build_dir = path / "build"
            command(["cmake", "-S", str(path), "-B", str(build_dir), "-G", "Ninja",
                     "-DCMAKE_BUILD_TYPE=Release", *resolve_setting(entry, "reference_cmake"),
                     "-DLLAMA_BUILD_TESTS=OFF",
                     "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_SERVER=ON"], timeout=1800,
                    cwd=path, overrides=overrides, stdout=stream, stderr=stream)
            command(["cmake", "--build", str(build_dir), "--target", "llama-server", "--parallel"],
                    timeout=7200, cwd=path, overrides=overrides, stdout=stream, stderr=stream)
            binary = build_dir / "bin/llama-server"
    built = {"label": arm["label"], "kind": arm["kind"], "sha": arm["sha"], "binary": str(binary),
             "binary_sha256": digest(binary)}
    architecture = skippy_env.get("LLAMA_STAGE_CUDA_ARCHITECTURES")
    if architecture:
        built["gpu_architecture"] = architecture
    return built


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
                "-ngl", "999", "-np", str(lanes), "-c", str(total), "-kvu",
                "-lv", str(REFERENCE_VERBOSITY), "--alias", coordinate]
        return args, {"lanes": lanes, "total_context": total, "log_verbosity": REFERENCE_VERBOSITY}, []
    args = [built["binary"], "serve", "--startup-timeout-secs", "300", "--ctx-size", str(context),
            "--n-gpu-layers", "999", "--bind-addr", f"127.0.0.1:{PORT}", "--model-path", str(model),
            "--model-id", coordinate]
    applied, missing = {"ctx_size": context}, []
    wanted = [("--generation-concurrency", lanes), ("--speculative-strategy", "disabled")]
    if pool is not None:
        wanted.append(("--kv-pool-tokens", pool))
    # llama.cpp's offloaded-layers line is the only GPU evidence older skippy
    # forwards, and only at debug verbosity; a None value passes a bare flag.
    wanted.append(("--debug", None))
    for flag, value in wanted:
        if not supports(help_text, flag):
            missing.append(flag)
            continue
        args += [flag] if value is None else [flag, str(value)]
        applied[flag.removeprefix("--").replace("-", "_")] = True if value is None else value
    return args, applied, missing


def may_reject(built, server, point):
    """Whether failed requests at this point are a result rather than a fault.

    `llama-server` frees KV from idle slots only, so when the requests in flight need more
    than its pool holds it fails them. Only there is a failure its answer: a Skippy arm is
    held to every point, and so is `llama-server` inside its pool.
    """
    pool = server["pool_tokens"]
    in_flight = point["concurrency"] * (point["depth"] + point["input_tokens"] + point["output_tokens"])
    return built["kind"] == "llama-server" and pool is not None and in_flight > pool
