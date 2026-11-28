#!/usr/bin/env python3
"""Compare pinned main and candidate on the current exclusive JY allocation."""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from runpy import run_path

from lib.ci_artifacts import digest, read_json, write_json
from lib.ci_process import DEADLINE, cancel_on_signal, capture, command, time_budget
from lib.inference_pairs import PROTOCOL, REPETITIONS, ROUNDS, schedule, summarize
from lib.inference_probe import validate_evidence
from lib.inference_rendezvous import Coordinator, await_condition, exchange, worker_state
from lib import inference_noise as noise

ROOT = Path(__file__).resolve().parents[1]
EXECUTE = run_path(str(ROOT / "scripts/inference-execute.py"))["execute"]


@contextmanager
def revision(directory, sha, output):
    previous, environment = Path.cwd(), os.environ.copy()
    os.chdir(directory)
    os.environ.update(JIANYANG_COMMIT=sha, JIANYANG_REPO_DIR=str(directory),
                      JIANYANG_ARTIFACT_DIR=str(output))
    try:
        yield
    finally:
        os.chdir(previous)
        os.environ.clear()
        os.environ.update(environment)


@contextmanager
def baseline_tree(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("comparison main must be an immutable commit")
    with tempfile.TemporaryDirectory(prefix="skippy-main-", dir=ROOT / "target") as temporary:
        directory = Path(temporary) / "source"
        try:
            command(["git", "cat-file", "-e", sha + "^{commit}"], timeout=30)
        except subprocess.CalledProcessError:
            command(["git", "fetch", "--no-tags", "https://github.com/Mesh-LLM/skippy.git", sha], timeout=180)
        command(["git", "worktree", "add", "--detach", str(directory), sha], timeout=60)
        try:
            yield directory
        finally:
            # Cleanup keeps its own bounded budget even when measurement expired.
            token = DEADLINE.set(None)
            try:
                command(["git", "worktree", "remove", "--force", str(directory)], timeout=120)
            finally:
                DEADLINE.reset(token)


def host(role, node):
    return {"role": role, "node": node, "hostname": platform.node(),
            "kernel": platform.release(), "arch": platform.machine(),
            "gpu": capture(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]).strip()}


def samples(directory, coordinate):
    result = []
    for repetition in range(REPETITIONS):
        path = directory if repetition == 0 else directory / f"probe-{repetition}"
        responses = validate_evidence(read_json(path / "throughput-probe.json"), coordinate)
        for shape, response in enumerate(responses):
            timing = response["timings"]
            result.append({"shape": shape, "prefill_tokens": timing["prompt_n"],
                           "prefill_ms": timing["prompt_ms"], "decode_tokens": timing["predicted_n"],
                           "decode_ms": timing["predicted_ms"]})
    return result


def prepare(main, baseline, candidate, output):
    # Both arms use this harness, one workload and the exact same model bytes.
    for path in ("ci/inference-artifacts.json", "ci/inference-quality.json"):
        if digest(ROOT / path) != digest(main / path):
            raise ValueError("paired comparison model or quality fixtures changed")
    with revision(main, baseline, output):
        command(["just", "--justfile", str(ROOT / "Justfile"),
                 "--working-directory", str(main), "inference-build"], timeout=7200)
    identities = {"main": read_json(main / "target/inference-build.json"),
                  "candidate": read_json(ROOT / "target/inference-build.json")}
    if identities["main"]["stack_commit"] != baseline or identities["candidate"]["stack_commit"] != candidate:
        raise ValueError("paired comparison executable source mismatch")
    policies = [identity["build_inputs"]["policy"] for identity in identities.values()]
    if policies[0] != policies[1] or policies[0]["cargo_profile"] != "release":
        raise ValueError("paired comparison build policies differ or are not release")
    return identities


def run_arm(design, main, baseline, key, mode, output):
    directory = output / f'{design["experiment"]}-{design["round"]}-{design["arm"]}'
    directory.mkdir()
    source = main if design["source_sha"] == baseline else ROOT
    try:
        with revision(source, design["source_sha"], directory):
            if design["experiment"] == "noise":
                kind = design.get("diagnostic", "pilot")
                with noise.diagnostics(directory, kind):
                    EXECUTE("pr", key, mode, probe=noise.probe(design["round"], kind),
                            phase_profile=kind == "profile")
            else:
                EXECUTE("pr", key, mode, REPETITIONS)
    finally:
        # JY uploads top-level files only. Archive each arm, including partial
        # failures, so raw responses and logs survive native workspace cleanup.
        with tarfile.open(output / f"{directory.name}-evidence.tar.gz", "w:gz") as archive:
            archive.add(directory, arcname=directory.name)
    return directory


