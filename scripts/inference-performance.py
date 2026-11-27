#!/usr/bin/env python3
"""Compare Skippy commits, and llama.cpp's own server, on one host of any backend in the table."""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request

from lib.ci_artifacts import digest, read_json, write_json
from lib.ci_process import cancel_on_signal, capture, command, start, stop
from lib.ci_smoke import curl
from lib.inference_checks import ENDPOINT, offload
from lib.inference_inputs import admit as admit_model
from lib.inference_inputs import fixture
from lib.jy_qualification import ORIGIN, Pilot
from lib.performance_arms import build, capabilities, may_reject, server_args, server_flags, supports
from lib.performance_backends import BACKENDS
from lib.performance_load import run_point, tokenizer
from lib.performance_plan import (SCENARIO, arms, branch_name, job_spec, models, nodes, pull_request_head,
                                  pull_request_number, scenario)
from lib.performance_report import markdown, rows, summarize

REPO = "Mesh-LLM/skippy"
LLAMA_REPO = "ggml-org/llama.cpp"
WORK = Path("target/inference-performance")
# One file per arm, as each arm builds in a job step of its own.
BUILT = WORK / "built"
# What the steps after `start` and `model` read back: each arm's options, each admitted model.
HELPS = WORK / "helps.json"
MODELS = WORK / "models"
RECORD = "performance.json"
# Outside the artifact directory: artifact names are flat, so the traces travel as one archive.
TRACES = WORK / "traces"
TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


def github(path):
    return json.loads(command(["gh", "api", path], overrides={"GH_TOKEN": os.environ["GH_TOKEN"]},
                              capture_stdout=True))


def resolve_main(ref):
    """A commit already on protected main."""
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", ref):
        raise ValueError(f"invalid ref: {ref!r}")
    sha = github(f"repos/{REPO}/commits/{quote(ref, safe='')}")["sha"]
    if github(f"repos/{REPO}/compare/{sha}...main")["status"] not in {"ahead", "identical"}:
        raise ValueError(f"{ref} ({sha[:12]}) is not on main")
    return sha


def resolve(ref):
    """A commit on main, with `pr:N` an open pull request's head, or with `branch:NAME` a branch's head, as
    (commit, N or None, NAME or None). Only collaborators with write access can push a branch here, which is
    the same bar `pr:N` sets for its author."""
    branch = branch_name(ref)
    if branch is not None:
        return github(f"repos/{REPO}/branches/{quote(branch, safe='')}")["commit"]["sha"], None, branch
    number = pull_request_number(ref)
    if number is None:
        return resolve_main(ref), None, None
    pull = github(f"repos/{REPO}/pulls/{number}")
    sha = pull_request_head(pull, REPO)
    login = pull["user"]["login"]
    permission = github(f"repos/{REPO}/collaborators/{quote(login, safe='')}/permission")["permission"]
    if permission not in {"admin", "maintain", "write"}:
        raise ValueError("native performance PR author must be a repository collaborator with write access")
    return sha, number, None


def off_main(pull_requests, branches=None):
    """A line for each arm taken from a pull request or a branch, so no report reads as main's."""
    return "".join([*(f"The {label} is the head of #{number}, not a commit on main.\n\n"
                      for label, number in pull_requests.items() if number is not None),
                    *(f"The {label} is the head of branch {name}, not a commit on main.\n\n"
                      for label, name in (branches or {}).items() if name is not None)])


def reference_pin(sha):
    content = github(f"repos/{REPO}/contents/llama_cpp/upstream.txt?ref={sha}")
    return base64.b64decode(content["content"]).decode("utf-8").strip()


def committed_at(arm_list):
    """When each arm's commit was made, so stored rows can be graphed in commit order."""
    return {arm["sha"]: github(f'repos/{REPO if arm["kind"] == "skippy" else LLAMA_REPO}/commits/'
                               f'{arm["sha"]}')["commit"]["committer"]["date"] for arm in arm_list}


