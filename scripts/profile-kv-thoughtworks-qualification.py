#!/usr/bin/env python3
"""Run the registered high-load Thoughtworks cells through the native owner."""

import argparse
import hashlib
import json
from pathlib import Path
import resource
import signal
import subprocess
import tempfile
import time

import lib.profile_kv_host as host


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def tree_digest(root):
    digestor = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        name = path.relative_to(root).as_posix().encode()
        digestor.update(len(name).to_bytes(8, "big"))
        digestor.update(name)
        digestor.update(bytes.fromhex(digest(path)))
    return digestor.hexdigest()


def backend(binary, runtime, cwd, env):
    version = subprocess.run([str(binary), "--version"], cwd=cwd, env=env,
                             capture_output=True, check=True, timeout=10)
    return {"executable": {"path": str(binary), "sha256": digest(binary)},
            "version_sha256": hashlib.sha256((version.stdout + version.stderr).strip()).hexdigest(),
            "runtime": {"path": str(runtime), "sha256": tree_digest(runtime)} if runtime else None,
            "cwd": str(cwd), "tokenizer": None, "hf_config": None, "comparison_model": None,
            "match_kv_capacity": False}


def descendants(pid, env):
    process_rows = host.command(["ps", "-axo", "pid=,ppid="], env, capture_output=True, text=True)
    children = {}
    for line in process_rows.stdout.splitlines():
        child, parent = map(int, line.split())
        children.setdefault(parent, []).append(child)
    found, pending = set(), [pid]
    while pending:
        child = pending.pop()
        if child not in found:
            found.add(child)
            pending.extend(children.get(child, []))
    return found


def check_phase_gates(gates, pressure, ready_since, evidence, warm_pause, measure_soak):
    for marker in sorted(gates.glob("*.ready")):
        release = marker.with_suffix(".go")
        if release.exists():
            continue
        now = time.monotonic()
        phase = ready_since.setdefault(marker.name, {"since": now, "nominal_since": now})
        if pressure:
            phase["nominal_since"] = now
            continue
        nominal_pause = measure_soak if marker.stem.startswith("measure-") else warm_pause
        if now - phase["nominal_since"] < nominal_pause:
            continue
        release.touch(exist_ok=False)
        evidence.append({"phase": marker.stem, "gate_seconds": round(now - phase["since"], 3)})


def run_native(command, directory, env, gates, phase_evidence, warm_pause=0, measure_soak=12):
    host.require_idle(env)
    env = env.copy()
    if gates is not None:
        gates.mkdir()
        env["SKIPPY_THOUGHTWORKS_GATES"] = str(gates)
    ready_since = {}
    with directory.with_suffix(".log").open("wb") as stream:
        child = subprocess.Popen(command, env=env, stdout=stream, stderr=stream, start_new_session=True)
        try:
            while child.poll() is None:
                time.sleep(0.75)
                running = host.competing_work(env)
                own = descendants(child.pid, env)
                active = {r["pid"] for r in host.competing_work(env)}
                competing = [r for r in running if r["pid"] in active and r["pid"] not in own]
                thermal = host.thermal_pressure()
                measuring = gates is None or (gates / "measure-0.go").is_file()
                if competing or thermal > (0 if measuring else 1):
                    raise host.HostUnavailable(f"native cell guard: competitors={competing}, thermal={thermal}")
                if gates is not None:
                    check_phase_gates(gates, thermal, ready_since, phase_evidence,
                                      warm_pause, measure_soak)
        except host.HostUnavailable:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                pass
            raise
        finally:
            host.stop(child)
    return child.returncode


def extract(directory):
    source = directory / "worker"
    marker = directory / "complete.json"
    summary = source / "worker-summary.json"
    requests = source / "requests.jsonl"
    record = {"completed": marker.is_file()}
    if summary.is_file():
        record["worker"] = json.loads(summary.read_text())
        if marker.is_file() and json.loads(marker.read_text())["worker_summary_sha256"] != digest(summary):
            raise ValueError("unbound native worker summary")
    if requests.is_file():
        if marker.is_file() and record["worker"]["requests_sha256"] != digest(requests):
            raise ValueError("unbound native request rows")
        with requests.open() as stream:
            rows = [json.loads(line) for line in stream]
        record["warmup_requests"] = sum(row["phase"] != "measured" for row in rows)
        record["requests"] = [row for row in rows if row["phase"] == "measured"]
    return record


