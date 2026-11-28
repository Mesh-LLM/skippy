#!/usr/bin/env python3
"""Execute the admitted inference workload on a native JY worker."""

import argparse
import ipaddress
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from contextlib import nullcontext

from lib.ci_artifacts import read_json, write_json
from lib.ci_process import cancel_on_signal, capture, command, start, stop
from lib.ci_smoke import curl
from lib.inference_checks import ENDPOINT, numerical, offload, performance, score, streaming
from lib.inference_inputs import admit, fixture, input_hashes, record
from lib.inference_ladder import plan
from lib.inference_admission import split_admissions_digest
from lib.inference_probe import measure
from lib.inference_peer import PeerControl, port_available


def authority():
    sha = os.environ["JIANYANG_COMMIT"]
    if capture(["git", "rev-parse", "HEAD"]).strip() != sha:
        raise ValueError("qualification source differs from admitted JY commit")
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise ValueError("DGX Spark Linux aarch64 required")
    gpu = capture(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if "GB10" not in gpu:
        raise ValueError("NVIDIA GB10 required")
    return sha, gpu


def available_memory():
    if os.geteuid() == 0:
        raise ValueError("native inference must not execute as root")
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
        command(args, timeout=900)
        admissions = output / "split-admissions.json"
        shutil.copyfile(directory / "admissions.json", admissions)
        split_admissions_digest(read_json(admissions))
        local = output / "stage.json"
        shutil.copyfile(directory / ("stage-1.json" if worker else "stage-0.json"), local)
    args = ["--config", str(local), "--stage-transport", "binary"]
    if worker:
        args.append("--worker-only")
    return args


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


def server_command(worker, phase_profile=False):
    args = ["target/debug/skippy", "--debug", "serve", "--startup-timeout-secs", "300"]
    if phase_profile:
        args += ["--telemetry-level", "debug"]
    if not worker:
        args += ["--speculative-strategy", "disabled", "--generation-concurrency", "1"]
    return args


def execute(profile, key, mode, repetitions=1, probe=None, phase_profile=False):
    sha, gpu = authority()
    result = plan(profile, sha)
    if not any(row["id"] == f"{key}-{mode}" for row in result["rows"]):
        raise ValueError("configuration not admitted by ladder")
    worker = os.environ.get("JIANYANG_ROLE") == "worker"
    if worker and mode != "split":
        raise ValueError("worker role requires split qualification")
    output = Path(os.environ["JIANYANG_ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    record(output, sha)
    inputs = fixture(key)
    if available_memory() < sum(file["size"] for file in inputs["files"]) + 16 * 1024**3:
        raise ValueError("insufficient free unified memory for weights and runtime reserve")
    model = admit(key, inputs)
    coordinate = f'{inputs["repo"]}@{inputs["revision"]}'
    context = 4096 if profile == "pr" else 65536
    args = server_command(worker, phase_profile)
    if mode == "split":
        args += prepare_split(model, coordinate, profile, context, output, worker)
    else:
        args += [
            "--ctx-size", str(context), "--n-gpu-layers", "999", "--bind-addr", "127.0.0.1:9337",
            "--model-path", str(model), "--model-id", coordinate,
        ]
    log = output / "server.log"
    peer_context = PeerControl.from_environment(sha, key, profile) if mode == "split" else nullcontext()
    with log.open("wb") as stream, peer_context as peer:
        # Keep formation identity observable while the leader runs longer
        # nightly/release numerical checks, before starting its serving child.
        if not worker and profile != "pr":
            numerical(model, coordinate, inputs, output)
        if peer is not None:
            peer.await_peer(None if worker else "ready")
            if worker:
                # A ready control response must describe our own new listener.
                port_available(peer.host, 9401)
        if not worker:
            port_available("127.0.0.1", 9337)
        server = start(args, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
        try:
            if worker:
                peer.worker_ready(server, read_json(output / "stage.json"))
                peer.await_peer("complete", server, seconds=18000)
                offload(log)
                peer.acknowledge()
                peer.save(output)
                return
            wait_for(server, ENDPOINT + "/v1/models", 900, 2, lambda _body: True)
            offload(log)
            score(coordinate, output, profile)
            if profile == "pr":
                streaming(coordinate, output)
                if probe is not None:
                    probe(coordinate, output)
                else:
                    for repetition in range(repetitions):
                        probe_output = output if repetition == 0 else output / f"probe-{repetition}"
                        probe_output.mkdir(parents=True, exist_ok=True)
                        measure(coordinate, probe_output, repetition if repetitions > 1 else None)
                scope = "GPU startup, deterministic endpoint repeats and bounded streaming smoke"
            else:
                performance(profile, coordinate, context, output, inputs)
                scope = ("native chain parity, state handoff, deterministic endpoint repeat; "
                         "not an independent implementation reference")
            if peer is not None:
                peer.complete(server)
                peer.save(output)
            write_json(output / "qualification.json", {
                "source_sha": sha, "model": key, "mode": mode, "profile": profile, "status": "passed",
                "performance_claim": result["performance_claim"], "gpu": gpu, "layers": inputs["layers"],
                "artifact_manifest_sha256": input_hashes()["model_manifest_sha256"],
                "correctness_scope": scope,
                "split_peer": peer.proof() if peer is not None else None,
                "split_admissions_sha256": (split_admissions_digest(read_json(output / "split-admissions.json"))
                                             if mode == "split" else None),
            })
        finally:
            stop(server)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile")
    parser.add_argument("model")
    parser.add_argument("mode")
    args = parser.parse_args()
    try:
        with cancel_on_signal():
            execute(args.profile, args.model, args.mode)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
