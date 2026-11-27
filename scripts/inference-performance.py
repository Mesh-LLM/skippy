#!/usr/bin/env python3
"""Compare Skippy commits, and llama.cpp's own server, on one Metal host at a time."""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request

from lib.ci_artifacts import digest, read_json, write_json
from lib.ci_process import cancel_on_signal, capture, command, start, stop
from lib.ci_smoke import curl
from lib import inference_metal
from lib.inference_checks import ENDPOINT, offload
from lib.inference_inputs import admit as admit_model
from lib.inference_inputs import fixture
from lib.jy_qualification import ORIGIN, Pilot
from lib.performance_arms import build, capabilities, server_args, supports
from lib.performance_load import run_point, tokenizer
from lib.performance_plan import (SCENARIO, arms, job_spec, models, nodes, pull_request_head,
                                  pull_request_number, round_order, scenario)
from lib.performance_report import markdown, rows, summarize

REPO = "Mesh-LLM/skippy"
LLAMA_REPO = "ggml-org/llama.cpp"
WORK = Path("target/inference-performance")
BUILT = WORK / "built.json"
# Outside the artifact directory: artifact names are flat, so the traces travel as one archive.
TRACES = WORK / "traces"
TERMINAL = {"succeeded", "failed", "cancelled", "expired"}
RESERVE = 16 * 1024**3


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
    """A commit on main, or with `pr:N` an open pull request's head, as (commit, N or None)."""
    number = pull_request_number(ref)
    if number is None:
        return resolve_main(ref), None
    pull = github(f"repos/{REPO}/pulls/{number}")
    sha = pull_request_head(pull, REPO)
    login = pull["user"]["login"]
    permission = github(f"repos/{REPO}/collaborators/{quote(login, safe='')}/permission")["permission"]
    if permission not in {"admin", "maintain", "write"}:
        raise ValueError("native performance PR author must be a repository collaborator with write access")
    return sha, number


def off_main(pull_requests):
    """A line for each arm taken from a pull request, so no report reads as main's."""
    return "".join(f"The {label} was selected from PR #{number}.\n\n"
                   for label, number in pull_requests.items() if number is not None)


def reference_pin(sha):
    content = github(f"repos/{REPO}/contents/llama_cpp/upstream.txt?ref={sha}")
    return base64.b64decode(content["content"]).decode("utf-8").strip()


def committed_at(arm_list):
    """When each arm's commit was made, so stored rows can be graphed in commit order."""
    return {arm["sha"]: github(f'repos/{REPO if arm["kind"] == "skippy" else LLAMA_REPO}/commits/'
                               f'{arm["sha"]}')["commit"]["committer"]["date"] for arm in arm_list}


def admit(evidence):
    harness = capture(["git", "rev-parse", "HEAD"]).strip()
    candidate, candidate_pull = resolve(os.environ["CANDIDATE"])
    baseline, baseline_pull = resolve(os.environ["BASELINE"])
    pull_requests = {"candidate": candidate_pull, "baseline": baseline_pull}
    with_reference = os.environ.get("REFERENCE", "true") == "true"
    trace = os.environ.get("TRACE", "false") == "true"
    arm_list = arms(candidate, baseline, reference_pin(candidate) if with_reference else None)
    keys = models([key.strip() for key in os.environ["MODELS"].split(",") if key.strip()])
    pins = nodes([node.strip() for node in os.environ.get("NODES", "").split(",") if node.strip()])
    rounds = int(os.environ.get("ROUNDS") or 3)
    jobs = [{"label": node or "any",
             "spec": job_spec(harness, arm_list, keys, rounds, node, node or "any", trace=trace)}
            for node in (pins or [None])]
    evidence.mkdir(parents=True, exist_ok=True)
    write_json(evidence / "plan.json", {"harness": harness, "arms": arm_list, "models": keys,
                                        "rounds": rounds, "trace": trace, "pull_requests": pull_requests,
                                        "committed_at": committed_at(arm_list), "jobs": jobs})
    for arm in arm_list:
        print(f'{arm["label"]}: {arm["kind"]} {arm["sha"]}')
    print(off_main(pull_requests), end="")
    print(f"{len(jobs)} job(s), {len(keys)} model(s), {rounds} round(s); nothing has run yet")


