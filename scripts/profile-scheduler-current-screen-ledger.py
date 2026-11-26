#!/usr/bin/env python3
"""Record sealed Coder balanced-screen jobs and $0 host occupancy."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path("target/scheduler-fixes")
STEM = "coder-current-screen-v1"


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
            raise ValueError("job ID missing: " + str(receipt))
        live = json.loads(subprocess.check_output(["jy", "job", match.group()], text=True))
        spec = json.loads(path.read_text())["spec"]
        if spec["budget"]["max_usd"] != 0 or spec["retries"] != 0:
            raise ValueError("cost/retry contract changed")
        phase = f"{STEM}-{label}"
        rows = [row for row in ledger["jobs"] if row["phase"] == phase]
        if len(rows) > 1:
            raise ValueError("duplicate phase")
        row = rows[0] if rows else {"phase": phase}
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if row.get("job_id", match.group()) != match.group() or row.get("sha256", sha) != sha:
            raise ValueError("job/spec identity drift")
        row.update(job_id=match.group(), spec=str(path), sha256=sha,
                   status=live["status"], runs=live["runs"],
                   lease_s=spec["budget"]["max_duration_s"], budget_max_usd=0,
                   cost_usd=0, submission_receipt=str(receipt),
                   submission_receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
        if live["status"] in ("succeeded", "failed", "cancelled") and not row.get("occupancy_accounted"):
            hours = sum((timestamp(run["ended_at"]) - timestamp(run["started_at"])).total_seconds()
                        for run in live["runs"] if run.get("started_at") and run.get("ended_at")) / 3600
            if hours < 0:
                raise ValueError("negative occupancy")
            row["occupancy_new_hours"] = hours
            ledger["completed_occupancy_hours"] += hours
            row["occupancy_accounted"] = True
        if not rows:
            ledger["jobs"].append(row)
    audit_path = ROOT / "artifacts" / STEM / "audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if audit["job_spec_sha256"] != hashlib.sha256(specs[-1].read_bytes()).hexdigest():
            raise ValueError("screen audit/spec identity drift")
        probe = next(row for row in ledger["jobs"] if row["phase"] == f"{STEM}-probe")
        probe.update(artifacts=str(audit_path.parent), audit=str(audit_path),
                     audit_sha256=hashlib.sha256(audit_path.read_bytes()).hexdigest(),
                     measurement_disposition=audit["measurement_disposition"])
        note = " Current-candidate four-round screen audited: " + audit["measurement_disposition"]
        if note not in ledger["models"]["coder"]:
            ledger["models"]["coder"] += note
    active = any(row["phase"].startswith(STEM) and row["status"] in
                 ("queued", "claimed", "running") for row in ledger["jobs"])
    ledger["status"] = ("current_coder_screen_active" if active else
                        "completed_with_unresolved_correctness_and_slo_gates")
    ledger["active_elapsed_hours"] = sum((now - timestamp(run["started_at"])).total_seconds()
                                         for row in ledger["jobs"] if row["phase"] == f"{STEM}-probe"
                                         and row["status"] in ("claimed", "running")
                                         for run in row["runs"] if run.get("started_at")
                                         and not run.get("ended_at")) / 3600
    ledger["last_checked_at"] = now.isoformat()
    ledger["updated_at"] = now.isoformat()
    probe_submitted = any(row["phase"] == f"{STEM}-probe" for row in ledger["jobs"])
    ledger["next_action"] = ("Fetch and verify all three sealed stage receipts, then submit the corrected 21,600-second Coder model spec."
                             if active and not probe_submitted else
                             "Audit the Coder current-candidate balanced screen after completion."
                             if active else "Review the Coder screen and unresolved three-model exact-output gates.")
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print(ledger["status"], "completed occupancy hours", round(ledger["completed_occupancy_hours"], 5))


if __name__ == "__main__":
    main()
