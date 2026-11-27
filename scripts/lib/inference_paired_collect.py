"""Admit and independently summarize same-allocation native comparison evidence."""

import os
import re
import time
from pathlib import Path

from .ci_artifacts import write_json
from .inference_pairs import summarize
from .inference_throughput import ARTIFACT_RETRIES, ARTIFACT_RETRY_DELAY, fetch_artifact, successful_run


def arm_archives(pilot, run_id, runs):
    expected = {f'{r["experiment"]}-{r["round"]}-{r["arm"]}-evidence.tar.gz' for r in runs}
    for attempt in range(ARTIFACT_RETRIES + 1):
        manifest = pilot.api("GET", f"/v1/artifacts/{run_id}")["artifacts"]
        found = {row["key"].rsplit("/", 1)[-1]: row for row in manifest
                 if row["key"].rsplit("/", 1)[-1] in expected and row["kind"] == "raw"}
        if set(found) == expected:
            if any(type(row["size_bytes"]) is not int or row["size_bytes"] <= 0
                   or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) for row in found.values()):
                raise ValueError("invalid paired inference archive manifest")
            return [found[name] for name in sorted(found)]
        if attempt < ARTIFACT_RETRIES:
            time.sleep(ARTIFACT_RETRY_DELAY)
    raise ValueError("incomplete uploaded paired inference arm archives")


def collect_pairs(pilot, job, row, candidate, baseline, output):
    run_id = successful_run(job, candidate)
    evidence = fetch_artifact(pilot, run_id, "paired-probe.json")
    summary = summarize(evidence, candidate, baseline, row["id"])
    placements = {p["role"]: p for p in evidence["placements"]}
    worker_archives = []
    if placements["leader"]["hostname"] != job["machine"]["host"]["hostname"]:
        raise ValueError("comparison leader differs from reserved JY machine")
    if row["mode"] == "split":
        formation = pilot.api("GET", f'/v1/formations/{job["formation_id"]}')
        members = formation["members"]
        if (formation["id"] != job["formation_id"] or formation["status"] != "succeeded" or len(members) != 2
                or {m["role"] for m in members} != {"leader", "worker"}
                or not any(m["role"] == "leader" and m["job_id"] == job["id"] for m in members)
                or any(m["status"] != "succeeded" or m["node"] != placements[m["role"]]["node"]
                       for m in members)):
            raise ValueError("comparison does not match the successful JY formation")
        worker = next(m for m in members if m["role"] == "worker")
        worker_job = pilot.api("GET", f'/v1/jobs/{worker["job_id"]}')
        worker_run = successful_run(worker_job, candidate)
        if worker_job["machine"]["host"]["hostname"] != placements["worker"]["hostname"]:
            raise ValueError("comparison worker differs from reserved JY machine")
        worker_archives = arm_archives(pilot, worker_run, evidence["runs"])
    builds = evidence["builds"]
    if len(builds) != len(placements) or {b["role"] for b in builds} != set(placements):
        raise ValueError("comparison build provenance is incomplete")
    for build in builds:
        for name, sha in (("main", baseline), ("candidate", candidate)):
            identity = build[name]
            if identity["stack_commit"] != sha or not re.fullmatch(r"[0-9a-f]{64}", identity["cli_sha256"]):
                raise ValueError("comparison build provenance mismatch")
    archives = arm_archives(pilot, run_id, evidence["runs"])
    receipt = {**evidence, "summary": summary, "job_id": job["id"], "run_id": run_id,
               "archives": archives, "worker_archives": worker_archives}
    write_json(output / f'{row["id"]}-paired.json', receipt)
    lines = [f'### Paired inference: {row["id"]}', "", f"Main `{baseline}` · candidate `{candidate}`",
             "", "Server phase timings; six A/A pairs and six alternating A/B pairs.", "",
             "| Input tokens | Phase | Main tok/s | Candidate tok/s | Change [pointwise 95% CI] | A/A noise | Verdict |",
             "| ---: | --- | ---: | ---: | --- | ---: | --- |"]
    for shape in summary:
        for phase in ("prefill", "decode"):
            item = shape[phase]
            change = [100 * (v - 1) for v in (item["ratio"], *item["ci95"])]
            lines.append(f'| {shape["prefill"]["tokens_per_request"]} | {phase} | '
                         f'{item["main_tok_s"]:.1f} | {item["candidate_tok_s"]:.1f} | '
                         f'{change[0]:+.1f}% [{change[1]:+.1f}%, {change[2]:+.1f}%] | '
                         f'{item["noise_pct"]:.1f}% | {item["verdict"]} |')
    lines.extend(["", "Uncalibrated A/A results make A/B inconclusive. See ci/README.md for the design and limitations.", ""])
    report = "\n".join(lines)
    (output / f'{row["id"]}-comparison.md').write_text(report, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(report)
