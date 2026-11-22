"""Compare the public standalone entry point with sequential native server execution."""

import socket
import subprocess
import time
from urllib.request import urlopen

from .ci_artifacts import write_json
from .ci_process import start, stop
from .workload_http import request


def completion(args, environment, log):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}/v1"
    with log.open("wb") as stream:
        child = start([*args, "--port", str(port)] if "llama-server" in args[0]
                      else [*args, "--bind-addr", f"127.0.0.1:{port}"],
                      overrides=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
        try:
            deadline = time.monotonic() + 900
            while child.poll() is None and time.monotonic() < deadline:
                try:
                    with urlopen(endpoint + "/models", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("standalone server did not become ready")
            result = request(endpoint, "/completions", {
                "model": "upgrade-fixture", "prompt": "The capital of France is",
                "max_tokens": 8, "temperature": 0, "seed": 42,
            })
            text = result["choices"][0]["text"]
            if not text or result["usage"]["completion_tokens"] <= 0:
                raise ValueError("standalone completion is empty")
            return result
        finally:
            stop(child)


def standalone(model, target, bins, out):
    out.mkdir(parents=True, exist_ok=True)
    common = ["--ctx-size", "128", "--n-gpu-layers", "0"]
    # Close the reference before loading Skippy, so large fixtures need one resident copy.
    reference = completion([str(bins.native / "bin/llama-server"), "--model", str(target),
                            "--alias", "upgrade-fixture", *common], bins.environment(), out / "reference.log")
    candidate = completion([str(bins.bin / "skippy"), "serve", "--model-path", str(target),
                            "--model-id", "upgrade-fixture", "--generation-concurrency", "1",
                            "--speculative-strategy", "disabled", *common], bins.environment(), out / "candidate.log")
    write_json(out / "standalone.json", {"reference": reference, "candidate": candidate})
    if candidate["choices"][0]["text"] != reference["choices"][0]["text"]:
        raise ValueError(f'{model["family"]}: standalone output differs from native reference')
