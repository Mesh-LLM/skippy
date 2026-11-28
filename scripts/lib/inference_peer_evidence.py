"""Verify that both successful JY attempts actually qualified together."""

import re
from datetime import datetime

from .ci_artifacts import read_json, write_json
from .inference_throughput import fetch_artifact, successful_run


def overlap(leader, worker):
    windows = []
    for job in (leader, worker):
        run = job["runs"][-1]
        try:
            window = tuple(datetime.fromisoformat(run[name].replace("Z", "+00:00"))
                           for name in ("started_at", "ended_at"))
        except (KeyError, ValueError, AttributeError):
            raise ValueError("split attempt has missing or invalid execution timestamps") from None
        if any(value.tzinfo is None for value in window) or window[0] >= window[1]:
            raise ValueError("split attempt has an invalid execution window")
        windows.append(window)
    if max(window[0] for window in windows) >= min(window[1] for window in windows):
        raise ValueError("split attempts did not execute together")


def verify_split_peer(pilot, job, row, sha, profile, output):
    formation = read_json(output / f'{row["id"]}-formation.json')
    members = formation.get("members") or []
    if (formation.get("id") != job.get("formation_id") or formation.get("status") != "succeeded"
            or len(members) != 2 or {m.get("role") for m in members} != {"leader", "worker"}
            or any(m.get("status") != "succeeded" for m in members)):
        raise ValueError("split requires two successful members of its own formation")
    leader = next(m for m in members if m["role"] == "leader")
    worker = next(m for m in members if m["role"] == "worker")
    if not leader.get("node") or not worker.get("node") or leader["node"] == worker["node"]:
        raise ValueError("split requires two distinct reserved nodes")
    if leader["job_id"] != job["id"] or not re.fullmatch(r"job_[A-Za-z0-9_]+", worker["job_id"]):
        raise ValueError("split formation job identity mismatch")
    worker_job = pilot.api("GET", f'/v1/jobs/{worker["job_id"]}')
    if (worker_job.get("formation_id") != job["formation_id"]
            or worker_job.get("id") != worker["job_id"]):
        raise ValueError("split worker job belongs to another formation")
    leader_run, worker_run = successful_run(job, sha), successful_run(worker_job, sha)
    if leader.get("run_id") != leader_run or worker.get("run_id") != worker_run:
        raise ValueError("split formation attempt identity mismatch")
    if any(member["node"] != native["runs"][-1].get("machine_node")
           for member, native in ((leader, job), (worker, worker_job))):
        raise ValueError("split attempts did not execute on their reserved nodes")
    overlap(job, worker_job)
    qualification = fetch_artifact(pilot, leader_run, "qualification.json")
    worker_proof = fetch_artifact(pilot, worker_run, "split-peer.json")
    if not isinstance(qualification, dict) or not isinstance(worker_proof, dict):
        raise ValueError("split peer evidence is missing or malformed")
    model = row["id"].removesuffix("-split")
    common = {"formation_id": job["formation_id"], "source_sha": sha, "model": model, "profile": profile}
    expected_leader = dict(common, run_id=leader_run, role="leader")
    expected_worker = dict(common, run_id=worker_run, role="worker")
    for proof, local, peer in ((qualification.get("split_peer") or {}, expected_leader, expected_worker),
                               (worker_proof, expected_worker, expected_leader)):
        if (not isinstance(proof, dict) or proof.get("local") != local or proof.get("peer") != peer
                or proof.get("completion_acknowledged") is not True):
            raise ValueError("split peer evidence does not match its formation/source/attempts")
    if any(qualification.get(k) != v for k, v in
           {"source_sha": sha, "model": model, "profile": profile, "mode": "split", "status": "passed"}.items()):
        raise ValueError("split qualification identity mismatch")
    write_json(output / f'{row["id"]}-split-peer.json', {
        "leader": qualification["split_peer"], "worker": worker_proof, "status": "passed",
    })
