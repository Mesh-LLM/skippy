"""Admit and independently summarize same-allocation native comparison evidence."""

import re
import os
from pathlib import Path

from .ci_artifacts import write_json
from .inference_pairs import summarize
from .inference_throughput import fetch_artifact, successful_run


def collect_pairs(pilot, job, row, candidate, baseline, output):
    run_id = successful_run(job, candidate)
    evidence = fetch_artifact(pilot, run_id, "paired-probe.json")
    summary = summarize(evidence, candidate, baseline, row["id"])
    placements = {p["role"]: p for p in evidence["placements"]}
    if placements["leader"]["hostname"] != job["machine"]["host"]["hostname"]:
        raise ValueError("comparison leader differs from reserved JY machine")
    if row["mode"] == "split":
        formation = pilot.api("GET", f'/v1/formations/{job["formation_id"]}')
        members = formation["members"]
        if (formation["status"] != "succeeded" or len(members) != 2
                or any(m["status"] != "succeeded" or m["node"] != placements[m["role"]]["node"]
                       for m in members)):
            raise ValueError("comparison does not match the successful JY formation")
    builds = evidence["builds"]
    if len(builds) != len(placements) or {b["role"] for b in builds} != set(placements):
        raise ValueError("comparison build provenance is incomplete")
    for build in builds:
        for name, sha in (("main", baseline), ("candidate", candidate)):
            identity = build[name]
            if identity["stack_commit"] != sha or not re.fullmatch(r"[0-9a-f]{64}", identity["cli_sha256"]):
                raise ValueError("comparison build provenance mismatch")
    receipt = {**evidence, "summary": summary, "job_id": job["id"], "run_id": run_id}
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