def admit(evidence):
    harness = capture(["git", "rev-parse", "HEAD"]).strip()
    candidate, candidate_pull, candidate_branch = resolve(os.environ["CANDIDATE"])
    baseline, baseline_pull, baseline_branch = resolve(os.environ["BASELINE"])
    pull_requests = {"candidate": candidate_pull, "baseline": baseline_pull}
    branches = {"candidate": candidate_branch, "baseline": baseline_branch}
    with_reference = os.environ.get("REFERENCE", "true") == "true"
    trace = os.environ.get("TRACE", "false") == "true"
    backend = os.environ.get("BACKEND") or "metal"
    arm_list = (arms(candidate, baseline, reference_pin(candidate), reference_pin(baseline)) if with_reference
                else arms(candidate, baseline, None))
    keys = models([key.strip() for key in os.environ["MODELS"].split(",") if key.strip()])
    pins = nodes([node.strip() for node in os.environ.get("NODES", "").split(",") if node.strip()])
    rounds = int(os.environ.get("ROUNDS") or 3)
    servers = [server.strip() for server in os.environ.get("SERVERS", "").split(",") if server.strip()] or None
    jobs = [{"label": node or "any",
             "spec": job_spec(harness, arm_list, keys, rounds, node, node or "any",
                              trace=trace, backend=backend, servers=servers)}
            for node in (pins or [None])]
    evidence.mkdir(parents=True, exist_ok=True)
    write_json(evidence / "plan.json", {"harness": harness, "backend": backend, "arms": arm_list,
                                        "models": keys,
                                        "rounds": rounds, "trace": trace, "servers": servers,
                                        "pull_requests": pull_requests,
                                        "branches": branches,
                                        "committed_at": committed_at(arm_list), "jobs": jobs})
    for arm in arm_list:
        print(f'{arm["label"]}: {arm["kind"]} {arm["sha"]}')
    print(off_main(pull_requests, branches), end="")
    print(f"backend {backend}: {len(jobs)} job(s), {len(keys)} model(s), {rounds} round(s); nothing has run yet")


def resource_id(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", identifier):
        raise ValueError("invalid JY resource id")
    return identifier


def cancel(pilot, ledger):
    failures = []
    for identifier in read_json(ledger):
        try:
            job = pilot.api("GET", f"/v1/jobs/{resource_id(identifier)}")
            if job.get("status") not in TERMINAL:
                pilot.api("POST", f"/v1/jobs/{identifier}/cancel")
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            failures.append(f"{identifier}: {error}")
    if failures:
        raise RuntimeError("could not cancel: " + "; ".join(failures))


def wait(pilot, identifier, evidence, deadline):
    while time.monotonic() < deadline:
        job = pilot.api("GET", f"/v1/jobs/{identifier}")
        write_json(evidence / f"{identifier}-job.json", job)
        if job["status"] in TERMINAL:
            return job
        time.sleep(30)
    raise RuntimeError(f"{identifier} did not finish before the controller deadline")


def download(pilot, run_id, name, path):
    """An artifact that is not JSON, streamed to `path`."""
    # Artifacts are namespaced by kind: raw/, log/ and trace/ files are served
    # under their kind segment, and the raw route alone 404s the others.
    last = None
    for kind in ("raw", "log", "trace"):
        request = Request(f"{ORIGIN}/v1/artifacts/{run_id}/{kind}/{quote(name, safe='')}",
                          headers={"Authorization": "Bearer " + pilot.auth(),
                                   "User-Agent": "skippy-inference-ci/1.0"})
        try:
            with pilot.opener.open(request, timeout=900) as response:
                with path.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
                return
        except OSError as error:
            last = error
    raise last


def collect(pilot, job, plan, label, evidence):
    run = (job.get("runs") or [{}])[-1]
    run_id = resource_id(run.get("id"))
    result = pilot.api("GET", f"/v1/artifacts/{run_id}/raw/performance.json")
    if result.get("harness") != plan["harness"] or [
        (arm["label"], arm["sha"]) for arm in result.get("arms", [])
    ] != [(arm["label"], arm["sha"]) for arm in plan["arms"]]:
        raise ValueError(f"{label}: performance evidence does not match the admitted plan")
    write_json(evidence / f"{label}-performance.json", result)
    (evidence / f"{label}-rows.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n"
                for row in rows(result, scenario(plan.get("servers")), plan["committed_at"], job["id"],
                                plan["pull_requests"], plan.get("branches"))),
        encoding="utf-8")
    if plan["trace"]:
        download(pilot, run_id, "traces.tar.gz", evidence / f"{label}-traces.tar.gz")
    host = f'{result["host"].get("gpu") or result["host"].get("chip") or "unknown device"}' \
           f' ({result["host"].get("node") or "unknown node"})'
    return markdown(f"Inference performance: {label}", host, summarize(result["samples"]), result)


