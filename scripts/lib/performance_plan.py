"""Plan a same-host performance comparison: its arms, rounds and the JY jobs that run them."""

import json
import math
import re
import shlex
from pathlib import Path

from .ci_artifacts import digest, read_json
from .inference_inputs import fixture
from .inference_ladder import ladder_models
from .performance_backends import BACKENDS
from .performance_load import request_rate

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "ci/inference-performance.json"
SKIPPY = "https://github.com/Mesh-LLM/skippy.git"
LLAMA = "https://github.com/ggml-org/llama.cpp.git"
SHA = re.compile(r"[0-9a-f]{40}")
PULL_REQUEST = re.compile(r"pr:([1-9][0-9]{0,6})")
BRANCH = re.compile(r"branch:([A-Za-z0-9._/-]{1,200})")
NODE = re.compile(r"[A-Za-z0-9._-]{1,64}")
MAX_ROUNDS = 9
POINT_FIELDS = {"id", "concurrency", "depth", "input_tokens", "output_tokens", "requests"}
SERVER_FIELDS = {"id", "lanes", "pool_tokens", "points"}
SKIPPY_FLAG = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
FLAG_VALUE = re.compile(r"[A-Za-z0-9._,:-]{1,64}")
# server_args sets these on every Skippy arm, so a variant's own flags cannot change them
HARNESS_FLAGS = {"startup-timeout-secs", "ctx-size", "n-gpu-layers", "bind-addr", "model-path", "model-id",
                 "generation-concurrency", "speculative-strategy", "kv-pool-tokens", "debug", "telemetry-level"}
# What a step is expected to take, in minutes, so the job's progress follows time rather than step count.
BUILD_MINUTES = {"skippy": 15, "llama-server": 5}
NOTE = re.compile(r"[A-Za-z0-9 ._:+-]{1,59}")
PROGRESS = ("progress() { printf '%s%% %s\\n' \"$1\" \"$2\" > \"$JIANYANG_PROGRESS_PATH\"; "
            "echo \"PROGRESS ${JIANYANG_NODE:-?} $(date +%T) $1% $2\"; }")


