#!/usr/bin/env python3
"""Execute the admitted inference workload on a native JY worker."""

import argparse
import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from lib.ci_artifacts import read_json, write_json
from lib.ci_process import cancel_on_signal, capture, command, start, stop
from lib.ci_smoke import curl
from lib import inference_metal
from lib.inference_checks import ENDPOINT, numerical, offload, performance, score, streaming
from lib.inference_inputs import admit, fixture, input_hashes, record
from lib.inference_ladder import allowed_split_admissions, plan


def authority(backend):
    sha = os.environ["JIANYANG_COMMIT"]
    if capture(["git", "rev-parse", "HEAD"]).strip() != sha:
        raise ValueError("qualification source differs from admitted JY commit")
    if backend == "metal":
        gpu, budget = inference_metal.device_and_budget()
        return sha, gpu, budget
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise ValueError("DGX Spark Linux aarch64 required")
    gpu = capture(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if "GB10" not in gpu:
        raise ValueError("NVIDIA GB10 required")
    return sha, gpu, None


def available_memory(metal_budget):
    if os.geteuid() == 0:
        raise ValueError("native inference must not execute as root")
    if metal_budget is not None:
        return inference_metal.available_memory(capture(["vm_stat"]), metal_budget)
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise ValueError("MemAvailable missing")


def prepare_split(model, coordinate, profile, context, output, worker):
    peers = json.loads(os.environ["JIANYANG_PEERS"])
    if len(peers) != 2 or peers[0]["node"] == peers[1]["node"]:
        raise ValueError("split requires two distinct reserved machines")
    hosts = {peer["role"]: ipaddress.ip_address(peer["host"]) for peer in peers}
    leader, follower = hosts["leader"], hosts["worker"]

    def address(ip, port):
        return f"[{ip}]:{port}" if ip.version == 6 else f"{ip}:{port}"

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "plan"
        args = [
            "target/debug/skippy", "plan-split", "--model-path", str(model), "--model-id",
            coordinate, "--worker", address(leader, 9400), "--worker", address(follower, 9401),
            "--ctx-size", str(context), "--n-gpu-layers", "999", "--output-dir", str(directory),
        ]
        allowed = allowed_split_admissions(profile, "split")
        if "uncertified_override" in allowed:
            args.append("--allow-uncertified-split")
        command(args, timeout=900)
        admissions = output / "split-admissions.json"
        shutil.copyfile(directory / "admissions.json", admissions)
        certification = read_json(admissions).get("certification")
        if certification not in allowed:
            raise ValueError("split plan lacks the profile's required certification admission")
        local = output / "stage.json"
        shutil.copyfile(directory / ("stage-1.json" if worker else "stage-0.json"), local)
    args = ["--config", str(local), "--stage-transport", "binary"]
    if worker:
        args.append("--worker-only")
    else:
        deadline = time.monotonic() + 900
        while True:
            try:
                with socket.create_connection((str(follower), 9401), timeout=1):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("split worker listener did not become ready")
                time.sleep(1)
    return args, "http://" + address(leader, 9912), certification


def wait_for(server, url, seconds, poll, check):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("inference server exited; inspect server.log")
        try:
            if check(curl([url], 5)):
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(poll)
    raise RuntimeError("inference server or split leader timed out")


def acknowledge_worker():
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 9912))
        listener.listen(1)
        listener.settimeout(60)
        try:
            connection, _ = listener.accept()
        except socket.timeout:
            return
        with connection:
            connection.settimeout(2)
            try:
                connection.recv(1024)
            except OSError:
                pass
            body = b"qualification-complete"
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 22\r\nConnection: close\r\n\r\n" + body)


def execute(profile, key, mode, backend):
    if backend == "metal" and mode != "standalone":
        raise ValueError("Metal qualification supports standalone jobs")
    sha, gpu, metal_budget = authority(backend)
    result = plan(profile, sha)
    if not any(row["id"] == f"{key}-{mode}" for row in result["rows"]):
        raise ValueError("configuration not admitted by ladder")
    worker = os.environ.get("JIANYANG_ROLE") == "worker"
    if worker and mode != "split":
        raise ValueError("worker role requires split qualification")
    output = Path(os.environ["JIANYANG_ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    record(output, sha, backend=backend)
    inputs = fixture(key)
    if available_memory(metal_budget) < sum(file["size"] for file in inputs["files"]) + 16 * 1024**3:
        raise ValueError("insufficient free unified memory for weights and runtime reserve")
    model = admit(key, inputs)
    coordinate = f'{inputs["repo"]}@{inputs["revision"]}'
    context = 4096 if profile == "pr" else 65536
    if not worker and profile != "pr":
        numerical(model, coordinate, inputs, output)
    args = ["target/debug/skippy", "--debug", "serve", "--startup-timeout-secs", "300"]
    if backend == "metal":
        args[1:1] = ["--output", "jsonl"]
    if not worker:
        args += ["--speculative-strategy", "disabled", "--generation-concurrency", "1"]
    control, certification = None, None
    if mode == "split":
        split_args, control, certification = prepare_split(model, coordinate, profile, context, output, worker)
        args += split_args
    else:
        args += [
            "--ctx-size", str(context), "--n-gpu-layers", "999", "--bind-addr", "127.0.0.1:9337",
            "--model-path", str(model), "--model-id", coordinate,
        ]
    log = output / "server.log"
    with log.open("wb") as stream:
        server = start(args, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
        try:
            if worker:
                wait_for(server, control, 18000, 5, lambda body: body == "qualification-complete")
                offload(log)
                return
            wait_for(server, ENDPOINT + "/v1/models", 900, 2, lambda _body: True)
            if backend == "metal":
                inference_metal.offload(log, server)
            else:
                offload(log)
            score(coordinate, output, profile)
            if profile == "pr":
                streaming(coordinate, output)
                scope = "GPU startup, deterministic endpoint repeats and bounded streaming smoke"
            else:
                performance(profile, coordinate, context, output, inputs)
                scope = ("native chain parity, state handoff, deterministic endpoint repeat; "
                         "not an independent implementation reference")
            write_json(output / "qualification.json", {
                "source_sha": sha, "model": key, "mode": mode, "profile": profile, "status": "passed",
                "performance_claim": result["performance_claim"], "gpu": gpu, "backend": backend,
                "layers": inputs["layers"],
                "artifact_manifest_sha256": input_hashes()["model_manifest_sha256"],
                "correctness_scope": scope, "split_certification": certification,
            })
            if mode == "split":
                acknowledge_worker()
        finally:
            stop(server)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("model")
    parser.add_argument("mode")
    parser.add_argument("backend", choices=("cuda", "metal"), nargs="?", default="cuda")
    args = parser.parse_args()
    try:
        with cancel_on_signal():
            execute(args.profile, args.model, args.mode, args.backend)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