def dispatch(evidence):
    pilot, plan = Pilot(), read_json(evidence / "plan.json")
    ledger, identifiers = evidence / "jobs.json", []
    write_json(ledger, identifiers)
    run = f'{os.environ.get("GITHUB_RUN_ID", "local")}-{os.environ.get("GITHUB_RUN_ATTEMPT", "1")}'
    deadline = time.monotonic() + 20700
    try:
        for job in plan["jobs"]:
            created = pilot.api("POST", "/v1/jobs", {
                "spec": job["spec"], "idempotency_key": f'inference-performance-{run}-{job["label"]}'})
            identifiers.append(resource_id(created["id"]))
            write_json(ledger, identifiers)
            print(f'{job["label"]}: {created["id"]} {created.get("status")}', flush=True)
        finished = [wait(pilot, identifier, evidence, deadline) for identifier in identifiers]
    except (Exception, KeyboardInterrupt):
        try:
            cancel(pilot, ledger)
        except (OSError, ValueError, KeyError, RuntimeError) as cleanup:
            print(f"Cancellation incomplete: {cleanup}", file=sys.stderr)
        raise
    reports, failures = [], []
    for job, spec in zip(finished, plan["jobs"]):
        if job["status"] != "succeeded":
            failures.append(f'{spec["label"]}: {job["status"]}')
            continue
        reports.append(collect(pilot, job, plan, spec["label"], evidence))
    report = off_main(plan["pull_requests"], plan.get("branches")) + "\n".join(reports)
    (evidence / "summary.md").write_text(report, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(report)
    print(report)
    if failures:
        raise RuntimeError("performance jobs did not succeed: " + "; ".join(failures))


def native_plan():
    plan = json.loads(os.environ["SKIPPY_PERFORMANCE_PLAN"])
    if capture(["git", "rev-parse", "HEAD"]).strip() != plan["harness"]:
        raise ValueError("harness checkout differs from the admitted harness commit")
    labels = {arm["label"]: arm["sha"] for arm in plan["arms"]}
    expected = arms(labels["candidate"], labels["baseline"], labels.get("reference"),
                    labels.get("baseline-reference"))
    if plan.get("backend") not in BACKENDS:
        raise ValueError(f'performance plan backend {plan.get("backend")!r} has no entry in the table')
    if (plan["arms"] != expected or plan["models"] != models(plan["models"])
            or type(plan.get("trace")) is not bool):
        raise ValueError("performance plan arms, models or trace flag are malformed")
    return plan


def toolcheck():
    """The backend's tool check, before any build spends an hour on a host without its tools."""
    check = BACKENDS[native_plan()["backend"]]["tool_check"]
    if callable(check):
        check()
    else:
        command(check, timeout=120, capture_stdout=True)


def build_arm(label):
    plan = native_plan()
    arm = next((arm for arm in plan["arms"] if arm["label"] == label), None)
    if arm is None:
        raise ValueError(f"{label} is not an arm of the admitted plan")
    BUILT.mkdir(parents=True, exist_ok=True)
    log = WORK / f"build-{label}.log"
    try:
        write_json(BUILT / f"{label}.json", build(arm, log, plan["backend"]))
    finally:
        # Copied at once, failed or not: a failed build ends the job.
        if log.is_file():
            (artifacts() / log.name).write_bytes(log.read_bytes())


def ready(server, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("server exited before it was ready; inspect its log")
        try:
            curl([ENDPOINT + "/v1/models"], 5)
            return
        except subprocess.CalledProcessError:
            time.sleep(2)
    raise RuntimeError("server did not become ready")


def traced(built, help_text, args, applied, missing, trace):
    """Debug telemetry for a diagnostic pass, its spans and gauges as OTLP/JSON lines."""
    if built["kind"] == "skippy" and supports(help_text, "--telemetry-level"):
        args += ["--telemetry-level", "debug"]
        applied["telemetry_level"] = "debug"
    else:
        missing.append("--telemetry-level")
    return {"SKIPPY_TELEMETRY_OTLP_FILE": str(trace.parent / f"{trace.name}.otlp.jsonl"),
            "SKIPPY_TELEMETRY_OTLP_METRICS_FILE": str(trace.parent / f"{trace.name}.metrics.jsonl")}


def measure(built, help_text, model, inputs, key, index, server, shape, output, trace=None,
            prefixes=("MTL",)):
    """`index` is the round, or None for a diagnostic pass that writes `trace`."""
    coordinate = f'{inputs["repo"]}@{inputs["revision"]}'
    args, applied, missing = server_args(built, help_text, model, coordinate,
                                         shape["context_per_lane"], server)
    overrides = None if trace is None else traced(built, help_text, args, applied, missing, trace)
    pass_name = "trace" if index is None else f"r{index + 1}"
    name = f'{key}-{pass_name}-{built["label"]}-{server["id"]}'
    log = output / f"{name}.log"
    samples = []
    with log.open("wb") as stream:
        process = start(args, overrides, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
        try:
            ready(process, 900)
            offload(log, prefixes)
            for point in server["points"]:
                metrics, rejected = run_point(ENDPOINT, point, coordinate, inputs["tokenizer_path"],
                                              shape["warmup_requests"], WORK / "aiperf" / name / point["id"],
                                              may_reject=may_reject(built, server, point))
                # A rejection is the answer of a server that is still serving; one that died is a fault.
                if rejected is not None and process.poll() is not None:
                    raise RuntimeError("server exited while it was rejecting requests; inspect its log")
                samples.append({"model": key, "round": None if index is None else index + 1,
                                "arm": built["label"],
                                "server": server["id"], "point": point["id"], "applied": applied,
                                "unsupported": missing, "metrics": metrics, "rejected": rejected})
        finally:
            stop(process)
    return samples


def artifacts():
    output = Path(os.environ["JIANYANG_ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    return output


def start_measurement():
    """Check every arm before any round, so a variant whose flag an arm lacks fails before
    anything is measured, and write the record the later steps add to."""
    if os.geteuid() == 0:
        raise ValueError("native inference must not execute as root")
    plan = native_plan()
    entry = BACKENDS[plan["backend"]]
    built = [read_json(BUILT / f'{arm["label"]}.json') for arm in plan["arms"]]
    if [(arm["label"], arm["sha"]) for arm in built] != [(arm["label"], arm["sha"]) for arm in plan["arms"]]:
        raise ValueError("built arms differ from the admitted plan")
    for arm in built:
        if digest(Path(arm["binary"])) != arm["binary_sha256"]:
            raise ValueError(f'{arm["label"]} binary changed after it was built')
    shape, helps = scenario(plan.get("servers")), {arm["label"]: capabilities(arm) for arm in built}
    for arm in built:
        for server in shape["servers"]:
            server_flags(arm, helps[arm["label"]], server)
    write_json(HELPS, helps)
    record = {"schema_version": 1, "harness": plan["harness"], "scenario_sha256": digest(SCENARIO),
              "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "host": {"node": os.environ.get("JIANYANG_NODE"), "backend": plan["backend"],
                       **entry["facts"]()},
              "arms": built, "rounds": plan["rounds"], "models": {}, "samples": []}
    if plan["trace"]:
        # Tracing slows the arm it runs on, so its samples never join the comparison.
        record["diagnostic"] = {"telemetry": "debug", "arm": "candidate", "samples": []}
        TRACES.mkdir(parents=True, exist_ok=True)
    write_json(artifacts() / RECORD, record)


def prepare_model(key):
    plan = native_plan()
    if key not in plan["models"]:
        raise ValueError(f"{key} is not a model of the admitted plan")
    entry, record = BACKENDS[plan["backend"]], read_json(artifacts() / RECORD)
    inputs = fixture(key)
    available = entry["free_memory"](record["host"])
    if available < sum(file["size"] for file in inputs["files"]) + entry["reserve_gib"] * 1024**3:
        raise ValueError(f"{key}: insufficient {plan['backend']} free memory for weights and reserve")
    model = admit_model(key, inputs)
    inputs = {**inputs, "tokenizer_path": str(tokenizer(inputs))}
    record["models"][key] = {"repo": inputs["repo"], "revision": inputs["revision"], "files": inputs["files"]}
    MODELS.mkdir(parents=True, exist_ok=True)
    write_json(MODELS / f"{key}.json", {"model": str(model), "inputs": inputs})
    write_json(artifacts() / RECORD, record)


def measure_step(key, label, server_id, round_number=None):
    """One arm on one server for one round, or the candidate's diagnostic pass without a round."""
    plan = native_plan()
    shape = scenario(plan.get("servers"))
    server = next((server for server in shape["servers"] if server["id"] == server_id), None)
    diagnostic = round_number is None
    if (server is None or label not in {arm["label"] for arm in plan["arms"]}
            or not diagnostic and not 1 <= round_number <= plan["rounds"]
            or diagnostic and (not plan["trace"] or label != "candidate")):
        raise ValueError(f"{label} on {server_id} is not a step of the admitted plan")
    output = artifacts()
    record, prepared = read_json(output / RECORD), read_json(MODELS / f"{key}.json")
    samples = measure(read_json(BUILT / f"{label}.json"), read_json(HELPS)[label], Path(prepared["model"]),
                      prepared["inputs"], key, None if diagnostic else round_number - 1, server, shape,
                      output, trace=TRACES / f"{key}-{server_id}" if diagnostic else None,
                      prefixes=BACKENDS[plan["backend"]]["device_prefixes"])
    (record["diagnostic"] if diagnostic else record)["samples"] += samples
    write_json(output / RECORD, record)


def finish_measurement():
    """Archive what the steps measured; the job runs it after a failure too."""
    output = artifacts()
    if (WORK / "aiperf").is_dir():
        with tarfile.open(output / "aiperf.tar.gz", "w:gz") as archive:
            archive.add(WORK / "aiperf", arcname="aiperf")
    if TRACES.is_dir():
        with tarfile.open(output / "traces.tar.gz", "w:gz") as archive:
            archive.add(TRACES, arcname="traces")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("admit", "dispatch"):
        actions.add_parser(name).add_argument("evidence", type=Path)
    actions.add_parser("cancel").add_argument("ledger", type=Path)
    actions.add_parser("toolcheck")
    actions.add_parser("build").add_argument("arm")
    step = actions.add_parser("step").add_subparsers(dest="step", required=True)
    step.add_parser("start")
    step.add_parser("model").add_argument("model")
    measured = step.add_parser("measure")
    for name in ("model", "round", "arm", "server"):
        measured.add_argument(name, type=int if name == "round" else str)
    traced_pass = step.add_parser("trace")
    for name in ("model", "server"):
        traced_pass.add_argument(name)
    step.add_parser("finish")
    args = parser.parse_args()
    if args.action == "admit":
        admit(args.evidence)
    elif args.action == "dispatch":
        dispatch(args.evidence)
    elif args.action == "cancel":
        cancel(Pilot(), args.ledger)
    elif args.action == "toolcheck":
        toolcheck()
    elif args.action == "build":
        build_arm(args.arm)
    elif args.step == "start":
        start_measurement()
    elif args.step == "model":
        prepare_model(args.model)
    elif args.step == "measure":
        measure_step(args.model, args.arm, args.server, args.round)
    elif args.step == "trace":
        measure_step(args.model, "candidate", args.server)
    else:
        finish_measurement()


if __name__ == "__main__":
    try:
        with cancel_on_signal():
            main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError,
            KeyboardInterrupt) as error:
        traceback.print_exc()
        sys.exit(str(error))
