#!/usr/bin/env python3
"""Record the sealed Qwen3.6 composition jobs and $0 occupancy."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path("target/scheduler-fixes")
STEM = "qwen36-composition-isolation"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    transport = json.loads((ROOT / f"{STEM}.spec.transport.json").read_text())
    specs = [Path(path) for path in transport["stage_specs"]] + [ROOT / f"{STEM}.spec.json"]
    ledger_path = ROOT / "campaign.json"
    ledger = json.loads(ledger_path.read_text())
    now = datetime.now(timezone.utc)
    for index, path in enumerate(specs):
        label = f"stage-{index}" if index < len(specs) - 1 else "probe"
        receipt = ROOT / f"{STEM}.{label}.submission.txt"
        if not receipt.exists():
            continue
        match = re.search(r"\bjob_[a-z0-9]+\b", receipt.read_text())
        if not match:
            raise ValueError("job id missing from receipt: " + str(receipt))
        job_id = match.group()
        live = json.loads(subprocess.check_output(["jy", "job", job_id], text=True))
        if live["status"] not in ("queued", "claimed", "running", "succeeded", "failed", "cancelled"):
            raise ValueError("unknown JY state")
        spec = json.loads(path.read_text())["spec"]
        if spec["budget"]["max_usd"] != 0:
            raise ValueError("job cost cap changed")
        phase = f"{STEM}-{label}"
        matches = [row for row in ledger["jobs"] if row["phase"] == phase]
        if len(matches) > 1:
            raise ValueError("duplicate phase")
        row = matches[0] if matches else {"phase": phase}
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if row.get("job_id", job_id) != job_id or row.get("sha256", sha) != sha:
            raise ValueError("job/spec identity drift")
        row.update(job_id=job_id, spec=str(path), sha256=sha, status=live["status"],
                   runs=live["runs"], lease_s=spec["budget"]["max_duration_s"],
                   budget_max_usd=0, cost_usd=0, submission_receipt=str(receipt),
                   submission_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
        if live["status"] in ("succeeded", "failed", "cancelled") and not row.get("occupancy_accounted"):
            hours = sum((timestamp(run["ended_at"]) - timestamp(run["started_at"])).total_seconds()
                        for run in live["runs"] if run.get("started_at") and run.get("ended_at")) / 3600
            if hours < 0:
                raise ValueError("negative occupancy")
            row["occupancy_new_hours"] = hours
            ledger["completed_occupancy_hours"] += hours
            row["occupancy_accounted"] = True
        if not matches:
            ledger["jobs"].append(row)
    audit_path = ROOT / "artifacts" / STEM / "audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if not audit["passed_integrity_gates"] or audit["job_spec_sha256"] != hashlib.sha256(specs[-1].read_bytes()).hexdigest():
            raise ValueError("composition audit/spec identity drift")
        note = " Two missing server/Metal composition corners audited."
        if note not in ledger["models"]["qwen3.6"]:
            ledger["models"]["qwen3.6"] += note
    active = ("qwen36-current-correctness", "flash-current-correctness",
              "coder-current-correctness", STEM)
    ledger["active_elapsed_hours"] = sum(
        (now - timestamp(run["started_at"])).total_seconds()
        for row in ledger["jobs"] if row["phase"].startswith(active)
        and row["status"] in ("claimed", "running")
        for run in row["runs"] if run.get("started_at") and not run.get("ended_at")) / 3600
    ledger["status"] = ("current_correctness_active" if any(
        row["phase"].startswith(active) and row["status"] in ("queued", "claimed", "running")
        for row in ledger["jobs"]) else "completed_with_unresolved_correctness_and_slo_gates")
    ledger["last_checked_at"] = now.isoformat()
    ledger["updated_at"] = now.isoformat()
    ledger["next_action"] = "Audit Flash/Coder current-candidate serial replay and Qwen3.6 composition isolation before timing promotion."
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print("composition jobs recorded; completed occupancy hours", round(ledger["completed_occupancy_hours"], 5))


if __name__ == "__main__":
    main()