def worker(origin, designs, main, baseline, key, output, identity):
    worker_state(origin)
    exchange(origin, "/ready", identity)
    try:
        for index, design in enumerate(designs):
            def ready():
                state = exchange(origin, "/state")
                if state["complete"] or state["error"]:
                    raise RuntimeError("paired inference leader stopped")
                return state["index"] == index
            await_condition(ready)
            directory = run_arm(design, main, baseline, key, "split", output)
            if design["experiment"] == "ab" and design["arm"] == "b":
                shutil.copyfile(directory / "split-peer.json", output / "split-peer.json")
            exchange(origin, "/done", {"index": index})
    except BaseException:
        exchange(origin, "/error", {"failed": True})
        raise


def execute(key, mode, baseline, experiment="paired"):
    diagnostic = experiment in {"noise-pilot", "noise-profile"}
    kind = experiment.removeprefix("noise-") if diagnostic else None
    if diagnostic and (f"{key}-{mode}" != noise.registration(kind)["configuration"] or baseline == "legacy"):
        raise ValueError("noise pilot requires its registered configuration and main commit")
    # Old protected controllers can encounter a PR's provisional template before
    # this controller reaches main. Preserve their original smoke protocol.
    if baseline == "legacy":
        EXECUTE("pr", key, mode)
        return
    candidate = os.environ["JIANYANG_COMMIT"]
    if capture(["git", "rev-parse", "HEAD"]).strip() != candidate:
        raise ValueError("paired comparison candidate differs from JY admission")
    output = Path(os.environ["JIANYANG_ARTIFACT_DIR"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    peers = json.loads(os.environ["JIANYANG_PEERS"]) if mode == "split" else []
    role = os.environ.get("JIANYANG_ROLE", "leader")
    node = next((p["node"] for p in peers if p["role"] == role), platform.node())
    with baseline_tree(baseline) as main:
        identities = prepare(main, baseline, candidate, output)
        local = host(role, node)
        designs = noise.schedule(baseline, kind) if diagnostic else schedule(baseline, candidate)
        if role == "worker":
            leader = next(p["host"] for p in peers if p["role"] == "leader")
            origin = f"http://[{leader}]:9913" if ":" in leader else f"http://{leader}:9913"
            worker(origin, designs, main, baseline, key, output, {"host": local, "builds": identities})
            return
        remote = next((p["host"] for p in peers if p["role"] == "worker"), None)
        leader_host = next((p["host"] for p in peers if p["role"] == "leader"), None)
        with Coordinator(remote, host=leader_host) if remote else nullcontext() as coordinator:
            placements = [local]
            builds = [{"role": role, **identities}]
            if coordinator:
                ready = coordinator.wait(lambda s: s["worker"], seconds=7200)
                placements.append(ready["host"])
                builds.append({"role": "worker", **ready["builds"]})
            evidence = {"schema_version": 1, "protocol": PROTOCOL, "source_sha": candidate,
                        "baseline_sha": baseline, "configuration": f"{key}-{mode}", "rounds": ROUNDS,
                        "repetitions": REPETITIONS, "placements": placements, "builds": builds, "runs": []}
            if diagnostic:
                registered = noise.registration(kind)
                evidence.update(protocol=registered["protocol"], rounds=len(registered["arms"]),
                                repetitions=registered["repetitions_per_condition_shape"],
                                diagnostic_only=True, registration=registered)
            try:
                for index, design in enumerate(designs):
                    if coordinator:
                        coordinator.state["index"] = index
                    directory = run_arm(design, main, baseline, key, mode, output)
                    if coordinator:
                        coordinator.wait(lambda s: s["done"] == index)
                    coordinate = read_json(ROOT / "ci/inference-artifacts.json")[key]
                    coordinate = f'{coordinate["repo"]}@{coordinate["revision"]}'
                    evidence["runs"].append({**design, "placements": placements, "quality": "passed",
                                             "samples": (read_json(directory / f"noise-{kind}.json") if diagnostic
                                                         else samples(directory, coordinate))})
                    # Preserve the existing standalone nightly smoke receipts too.
                    if design["experiment"] == "ab" and design["arm"] == "b":
                        for name in ("qualification.json", "quality.json", "throughput-probe.json",
                                     "provenance.json", "split-admissions.json", "split-peer.json"):
                            if (directory / name).is_file():
                                shutil.copyfile(directory / name, output / name)
                if not diagnostic:
                    evidence["summary"] = summarize(evidence, candidate, baseline, f"{key}-{mode}")
                else:
                    evidence["status"] = "diagnostic-complete"
            finally:
                write_json(output / (f"noise-{kind}.json" if diagnostic else "paired-probe.json"), evidence)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("mode", choices=("standalone", "split"))
    parser.add_argument("baseline")
    parser.add_argument("experiment", nargs="?", choices=("paired", "noise-pilot", "noise-profile"), default="paired")
    args = parser.parse_args()
    try:
        with cancel_on_signal(), time_budget(14400):
            execute(args.model, args.mode, args.baseline, args.experiment)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
