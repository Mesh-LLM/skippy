"""Exercise the packaged server and retain device and completion evidence."""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import ci_artifacts as artifacts
from .ci_process import command, environment, start, stop
from .ci_targets import require_host, validate_native_identity
from .ci_vulkan import preflight as vulkan_preflight


def unpack_product(host, runtime, product_input, product):
    require_host(host)
    artifacts.unpack(product_input, product, check_space=True)
    artifacts.verify(product, "product", host["target"], runtime["id"])


def verify_product(host, runtime, product_input, product):
    unpack_product(host, runtime, product_input, product)
    binary = artifacts.binary(product.resolve(), host["os"])
    env = environment()
    for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "DYLD_LIBRARY_PATH",
                "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"):
        env.pop(key, None)
    command([str(binary), "--version"], timeout=60, cwd=product, overrides=env, inherit_env=False)
    return binary


def artifact_smoke(host, runtime, product_input):
    variant = runtime["id"]
    backend = runtime["backend"]
    archive = product_input / f"skippy-{variant}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        product = Path(tmp) / "product"
        verify_product(host, runtime, product_input, product)
        directory = product / "native-runtimes" / variant
        architectures = runtime["gpu_architectures"] if backend == "cuda" else None
        validate_native_identity(directory, host, runtime, architectures)
        verifier = Path(__file__).resolve().parents[1] / "native-inspect.py"
        args = [sys.executable, str(verifier), "verify-runtime-package", str(directory)]
        if backend == "rocm":
            args.append("--resolve-rocm")
        command(args)
    output = product_input / "evidence"
    output.mkdir(parents=True, exist_ok=True)
    artifacts.write_json(output / "artifact-smoke.json", {
        "source": artifacts.source(), "variant": variant, "backend": backend,
        "archive_sha256": artifacts.digest(archive),
    })


def model_digest(model):
    manifest = artifacts.read_json(Path("ci/llama-canary/model-fixtures.json"))
    row = next((row for row in manifest["models"] if row["family"] == "qwen3-dense"), None)
    if row is None:
        raise ValueError("missing Qwen3 dense fixture")
    artifact = row["artifact"]
    identity = artifact["file_integrity"][artifact["files"][0]]
    if model.stat().st_size != identity["size_bytes"]:
        raise ValueError("Qwen3 fixture size or SHA-256 mismatch")
    result = artifacts.digest(model)
    if result != identity["blob_id"]:
        raise ValueError("Qwen3 fixture size or SHA-256 mismatch")
    return result


def curl(args, seconds, *, local=True):
    return command(
        ["curl", "--fail", "--silent", "--show-error",
         *(["--noproxy", "*"] if local else []), "--max-time", str(seconds), *args], timeout=seconds + 2, capture_stdout=True,
    )


def require_device(logs, server, deadline, backend):
    prefixes = {"cuda": ("CUDA",), "metal": ("MTL", "Metal"), "rocm": ("ROCm", "HIP"),
                "vulkan": ("Vulkan",)}[backend]
    readers = [path.open("rb") for path in logs]
    selected = offloaded = False
    try:
        while time.monotonic() < deadline and server.poll() is None:
            for stream in readers:
                while True:
                    offset = stream.tell()
                    line = stream.readline(1024 * 1024)
                    if not line.endswith(b"\n"):
                        stream.seek(offset)
                        break
                    offloaded |= bool(re.search(rb"offloaded [1-9][0-9]*/[^\n]*GPU", line))
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(event, dict) and event.get("type") == "backend_device_selected":
                        data = event.get("data")
                        detail = data.get("detail") if isinstance(data, dict) else None
                        if isinstance(detail, str) and detail.startswith(prefixes):
                            selected = True
                if selected and offloaded:
                    return
            time.sleep(0.2)
    finally:
        for stream in readers:
            stream.close()
    raise RuntimeError("packaged server did not report a GPU device and positive layer offload")


def completion(server, endpoint, deadline):
    readiness_deadline = min(time.monotonic() + 600, deadline)
    while time.monotonic() < readiness_deadline and server.poll() is None:
        try:
            curl([endpoint + "/models"], 2)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(0.2)
    else:
        raise RuntimeError("server readiness deadline or early exit")
    request = {"model": "qwen3-ci", "messages": [{"role": "user", "content": "Say hello."}],
               "max_tokens": 4, "temperature": 0}
    response = json.loads(curl([
        "--header", "content-type: application/json", "--data-binary", json.dumps(request),
        endpoint + "/chat/completions",
    ], 120))
    tokens = response["usage"]["completion_tokens"]
    if (response["object"] != "chat.completion"
            or response["choices"][0]["message"]["role"] != "assistant"
            or type(tokens) is not int or tokens <= 0):
        raise ValueError("invalid or empty OpenAI completion")
    return response


def smoke(host, runtime, product_input):
    model_path = os.environ.get("SKIPPY_CI_MODEL")
    if not model_path:
        raise ValueError("SKIPPY_CI_MODEL must point at the pinned Qwen3 dense fixture")
    model = Path(model_path).resolve(strict=True)
    backend, variant = runtime["backend"], runtime["id"]
    output = product_input / "evidence"
    vulkan_vendor = os.environ.get("SKIPPY_CI_VULKAN_VENDOR")
    vulkan_devices = vulkan_preflight(vulkan_vendor, output) if backend == "vulkan" else None
    gpu = backend != "cpu"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        product = root / "product"
        binary = str(verify_product(host, runtime, product_input, product))
        digest = model_digest(model)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            address = f"127.0.0.1:{listener.getsockname()[1]}"
        argv = [
            binary, "--debug", "--runtime-bundle", str(product / "native-runtimes"),
            "--runtime-selection", backend, "--runtime-cache", str(root / "cache"),
            "serve", "--model-path", str(model), "--model-id", "qwen3-ci",
            "--bind-addr", address, "--ctx-size", "2048", "--n-gpu-layers", "999" if gpu else "0",
        ]
        output.mkdir(parents=True, exist_ok=True)
        logs = [output / "stdout.log", output / "stderr.log"]
        with logs[0].open("wb") as stdout, logs[1].open("wb") as stderr:
            with start(argv, {"HF_HUB_OFFLINE": "1", "HOME": tmp, "USERPROFILE": tmp},
                       stdout=stdout, stderr=stderr) as server:
                try:
                    deadline = time.monotonic() + 850
                    response = completion(server, "http://" + address + "/v1", deadline)
                    if gpu:
                        require_device(logs, server, deadline, backend)
                    if server.poll() is not None:
                        raise RuntimeError("packaged server exited before shutdown")
                finally:
                    stop(server)
                if server.returncode != 0:
                    raise RuntimeError(f"packaged server exited with status {server.returncode}")
        artifacts.write_json(output / "smoke.json", {
            "source": artifacts.source(), "variant": variant, "backend": backend,
            "archive_sha256": artifacts.digest(product_input / f"skippy-{variant}.tar.gz"),
            "gpu_offload_required": gpu, "model_sha256": digest, "response": response,
            **({"vulkan_vendor": vulkan_vendor, "vulkan_devices": vulkan_devices}
               if vulkan_devices is not None else {}),
        })