def resource_id(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", identifier):
        raise ValueError("invalid JY resource id")
    return identifier


def cancel(pilot, ledger):
    failures = []
    for entry in read_json(ledger):
        identifier = entry if isinstance(entry, str) else entry.get("id")
        try:
            if identifier is None:
                # JY replays the existing job for a project-scoped idempotency key.
                recovered = pilot.api("POST", "/v1/jobs", {
                    "spec": entry["spec"], "idempotency_key": entry["idempotency_key"]})
                identifier = resource_id(recovered["id"])
            job = pilot.api("GET", f"/v1/jobs/{resource_id(identifier)}")
            if job.get("status") not in TERMINAL:
                pilot.api("POST", f"/v1/jobs/{identifier}/cancel")
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            failures.append(f"{identifier or entry.get('idempotency_key')}: {error}")
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
                for row in rows(result, scenario(), plan["committed_at"], job["id"], plan["pull_requests"])),
        encoding="utf-8")
    download(pilot, run_id, "aiperf.tar.gz", evidence / f"{label}-aiperf.tar.gz")
    trace_notice = ""
    if plan["trace"]:
        try:
            download(pilot, run_id, "traces.tar.gz", evidence / f"{label}-traces.tar.gz")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            trace_notice = f"\nDiagnostic trace archive unavailable: {error}\n"
    for failure in result.get("diagnostic", {}).get("failures", []):
        trace_notice += f"\nDiagnostic trace failed: {failure}\n"
    host = f'{result["host"]["chip"]} ({result["host"].get("node") or "unknown node"})'
    return markdown(f"Inference performance: {label}", host, summarize(result["samples"]), result) + trace_notice


def save_ledger(path, entries):
    pending = path.with_suffix(".pending")
    write_json(pending, entries)
    pending.replace(path)


def dispatch(evidence):
    pilot, plan = Pilot(), read_json(evidence / "plan.json")
    ledger, submissions, identifiers = evidence / "jobs.json", [], []
    save_ledger(ledger, submissions)
    run = f'{os.environ.get("GITHUB_RUN_ID", "local")}-{os.environ.get("GITHUB_RUN_ATTEMPT", "1")}'
    deadline = time.monotonic() + 20700
    try:
        for job in plan["jobs"]:
            entry = {"spec": job["spec"], "idempotency_key": f'inference-performance-{run}-{job["label"]}',
                     "id": None}
            submissions.append(entry)
            save_ledger(ledger, submissions)
            created = pilot.api("POST", "/v1/jobs", {key: entry[key] for key in ("spec", "idempotency_key")})
            entry["id"] = resource_id(created["id"])
            identifiers.append(entry["id"])
            save_ledger(ledger, submissions)
            print(f'{job["label"]}: {created["id"]} {created.get("status")}', flush=True)
        finished = [wait(pilot, identifier, evidence, deadline) for identifier in identifiers]
    except (Exception, KeyboardInterrupt):
        try:
            cancel(pilot, ledger)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as cleanup:
            print(f"Cancellation incomplete: {cleanup}", file=sys.stderr)
        raise
    reports, failures = [], []
    for job, spec in zip(finished, plan["jobs"]):
        if job["status"] != "succeeded":
            failures.append(f'{spec["label"]}: {job["status"]}')
            continue
        try:
            reports.append(collect(pilot, job, plan, spec["label"], evidence))
        except Exception as error:
            failures.append(f'{spec["label"]}: collection failed: {error}')
    report = off_main(plan["pull_requests"]) + "\n".join(reports)
    if failures:
        report += "\n## Incomplete jobs or evidence\n\n" + "\n".join("- " + error for error in failures) + "\n"
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
    expected = arms(labels["candidate"], labels["baseline"], labels.get("reference"))
    if (plan["arms"] != expected or plan["models"] != models(plan["models"])
            or type(plan.get("trace")) is not bool):
        raise ValueError("performance plan arms, models or trace flag are malformed")
    return plan


def build_arms():
    plan = native_plan()
    inference_metal.chip()
    WORK.mkdir(parents=True, exist_ok=True)
    try:
        write_json(BUILT, [build(arm, WORK / f'build-{arm["label"]}.log') for arm in plan["arms"]])
    finally:
        # execute() is the only other place build logs reach the artifact
        # directory, and it never runs after a build failure.
        output = os.environ.get("JIANYANG_ARTIFACT_DIR")
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            for log in WORK.glob("build-*.log"):
                (Path(output) / log.name).write_bytes(log.read_bytes())


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


