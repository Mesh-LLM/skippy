#!/usr/bin/env python3
"""Run bounded direct-API diagnostics against the unchanged pinned native bundle."""
import argparse
import hashlib
import json
import lzma
import shutil
from pathlib import Path
import subprocess
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite

NATIVE = "6704435209b0fa8eabc3aa91e3c0a22f92b89863"
MANIFEST = "c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481"
FIXTURE = "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e"
MODES = ("whole", "whole-logprobs", "split-iteration", "split-prefill")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def prepare(cache, root, report, selection=None):
    selection = selection or {'pin': 'qwen36-35b-a3b', 'fixture': 'qwen3.6-35b-a3b-q4-k-m',
                              'prompt': 'long-2', 'group': 'mixed',
                              'justfile': 'scripts/profile-scheduler-native-probe.just'}
    bundle = cache / "native-runtimes/skippy-native-runtime-darwin-aarch64-metal"
    if digest(bundle / "manifest.json") != MANIFEST:
        raise ValueError("registered runtime manifest changed")
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["build"]["llama_patched_sha"] != NATIVE:
        raise ValueError("native source identity mismatch")
    # Match native-manifest.py: hash resolved file bytes within the bundle.
    for name, expected in manifest["runtime"]["files"].items():
        path = bundle / name
        if not path.resolve().is_relative_to(bundle.resolve()):
            raise ValueError("native bundle file escapes root")
        actual = digest(path)
        if actual != expected:
            raise ValueError("native bundle file mismatch: " + name)
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())[selection['pin']]
    snapshot = (Path("/Users/Shared/jianyang/model-store/huggingface/hub") /
                ("models--" + pin["repo"].replace("/", "--")) / "snapshots" / pin["revision"])
    if len(pin["files"]) != 1:
        raise ValueError("unexpected shard count")
    file = pin["files"][0]
    model = snapshot / file["path"]
    if model.stat().st_size != file["size"] or digest(model) != file["sha256"]:
        raise ValueError("model identity mismatch")
    fixture = cache / ('scheduler-suite-v1-' + selection['fixture'] + '.json')
    if digest(fixture) != FIXTURE:
        raise ValueError("fixture drift")
    prompt = root / "prompt.txt"
    subjects = json.loads(fixture.read_text())[selection['group']]
    subject = subjects[selection['prompt']] if selection['group'] == 'mixed' else next(
        row for row in subjects if row['id'] == selection['prompt'])
    prompt.write_text(subject['text'] if selection['group'] == 'mixed' else subject['messages'][0]['content'])
    headers = root / "headers"
    headers.mkdir()
    source = cache / "llama-source"
    header_manifest = json.loads(Path("native-headers.json").read_text())
    if header_manifest["native"] != NATIVE:
        raise ValueError("header native identity mismatch")
    for name, expected in header_manifest["files"][header_manifest["upstream"]].items():
        original = source / name
        if original.is_symlink() or digest(original) != expected:
            raise ValueError("upstream working header mismatch: " + name)
        destination = headers / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    stage = cache / ("native-headers-" + header_manifest["compressed_sha256"])
    pieces = []
    for chunk in header_manifest["chunks"]:
        path = stage / chunk["name"]
        if digest(path) != chunk["sha256"]:
            raise ValueError("header transport chunk mismatch")
        pieces.append(path.read_bytes())
    packed = b"".join(pieces)
    if hashlib.sha256(packed).hexdigest() != header_manifest["compressed_sha256"]:
        raise ValueError("header transport aggregate mismatch")
    patch = lzma.decompress(packed)
    if hashlib.sha256(patch).hexdigest() != header_manifest["patch_sha256"]:
        raise ValueError("header patch mismatch")
    subprocess.run(["git", "apply", "--check", "-"], cwd=headers, input=patch, check=True)
    subprocess.run(["git", "apply", "-"], cwd=headers, input=patch, check=True)
    expected_files = header_manifest["files"][NATIVE]
    actual_files = {str(p.relative_to(headers)): digest(p) for p in headers.rglob("*") if p.is_file()}
    if actual_files != expected_files:
        raise ValueError("reconstructed pinned headers mismatch")
    host.save(root / "header-provenance.json", header_manifest)
    header_archive = root / "headers.tar"
    with tarfile.open(header_archive, "w") as tar:
        for name in sorted(expected_files):
            tar.add(headers / name, arcname=name)
    binary = root / "native-probe"
    env = host.clean_env(root)
    with (root / "build.log").open("w") as log:
        subprocess.run(["just", "--justfile", selection['justfile'],
                        "build", str(headers), str(bundle), str(binary)], env=env,
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
    identity = json.loads(subprocess.check_output([str(binary), "--identity"], env=env))
    report.update(native_manifest=manifest, native_manifest_sha256=MANIFEST,
                  header_manifest_sha256=digest(Path("native-headers.json")),
                  model=pin, fixture_sha256=FIXTURE,
                  prompt_sha256=digest(prompt), header_archive_sha256=digest(header_archive),
                  binary_sha256=digest(binary), abi=identity, native_source=NATIVE)
    return binary, model, prompt


def run_cell(binary, model, prompt, root, repeat, mode):
    name = f"r{repeat}-{mode}"
    directory = root / name
    env = host.clean_env(root)
    suite.nominal(env, soak=60)
    launch = [str(binary), str(model), str(prompt), mode, str(directory)]
    log_path = root / (name + ".log")
    with log_path.open("w") as log:
        child = subprocess.Popen(launch, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
        try:
            deadline = time.monotonic() + 300
            while not (directory / "ready.json").exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    raise ValueError("native readiness failed: " + name)
                time.sleep(0.5)
            ready = json.loads((directory / "ready.json").read_text())
            token_hash = hashlib.sha256(json.dumps(ready["prompt_ids"]).encode()).hexdigest()
            if token_hash != "81f8383f806958a17b223be4ada284a1d95932822e483ca02b65b2dc5bf7a9c1":
                raise ValueError("native prompt token identity changed")
            resolved = suite.resolved(log_path, "skippy-base", 16)
            if resolved["gpu_layers"] != 41 or resolved["n_seq_max"] != ["32"] or resolved["n_rs_seq"] != ["0"]:
                raise ValueError("native state/offload differs")
            suite.nominal(env, child.pid, 30)
            host.telemetry(directory / "before.json", env)
            host.save(directory / "launch.json", {"argv": launch, "environment_names": sorted(env)})
            start = time.time_ns()
            child.stdin.write("GO\n")
            child.stdin.flush()
            deadline = time.monotonic() + 300
            while child.poll() is None:
                if host.thermal_pressure() or host.competing_work(env, child.pid) or time.monotonic() > deadline:
                    raise host.HostUnavailable("native probe thermal/contention/deadline gate")
                time.sleep(0.5)
            end = time.time_ns()
            if child.returncode != 0:
                raise ValueError("native probe failed: " + name)
            host.telemetry(directory / "after.json", env)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            child.stdin.close()
    record = json.loads((directory / "probe.json").read_text())
    if not record.get("complete") or len(record["steps"]) != 24:
        raise ValueError("incomplete native replay")
    raw = {step["raw_file"]: digest(directory / step["raw_file"]) for step in record["steps"]}
    host.save(directory / "raw-hashes.json", raw)
    return {"directory": name, "mode": mode, "repeat": repeat, "resolved": resolved,
            "start_unix_ns": start, "end_unix_ns": end,
            "report_sha256": digest(directory / "probe.json"), "raw_sha256": raw}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-native-probe-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "cells": [],
              "scope": "Direct API diagnostic; unchanged native library, no Rust/server or performance qualification. Raw post-call logits remain private."}
    try:
        binary, model, prompt = prepare(args.cache, root, report)
        for repeat in range(2):
            for mode in (MODES if repeat == 0 else tuple(reversed(MODES))):
                print("NATIVE_PROBE", repeat, mode, flush=True)
                report["cells"].append(run_cell(binary, model, prompt, root, repeat, mode))
                host.save(args.output / "native-probe.json", report)
        report["complete"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "native-probe.json", report)
        with tarfile.open(args.output / "native-probe-evidence.tar.gz", "w:gz") as tar:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    tar.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
