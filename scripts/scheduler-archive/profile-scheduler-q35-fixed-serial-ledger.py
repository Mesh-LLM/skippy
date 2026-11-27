#!/usr/bin/env python3
"""Refresh the three sealed Qwen3.5 serial-control jobs in the fix ledger."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path("target/scheduler-fixes")
PHASES = ("stage-0", "stage-1", "probe")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-0", required=True)
    parser.add_argument("--stage-1", required=True)
    parser.add_argument("--probe")
    args = parser.parse_args()
    ledger_path = ROOT / "campaign.json"
    ledger = json.loads(ledger_path.read_text())
    ids = {"stage-0": args.stage_0, "stage-1": args.stage_1, "probe": args.probe}
    now = datetime.now(timezone.utc)
    active_seconds = 0.0
    for phase in PHASES:
        job_id = ids[phase]
        if job_id is None:
            continue
        path = ROOT / ("q35-fixed-serial.spec.json" if phase == "probe" else
                       f"q35-fixed-serial.spec.{phase}.json")
        spec = json.loads(path.read_text())["spec"]
        live = json.loads(subprocess.check_output(["jy", "job", job_id], text=True))
        if live["status"] not in ("queued", "claimed", "running", "succeeded", "failed", "cancelled"):
            raise ValueError("unknown JY status")
        name = "q35-fixed-serial-" + phase
        matches = [row for row in ledger["jobs"] if row["phase"] == name]
        if len(matches) > 1:
            raise ValueError("duplicate ledger phase")
        row = matches[0] if matches else {"phase": name}
        if "job_id" in row and row["job_id"] != job_id:
            raise ValueError("ledger job identity drift")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if "sha256" in row and row["sha256"] != sha:
            raise ValueError("spec identity drift")
        row.update(job_id=job_id, spec=str(path), sha256=sha, status=live["status"],
                   runs=live["runs"], lease_s=spec["budget"]["max_duration_s"],
                   budget_max_usd=spec["budget"]["max_usd"], cost_usd=0)
        for run in live["runs"]:
            if run.get("started_at") is None:
                continue
            start = timestamp(run["started_at"])
            end = timestamp(run["ended_at"]) if run.get("ended_at") else now
            seconds = (end - start).total_seconds()
            if seconds < 0:
                raise ValueError("negative occupancy")
            if run.get("ended_at"):
                if not row.get("occupancy_accounted"):
                    row["occupancy_new_hours"] = row.get("occupancy_new_hours", 0) + seconds / 3600
            else:
                active_seconds += seconds
        if live["status"] in ("succeeded", "failed", "cancelled") and not row.get("occupancy_accounted"):
            ledger["completed_occupancy_hours"] += row.get("occupancy_new_hours", 0)
            row["occupancy_accounted"] = True
        if not matches:
            ledger["jobs"].append(row)
    transport = json.loads((ROOT / "q35-fixed-serial.spec.transport.json").read_text())
    for index, expected in enumerate(transport["chunks"]):
        receipt = ROOT / f"artifacts/q35-fixed-serial/stage-{index}/stage.json"
        if receipt.exists():
            if json.loads(receipt.read_text()) != expected:
                raise ValueError(f"staging receipt mismatch: {index}")
            row = next(row for row in ledger["jobs"] if row["phase"] == f"q35-fixed-serial-stage-{index}")
            row["stage_receipt_sha256"] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    artifacts = ROOT / "artifacts/q35-fixed-serial"
    audit = artifacts / "audit.json"
    if audit.exists():
        evidence = json.loads(audit.read_text())
        row = next(row for row in ledger["jobs"] if row["phase"] == "q35-fixed-serial-probe")
        if (evidence["job_id"] != row["job_id"] or evidence["spec_sha256"] != row["sha256"]
                or evidence["response_count"] != 8):
            raise ValueError("probe audit identity mismatch")
        row["artifacts"] = str(artifacts)
        row["audit"] = str(audit)
        row["artifact_sha256"] = {
            name: hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
            for name in ("q35-fixed-serial.json", "q35-fixed-serial-evidence.tar.gz", "audit.json")}
        row["measurement_disposition"] = (
            "Eight fresh fixed-Metal serial responses audited; both Skippy identities exact "
            "across repeats and match archived fixed-Metal c128 on load-0100/load-0190. "
            "Twelve cross-engine c128 mismatches remain failed; no timing clearance.")
    ledger["active_elapsed_hours"] = active_seconds / 3600
    ledger["status"] = ("q35_fixed_serial_active" if any(
        row["phase"].startswith("q35-fixed-serial-") and row["status"] in ("queued", "claimed", "running")
        for row in ledger["jobs"]) else "completed_with_unresolved_correctness_and_slo_gates")
    ledger["last_checked_at"] = now.isoformat()
    ledger["updated_at"] = now.isoformat()
    ledger["next_action"] = "Audit fixed-Metal load-0100 and load-0190 serial outputs against archived c128 and prior controls."
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print(ledger["status"], "completed occupancy hours", round(ledger["completed_occupancy_hours"], 5))


if __name__ == "__main__":
    main()