def run(args, root):
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft != resource.RLIM_INFINITY and soft < args.min_open_files:
        if hard != resource.RLIM_INFINITY and hard < args.min_open_files:
            raise ValueError(f"open-file hard limit {hard} is below {args.min_open_files}")
        resource.setrlimit(resource.RLIMIT_NOFILE, (args.min_open_files, hard))
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    print(f"Open-file limits per process: soft={soft}, hard={hard}", flush=True)
    staged = args.cache / f"thoughtworks-{args.manifest_sha256}"
    build = json.loads((staged / "thoughtworks-build.json").read_text())
    for name, sha in build["files"].items():
        if digest(staged / name) != sha:
            raise ValueError(f"retained input changed: {name}")
    if build["files"]["manifest.json"] != args.manifest_sha256:
        raise ValueError("native workload manifest drift")
    model = Path((staged / "model-path.txt").read_text().strip()).resolve(strict=True)
    model_sha = digest(model)
    if model_sha != build["model_sha256"]:
        raise ValueError("admitted model changed")
    identity = json.loads((args.cache / "build-identity.json").read_text())
    runtime = next((args.cache / "native-runtimes").glob("*/manifest.json")).parent
    if digest(runtime / "manifest.json") != identity["files"][str((runtime / "manifest.json").relative_to(args.cache))]:
        raise ValueError("shared native runtime manifest changed")
    candidate_root = args.cache / f"scheduler-fix-{args.patch}"
    candidate = json.loads((candidate_root / "scheduler-fix-build.json").read_text())
    if candidate["patch_sha256"] != args.patch or candidate["source"] != identity["source_sha"]:
        raise ValueError("candidate source changed")
    env = host.clean_env(root)
    env["SKIPPY_NATIVE_RUNTIME_BUNDLE_DIR"] = str(runtime)
    env["SKIPPY_KV_CACHE_DISK"] = "off"
    backends = {}
    for label, binary, expected in (("baseline", args.cache / "skippy", identity["files"]["skippy"]),
                                    ("patched", candidate_root / "skippy", candidate["binary_sha256"])):
        backends[label] = backend(binary, runtime, root, env)
        if backends[label]["executable"]["sha256"] != expected:
            raise ValueError(f"{label} binary changed")
    known = json.loads((staged / "plan.json").read_text())
    plan = {cell["concurrency"]: cell for cell in known["cells"] if cell["arm"] == "mesh"}
    config = staged / "competitive-config.json"
    report = {"build": build, "backends": backends, "cells": [],
              "open_file_limits": {"soft": soft, "hard": hard},
              "warm_pause_seconds": args.warm_pause, "measure_soak_seconds": args.measure_soak}
    destination = args.output / "thoughtworks-qualification.json"
    host.save(destination, report)
    time.sleep(args.initial_cooldown)
    for concurrency in args.concurrency:
        cap = {64: 135, 128: 240, 256: 410}[concurrency]
        if args.phase_gates:
            cap = {64: 280, 128: 620, 256: 930}[concurrency]
        for label in (("patched", "baseline") if concurrency == 128 else ("baseline", "patched")):
            if report["cells"]:
                time.sleep(args.cell_cooldown)
            print("THOUGHTWORKS", concurrency, label, flush=True)
            tag = f"c{concurrency}-{label}"
            cell = root / tag
            input_file = root / f"{tag}.json"
            phase_evidence = []
            gates = root / f"{tag}-gates" if args.phase_gates else None
            host.save(input_file, {"config": str(config), "config_sha256": digest(config),
                                  "cell": plan[concurrency],
                                  "model": {"path": str(model), "sha256": model_sha},
                                  "backend": backends[label], "manifest": str(staged / "manifest.json"),
                                  "benchy": None, "output": str(cell), "timeout_seconds": cap,
                                  "request_timeout_seconds": min(cap - 10, 240)})
            try:
                code = run_native([str(staged / "xtask"), "automation", "replay-matrix",
                                  "competitive-run-cell", "--input", str(input_file)],
                                  cell, env, gates, phase_evidence, args.warm_pause, args.measure_soak)
            except host.HostUnavailable:
                report["cells"].append({
                    **extract(cell), "label": label, "concurrency": concurrency,
                    "completed": False, "exit_code": None, "phase_gates": phase_evidence,
                })
                host.save(destination, report)
                raise
            observed = extract(cell)
            if args.phase_gates and observed["completed"]:
                expected_gates = 2 * (concurrency // 32) + 1
                if len(phase_evidence) != expected_gates:
                    raise ValueError(f"{tag} completed without the registered warmup checkpoints")
            report["cells"].append({"label": label, "concurrency": concurrency,
                                    "exit_code": code, "phase_gates": phase_evidence, **observed})
            host.save(destination, report)
            if code or not observed["completed"]:
                error = cell.with_suffix(".log").read_text(errors="replace").splitlines()[-3:]
                raise RuntimeError(f"{tag} failed: {error}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--patch", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=(64, 128, 256), action="append", required=True)
    parser.add_argument("--initial-cooldown", type=int, default=0)
    parser.add_argument("--cell-cooldown", type=int, default=8)
    parser.add_argument("--phase-gates", action="store_true")
    parser.add_argument("--warm-pause", type=int, default=0)
    parser.add_argument("--measure-soak", type=int, default=12)
    parser.add_argument("--min-open-files", type=int, default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="thoughtworks-", dir=Path.cwd()) as directory:
        run(args, Path(directory))


if __name__ == "__main__":
    main()
