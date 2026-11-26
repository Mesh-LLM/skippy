#!/usr/bin/env python3
"""Record the unchanged-vanilla Qwen3.5 probe jobs and $0 occupancy."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path("target/scheduler-fixes")
PREFIX = "q35-vanilla-concurrency"


def time_value(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-0", required=True)
    parser.add_argument("--stage-1")
    parser.add_argument("--probe")
    args = parser.parse_args()
    job_ids = {"stage-0": args.stage_0, "stage-1": args.stage_1, "probe": args.probe}
    ledger_path = ROOT / "campaign.json"
    ledger = json.loads(ledger_path.read_text())
    now = datetime.now(timezone.utc)
    active_seconds = 0.0
    for phase, job_id in job_ids.items():
        if job_id is None:
            continue
        spec_path = ROOT / (f"{PREFIX}.spec.json" if phase == "probe" else
                            f"{PREFIX}.spec.{phase}.json")
        spec = json.loads(spec_path.read_text())["spec"]
        sha = hashlib.sha256(spec_path.read_bytes()).hexdigest()
        live = json.loads(subprocess.check_output(["jy", "job", job_id], text=True))
        if live["status"] not in ("queued", "claimed", "running", "succeeded", "failed", "cancelled"):
            raise ValueError("unknown JY status")
        key = f"{PREFIX}-{phase}"
        matches = [row for row in ledger["jobs"] if row["phase"] == key]
        if len(matches) > 1:
            raise ValueError("duplicate ledger phase")
        row = matches[0] if matches else {"phase": key}
        if (row.get("job_id", job_id) != job_id or row.get("sha256", sha) != sha):
            raise ValueError("job or spec identity drift")
        row.update(job_id=job_id, spec=str(spec_path), sha256=sha, status=live["status"],
                   runs=live["runs"], lease_s=spec["budget"]["max_duration_s"],
                   budget_max_usd=spec["budget"]["max_usd"], cost_usd=0)
        completed_seconds = 0.0
        for run in live["runs"]:
            if run.get("started_at") is None:
                continue
            start = time_value(run["started_at"])
            end = time_value(run["ended_at"]) if run.get("ended_at") else now
            duration = (end - start).total_seconds()
            if duration < 0:
                raise ValueError("negative occupancy")
            if run.get("ended_at"):
                completed_seconds += duration
            else:
                active_seconds += duration
        if live["status"] in ("succeeded", "failed", "cancelled") and not row.get("occupancy_accounted"):
            row["occupancy_new_hours"] = completed_seconds / 3600
            ledger["completed_occupancy_hours"] += row["occupancy_new_hours"]
            row["occupancy_accounted"] = True
        if not matches:
            ledger["jobs"].append(row)
    transport = json.loads((ROOT / f"{PREFIX}.spec.transport.json").read_text())
    for index, expected in enumerate(transport["chunks"]):
        receipt = ROOT / f"artifacts/{PREFIX}/stage-{index}/stage.json"
        if receipt.exists():
            if json.loads(receipt.read_text()) != expected:
                raise ValueError("stage receipt mismatch")
            row = next(row for row in ledger["jobs"] if row["phase"] == f"{PREFIX}-stage-{index}")
            row["stage_receipt_sha256"] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    artifacts = ROOT / f"artifacts/{PREFIX}"
    audit = artifacts / "audit.json"
    if audit.exists():
        evidence = json.loads(audit.read_text())
        row = next(row for row in ledger["jobs"] if row["phase"] == f"{PREFIX}-probe")
        if evidence["job_id"] != row["job_id"] or evidence["spec_sha256"] != row["sha256"]:
            raise ValueError("audit job/spec identity mismatch")
        row["artifacts"] = str(artifacts)
        row["audit"] = str(audit)
        row["artifact_sha256"] = {
            name: hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
            for name in ("vanilla-concurrency.json", "vanilla-concurrency-evidence.tar.gz", "audit.json")}
        row["measurement_disposition"] = evidence["disposition"]
    ledger["active_elapsed_hours"] = active_seconds / 3600
    ledger["status"] = ("q35_vanilla_concurrency_active" if any(
        row["phase"].startswith(PREFIX + "-") and row["status"] in ("queued", "claimed", "running")
        for row in ledger["jobs"]) else "completed_with_unresolved_correctness_and_slo_gates")
    ledger["last_checked_at"] = now.isoformat()
    ledger["updated_at"] = now.isoformat()
    ledger["next_action"] = "Audit unchanged vanilla load-0100 c1/c16 text distributions against serial/c128 controls."
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print(ledger["status"], "completed occupancy hours", round(ledger["completed_occupancy_hours"], 5))


if __name__ == "__main__":
    main()
