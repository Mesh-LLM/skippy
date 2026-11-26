#!/usr/bin/env python3
"""Refresh the local JY campaign ledger and cumulative reserved-host time."""

import datetime
import json
import subprocess
from pathlib import Path


def instant(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    path = Path("evals/kv-profiling/campaign.json")
    ledger = json.loads(path.read_text())
    now = datetime.datetime.now(datetime.timezone.utc)
    occupied = 0.0
    for row in ledger["jobs"]:
        job = json.loads(subprocess.check_output(["jy", "job", row["job_id"]], text=True))
        row.update(status=job["status"], status_detail=job.get("status_detail"))
        row["runs"] = []
        for run in job.get("runs", []):
            selected = {key: run.get(key) for key in ("id", "status", "claimed_at", "started_at", "ended_at", "error")}
            row["runs"].append(selected)
            start = run.get("claimed_at") or run.get("started_at")
            if start:
                end = instant(run["ended_at"]) if run.get("ended_at") else now
                occupied += max(0, (end - instant(start)).total_seconds())
        if row["runs"]:
            latest = row["runs"][-1]
            row.update(run_id=latest["id"], started_at=latest["started_at"], ended_at=latest["ended_at"])
    ledger.update(last_checked_at=now.isoformat(), host_seconds_used_at_last_check=round(occupied, 3),
                  remaining_host_seconds_at_last_check=round(ledger["host_budget_seconds"] - occupied, 3))
    latest = ledger["jobs"][-1]
    ledger["status"] = latest["phase"] + "-" + latest["status"]
    path.write_text(json.dumps(ledger, indent=2) + "\n")
    print(ledger["status"], latest["job_id"], f"host budget remaining {ledger['remaining_host_seconds_at_last_check']:.0f}s")


if __name__ == "__main__":
    main()