def count(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def skippy_flags(server):
    """A variant server's own Skippy flags: names without the dashes, positive counts or short words."""
    flags = server["skippy_flags"]
    if type(flags) is not dict or not flags:
        raise ValueError(f'{server["id"]}: skippy_flags must name at least one flag')
    for name, value in flags.items():
        if not SKIPPY_FLAG.fullmatch(name) or name in HARNESS_FLAGS:
            raise ValueError(f'{server["id"]}: --{name} is not a flag a variant may set')
        if not (type(value) is int and value >= 1 or type(value) is str and FLAG_VALUE.fullmatch(value)):
            raise ValueError(f'{server["id"]}: --{name} needs a positive integer or a short word')
    return flags


def rates(point):
    """A point's per-backend arrivals per second, each a positive number for a backend in the table."""
    value = point["request_rate"]
    if (type(value) is not dict or not value or not set(value) <= set(BACKENDS)
            or not all(type(rate) in {int, float} and 0 < rate <= 100 for rate in value.values())):
        raise ValueError(f'{point["id"]}: request_rate maps backends to positive arrivals per second')
    return value


def scenario(named=None):
    """The checked scenario; with `named`, only those servers and their points, to rerun one
    workload without the rest. A server with its own `skippy_flags` is a variant of another,
    run only when named, so a campaign keeps the same cells."""
    value = read_json(SCENARIO)
    if (set(value) != {"schema_version", "context_per_lane", "warmup_requests", "servers"}
            or value["schema_version"] != 1 or not value["servers"]):
        raise ValueError("invalid performance scenario")
    context = count(value["context_per_lane"], "context_per_lane")
    count(value["warmup_requests"], "warmup_requests", 0)
    servers, points, variants = set(), set(), set()
    for server in value["servers"]:
        if set(server) - {"skippy_flags"} != SERVER_FIELDS or not server["points"]:
            raise ValueError("invalid performance server")
        if "skippy_flags" in server:
            skippy_flags(server)
            variants.add(server["id"])
        if server["id"] in servers:
            raise ValueError(f'duplicate performance server: {server["id"]}')
        servers.add(server["id"])
        lanes = count(server["lanes"], f'{server["id"]} lanes')
        if server["pool_tokens"] is not None and count(server["pool_tokens"], "pool_tokens") < context:
            raise ValueError(f'{server["id"]}: a KV pool must hold at least one lane\'s context')
        for point in server["points"]:
            if set(point) - {"request_rate"} != POINT_FIELDS or point["id"] in points:
                raise ValueError("invalid or duplicate performance point")
            if "request_rate" in point:
                rates(point)
            points.add(point["id"])
            for field in ("concurrency", "input_tokens", "output_tokens", "requests"):
                count(point[field], f'{point["id"]} {field}')
            count(point["depth"], f'{point["id"]} depth', 0)
            if point["concurrency"] > lanes:
                raise ValueError(f'{point["id"]}: concurrency exceeds the server\'s lanes')
            if point["depth"] + point["input_tokens"] + point["output_tokens"] > context:
                raise ValueError(f'{point["id"]}: one request exceeds a lane\'s context')
    if named is None:
        return {**value, "servers": [server for server in value["servers"] if server["id"] not in variants]}
    unknown = set(named) - servers
    if not named or unknown:
        raise ValueError(f"unknown performance servers: {', '.join(sorted(unknown)) or 'none named'}")
    return {**value, "servers": [server for server in value["servers"] if server["id"] in named]}


def pull_request_number(ref):
    """`pr:N` names the head of pull request N; any other ref names a commit on main."""
    match = PULL_REQUEST.fullmatch(ref)
    return int(match[1]) if match else None


def branch_name(ref):
    """`branch:NAME` names the head of a branch of this repository, measured as off main."""
    match = BRANCH.fullmatch(ref)
    return match[1] if match else None


def pull_request_head(pull, repository):
    """The head of an open pull request from this repository: a fork's code never runs on the lab."""
    if pull.get("state") != "open" or (pull.get("head", {}).get("repo") or {}).get("full_name") != repository:
        raise ValueError(f'#{pull.get("number")} is not an open pull request from {repository}')
    return pull["head"]["sha"]


def arms(candidate, baseline, reference, baseline_reference=None):
    """Candidate and baseline are Skippy commits; reference is llama.cpp's own server at the candidate's
    pin. A baseline on another pin adds llama.cpp at that pin as baseline-reference, so a change that
    moves the pin shows whether llama.cpp moved as well as whether Skippy did."""
    if not SHA.fullmatch(candidate) or not SHA.fullmatch(baseline):
        raise ValueError("arms must be immutable 40-digit commit SHAs")
    result = [{"label": "candidate", "kind": "skippy", "sha": candidate, "path": "candidate"},
              {"label": "baseline", "kind": "skippy", "sha": baseline, "path": "baseline"}]
    pins = [("reference", reference)]
    if baseline_reference not in (None, reference):
        if reference is None:
            raise ValueError("a baseline reference needs the reference arm")
        pins.append(("baseline-reference", baseline_reference))
    for label, pin in pins:
        if pin is None:
            continue
        if not SHA.fullmatch(pin):
            raise ValueError(f"the {label} pin must be a 40-digit llama.cpp commit")
        result.append({"label": label, "kind": "llama-server", "sha": pin, "path": label})
    return result


def round_order(arm_list, index):
    """Each round starts one arm later, so no arm always runs first on a cold or hot machine."""
    shift = index % len(arm_list)
    return arm_list[shift:] + arm_list[:shift]


def measure_minutes(server, backend):
    """A server's start and its points' requests: about two seconds each when kept in flight,
    and as long as their arrivals take at a point's own rate."""
    def seconds(point):
        rate = request_rate(point, backend)
        return point["requests"] * 2 if rate is None else point["requests"] / rate + 10
    return 0.5 + sum(seconds(point) for point in server["points"]) / 60


def job_steps(arm_list, keys, rounds, servers, trace, backend):
    """The job's commands in the order they run: each a `just` recipe and its arguments, what it
    finishes, and the minutes it is expected to take."""
    steps = [(["inference-performance-toolcheck"], "tools checked", 0.2)]
    steps += [(["inference-performance-build-arm", arm["label"]], f'{arm["label"]} built',
               BUILD_MINUTES[arm["kind"]]) for arm in arm_list]
    steps.append((["inference-performance-step", "start"], "measurement ready", 0.3))
    for key in keys:
        steps.append((["inference-performance-step", "model", key], f"{key} admitted", 1))
        for index in range(rounds):
            for arm in round_order(arm_list, index):
                steps += [(["inference-performance-step", "measure", key, str(index + 1), arm["label"],
                            server["id"]], f'r{index + 1} {arm["label"]} {server["id"]}',
                           measure_minutes(server, backend)) for server in servers]
        if trace:
            steps += [(["inference-performance-step", "trace", key, server["id"]], f'trace {server["id"]}',
                       measure_minutes(server, backend) * 1.5) for server in servers]
    steps.append((["inference-performance-step", "finish"], "evidence archived", 0.5))
    return steps


def job_command(steps):
    """A shell script that runs `steps` from the harness checkout and reports progress after each:
    the percent of the expected time spent, never falling, and 100 only once all have run. A
    failure names the command that stopped it and still archives what was measured."""
    total = sum(minutes for _, _, minutes in steps)
    lines = ["set -euo pipefail", PROGRESS,
             "trap 'echo \"inference performance stopped at: $BASH_COMMAND\" >&2' ERR",
             "trap 'status=$?; if [ \"$status\" -ne 0 ]; then just inference-performance-step finish "
             ">/dev/null 2>&1 || true; fi' EXIT",
             'progress 0 "starting"', "cd harness"]
    spent = 0
    for args, note, minutes in steps:
        note = note[:59]
        if not NOTE.fullmatch(note):
            raise ValueError(f"progress note {note!r} needs letters, digits and . _ : + - only")
        spent += minutes
        lines += [shlex.join(["just", *args]), f'progress {min(99, math.floor(spent / total * 100))} "{note}"']
    lines.append('progress 100 "done"')
    return "\n".join(lines) + "\n"


def models(keys):
    known = {model["id"] for model in ladder_models()}
    if not keys or len(set(keys)) != len(keys) or not set(keys) <= known:
        raise ValueError(f"models must be distinct ladder ids from: {', '.join(sorted(known))}")
    return keys


def nodes(values):
    if len(set(values)) != len(values) or not all(NODE.fullmatch(value) for value in values):
        raise ValueError("nodes must be distinct JY node names")
    return values


def job_spec(harness, arm_list, keys, rounds, node, label, *, trace=False, backend="metal", servers=None):
    """`trace` adds one diagnostic pass of the candidate with debug telemetry, kept out
    of the measurements: tracing costs time, so a traced arm is not a comparable one."""
    count(rounds, "rounds")
    if type(trace) is not bool:
        raise ValueError("trace must be true or false")
    if rounds > MAX_ROUNDS:
        raise ValueError(f"at most {MAX_ROUNDS} rounds fit the job's time budget")
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; the table holds {', '.join(sorted(BACKENDS))}")
    shape = scenario(servers)
    entry = BACKENDS[backend]
    for server in shape["servers"]:
        for point in server["points"]:
            request_rate(point, backend)
    if entry["pin_required"] and node is None:
        raise ValueError(f"a {backend} plan must name exactly one node: its GPUs differ between hosts")
    largest = max(sum(file["size"] for file in fixture(key)["files"]) for key in keys)
    if entry["selector"]:
        selector = {key: value for key, value in entry["selector"].items() if key != "memory"}
        selector["memory"] = {entry["memory_key"]: {"min": math.ceil(largest / 1024**3) + entry["reserve_gib"]},
                              **entry["selector"].get("memory", {})}
        target = {"executor": "lab", "num_nodes": 1, "selector": selector}
    else:
        # An entry with no selector targets its pinned node bare: the selector's
        # os and accelerator facts are exactly what some lab agents never report.
        target = {"executor": "lab", "num_nodes": 1}
    if node is not None:
        target["nodes"] = [node]
    repos = [{"url": SKIPPY, "rev": harness, "path": "harness"}] + [
        {"url": SKIPPY if arm["kind"] == "skippy" else LLAMA, "rev": arm["sha"], "path": arm["path"]}
        for arm in arm_list]
    plan = {"harness": harness, "backend": backend, "arms": arm_list, "models": keys,
            "rounds": rounds, "trace": trace}
    if servers is not None:
        plan["servers"] = servers
    points = [point for server in shape["servers"] for point in server["points"]]
    return {
        "name": f"skippy-inference-performance-{label}",
        "description": entry["description"] + " of " + ", ".join(
            f'{arm["label"]} {arm["sha"][:12]}' for arm in arm_list) + f" on {', '.join(keys)}.",
        "priority": 50,
        "target": target,
        "runtime": {
            "model_package": {"ref": "skippy://inference-performance/" + "+".join(keys),
                              "sha256": fixture(keys[0])["files"][0]["sha256"], "egress": "lab_only"},
            # The arms build in the command rather than the workspace build, so
            # the job's progress covers its longest steps.
            "workspace": {"repos": repos, "retain": "on_failure"},
            "env": {"SKIPPY_PERFORMANCE_PLAN": json.dumps(plan, separators=(",", ":")),
                    **entry["env"]},
            "command": job_command(job_steps(arm_list, keys, rounds, shape["servers"], trace, backend)),
        },
        "harness": {
            "name": "custom", "version": "aiperf-0.13.0",
            "trace": {"kind": "synthetic", "ref": f"ci/inference-performance.json@{harness}",
                      "sha256": digest(SCENARIO)},
            "seed": 42, "warmup_requests": shape["warmup_requests"],
            "concurrency": sorted({point["concurrency"] for point in points}),
            "request_count": sum(point["requests"] for point in points) * rounds * len(arm_list) * len(keys),
        },
        "budget": {"max_usd": 0, "max_duration_s": 18000, "grace_s": 300},
    }