def measure(built, help_text, model, inputs, key, index, server, shape, output, trace=None):
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
            offload(log)
            for point in server["points"]:
                metrics = run_point(ENDPOINT, point, coordinate, inputs["tokenizer_path"],
                                    shape["warmup_requests"], WORK / "aiperf" / name / point["id"])
                samples.append({"model": key, "round": None if index is None else index + 1,
                                "arm": built["label"],
                                "server": server["id"], "point": point["id"], "applied": applied,
                                "unsupported": missing, "metrics": metrics})
        finally:
            stop(process)
    return samples


def execute():
    if os.geteuid() == 0:
        raise ValueError("native inference must not execute as root")
    plan, chip = native_plan(), inference_metal.chip()
    working_set = inference_metal.working_set(chip)
    built = read_json(BUILT)
    if [(entry["label"], entry["sha"]) for entry in built] != [(arm["label"], arm["sha"]) for arm in plan["arms"]]:
        raise ValueError("built arms differ from the admitted plan")
    for entry in built:
        if digest(Path(entry["binary"])) != entry["binary_sha256"]:
            raise ValueError(f'{entry["label"]} binary changed after it was built')
    output = Path(os.environ["JIANYANG_ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    for log in WORK.glob("build-*.log"):
        (output / log.name).write_bytes(log.read_bytes())
    shape, helps = scenario(), {entry["label"]: capabilities(entry) for entry in built}
    record = {"schema_version": 1, "harness": plan["harness"], "scenario_sha256": digest(SCENARIO),
              "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "host": {"chip": chip, "macos": capture(["sw_vers", "-productVersion"]).strip(),
                       "metal_working_set_bytes": working_set, "node": os.environ.get("JIANYANG_NODE")},
              "arms": built, "rounds": plan["rounds"], "models": {}, "samples": []}
    if plan["trace"]:
        # Tracing slows the arm it runs on, so its samples never join the comparison.
        record["diagnostic"] = {"telemetry": "debug", "arm": "candidate", "samples": []}
        TRACES.mkdir(parents=True, exist_ok=True)
    diagnostic_inputs = []
    try:
        for key in plan["models"]:
            inputs = fixture(key)
            available = inference_metal.available_memory(capture(["vm_stat"]), working_set)
            if available < sum(file["size"] for file in inputs["files"]) + RESERVE:
                raise ValueError(f"{key}: insufficient Metal working set or free memory for weights and reserve")
            model = admit_model(key, inputs)
            inputs = {**inputs, "tokenizer_path": tokenizer(inputs)}
            record["models"][key] = {"repo": inputs["repo"], "revision": inputs["revision"],
                                     "files": inputs["files"]}
            for index in range(plan["rounds"]):
                for entry in round_order(built, index):
                    for server in shape["servers"]:
                        record["samples"] += measure(entry, helps[entry["label"]], model, inputs, key,
                                                     index, server, shape, output)
                        write_json(output / "performance.json", record)
            diagnostic_inputs.append((key, model, inputs))
        if plan["trace"]:
            candidate = next(entry for entry in built if entry["label"] == "candidate")
            record["diagnostic"]["failures"] = []
            for key, model, inputs in diagnostic_inputs:
                for server in shape["servers"]:
                    try:
                        record["diagnostic"]["samples"] += measure(
                            candidate, helps["candidate"], model, inputs, key, None, server, shape, output,
                            trace=TRACES / f'{key}-{server["id"]}')
                    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
                        record["diagnostic"]["failures"].append(f'{key}/{server["id"]}: {error}')
                    write_json(output / "performance.json", record)
    finally:
        write_json(output / "performance.json", record)
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
    actions.add_parser("build")
    actions.add_parser("execute")
    args = parser.parse_args()
    if args.action == "admit":
        admit(args.evidence)
    elif args.action == "dispatch":
        dispatch(args.evidence)
    elif args.action == "cancel":
        cancel(Pilot(), args.ledger)
    elif args.action == "build":
        build_arms()
    else:
        execute()


if __name__ == "__main__":
    try:
        with cancel_on_signal():
            main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError,
            KeyboardInterrupt) as error:
        sys.exit(str(error))
