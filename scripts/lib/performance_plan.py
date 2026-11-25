"""Plan a same-host performance comparison: its arms, rounds and the JY jobs that run them."""

import json
import math
import re
from pathlib import Path

from .ci_artifacts import digest, read_json
from .inference_inputs import fixture
from .inference_ladder import ladder_models
from .performance_backends import BACKENDS

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


def count(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def scenario():
    value = read_json(SCENARIO)
    if (set(value) != {"schema_version", "context_per_lane", "warmup_requests", "servers"}
            or value["schema_version"] != 1 or not value["servers"]):
        raise ValueError("invalid performance scenario")
    context = count(value["context_per_lane"], "context_per_lane")
    count(value["warmup_requests"], "warmup_requests", 0)
    servers, points = set(), set()
    for server in value["servers"]:
        if set(server) != {"id", "lanes", "pool_tokens", "points"} or not server["points"]:
            raise ValueError("invalid performance server")
        if server["id"] in servers:
            raise ValueError(f'duplicate performance server: {server["id"]}')
        servers.add(server["id"])
        lanes = count(server["lanes"], f'{server["id"]} lanes')
        if server["pool_tokens"] is not None and count(server["pool_tokens"], "pool_tokens") < context:
            raise ValueError(f'{server["id"]}: a KV pool must hold at least one lane\'s context')
        for point in server["points"]:
            if set(point) != POINT_FIELDS or point["id"] in points:
                raise ValueError("invalid or duplicate performance point")
            points.add(point["id"])
            for field in ("concurrency", "input_tokens", "output_tokens", "requests"):
                count(point[field], f'{point["id"]} {field}')
            count(point["depth"], f'{point["id"]} depth', 0)
            if point["concurrency"] > lanes:
                raise ValueError(f'{point["id"]}: concurrency exceeds the server\'s lanes')
            if point["depth"] + point["input_tokens"] + point["output_tokens"] > context:
                raise ValueError(f'{point["id"]}: one request exceeds a lane\'s context')
    return value


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


def arms(candidate, baseline, reference):
    """Candidate and baseline are Skippy commits; reference is llama.cpp's own server at its pin."""
    if not SHA.fullmatch(candidate) or not SHA.fullmatch(baseline):
        raise ValueError("arms must be immutable 40-digit commit SHAs")
    result = [{"label": "candidate", "kind": "skippy", "sha": candidate, "path": "candidate"},
              {"label": "baseline", "kind": "skippy", "sha": baseline, "path": "baseline"}]
    if reference is not None:
        if not SHA.fullmatch(reference):
            raise ValueError("the reference pin must be a 40-digit llama.cpp commit")
        result.append({"label": "reference", "kind": "llama-server", "sha": reference, "path": "reference"})
    return result


def round_order(arm_list, index):
    """Each round starts one arm later, so no arm always runs first on a cold or hot machine."""
    shift = index % len(arm_list)
    return arm_list[shift:] + arm_list[:shift]


def models(keys):
    known = {model["id"] for model in ladder_models()}
    if not keys or len(set(keys)) != len(keys) or not set(keys) <= known:
        raise ValueError(f"models must be distinct ladder ids from: {', '.join(sorted(known))}")
    return keys


def nodes(values):
    if len(set(values)) != len(values) or not all(NODE.fullmatch(value) for value in values):
        raise ValueError("nodes must be distinct JY node names")
    return values


def job_spec(harness, arm_list, keys, rounds, node, label, *, trace=False, backend="metal"):
    """`trace` adds one diagnostic pass of the candidate with debug telemetry, kept out
    of the measurements: tracing costs time, so a traced arm is not a comparable one."""
    count(rounds, "rounds")
    if type(trace) is not bool:
        raise ValueError("trace must be true or false")
    if rounds > MAX_ROUNDS:
        raise ValueError(f"at most {MAX_ROUNDS} rounds fit the job's time budget")
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; the table holds {', '.join(sorted(BACKENDS))}")
    shape = scenario()
    entry = BACKENDS[backend]
    if entry["pin_required"] and node is None:
        raise ValueError(f"a {backend} plan must name exactly one node: its GPUs differ between hosts")
    largest = max(sum(file["size"] for file in fixture(key)["files"]) for key in keys)
    selector = {key: value for key, value in entry["selector"].items() if key != "memory"}
    selector["memory"] = {entry["memory_key"]: {"min": math.ceil(largest / 1024**3) + entry["reserve_gib"]},
                          **entry["selector"].get("memory", {})}
    target = {"executor": "lab", "num_nodes": 1, "selector": selector}
    if node is not None:
        target["nodes"] = [node]
    repos = [{"url": SKIPPY, "rev": harness, "path": "harness"}] + [
        {"url": SKIPPY if arm["kind"] == "skippy" else LLAMA, "rev": arm["sha"], "path": arm["path"]}
        for arm in arm_list]
    plan = {"harness": harness, "backend": backend, "arms": arm_list, "models": keys,
            "rounds": rounds, "trace": trace}
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
            "workspace": {"repos": repos, "build": "cd harness && just inference-performance-build",
                          "retain": "on_failure"},
            "env": {"SKIPPY_PERFORMANCE_PLAN": json.dumps(plan, separators=(",", ":")),
                    **entry["env"]},
            "command": "cd harness && just inference-performance-execute",
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
