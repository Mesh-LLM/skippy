#!/usr/bin/env python3
"""Record sealed Qwen3.6/Flash correctness jobs and $0 host occupancy."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path("target/scheduler-fixes")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("qwen36", "flash", "coder"), required=True)
    parser.add_argument("--stem")
    args = parser.parse_args()
    stem = args.stem or f"{args.model}-current-correctness"
    if stem not in (f"{args.model}-current-correctness",
                    f"{args.model}-current-correctness-v2",
                    f"{args.model}-current-correctness-v3"):
        raise ValueError("unregistered correctness ledger stem")
    transport = json.loads((ROOT / f"{stem}.spec.transport.json").read_text())
    specs = [Path(path) for path in transport["stage_specs"]] + [ROOT / f"{stem}.spec.json"]
    ledger_path = ROOT / "campaign.json"
    ledger = json.loads(ledger_path.read_text())
    now = datetime.now(timezone.utc)
    for index, path in enumerate(specs):
        label = f"stage-{index}" if index < len(specs) - 1 else "probe"
        receipt = ROOT / f"{stem}.{label}.submission.txt"
        if not receipt.exists():
            continue
        match = re.search(r"\bjob_[a-z0-9]+\b", receipt.read_text())
        if not match:
            raise ValueError("job id missing from submission receipt: " + str(receipt))
        job_id = match.group()
        live = json.loads(subprocess.check_output(["jy", "job", job_id], text=True))
        if live["status"] not in ("queued", "claimed", "running", "succeeded", "failed", "cancelled"):
            raise ValueError("unknown JY state: " + live["status"])
        spec = json.loads(path.read_text())["spec"]
        if spec["budget"]["max_usd"] != 0:
            raise ValueError("job cost cap changed")
        phase = f"{stem}-{label}"
        rows = [row for row in ledger["jobs"] if row["phase"] == phase]
        if len(rows) > 1:
            raise ValueError("duplicate phase")
        row = rows[0] if rows else {"phase": phase}
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if (row.get("job_id", job_id) != job_id or row.get("sha256", sha) != sha):
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
        if not rows:
            ledger["jobs"].append(row)
    audit_path = ROOT / "artifacts" / stem / "audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if audit["job_spec_sha256"] != hashlib.sha256(specs[-1].read_bytes()).hexdigest():
            raise ValueError("correctness audit/spec identity drift")
        if audit["responses_verified"] != 64 or audit["fresh_processes"] != 8:
            raise ValueError("incomplete current-candidate audit")
        probe = next(row for row in ledger["jobs"] if row["phase"] == f"{stem}-probe")
        probe.update(artifacts=str(audit_path.parent), audit=str(audit_path),
                     audit_sha256=hashlib.sha256(audit_path.read_bytes()).hexdigest(),
                     responses_verified=64,
                     measurement_disposition=(
                         "Selected serial exact-output PASS; no concurrent or balanced-timing clearance."
                         if audit.get("selected_exact_pass", audit["exact_cross_engine_pass"]) else
                         "Selected serial exact-output FAIL; timing promotion blocked."))
        model_key = {"qwen36": "qwen3.6", "flash": "flash", "coder": "coder"}[args.model]
        verdict = "PASS" if audit.get("selected_exact_pass", audit["exact_cross_engine_pass"]) else "FAIL"
        note = (
            f"Current-candidate 64-response serial replay audited: exact cross-engine {verdict}; "
            "selected prompts only, with prior failures retained. "
            "No concurrent or balanced-timing clearance from this screen."
        )
        historical, marker, tail = ledger["models"][model_key].partition(
            "Current-candidate 64-response serial replay audited:")
        if marker:
            _, ending, extra = tail.partition("No concurrent or balanced-timing clearance from this screen.")
            if not ending:
                raise ValueError("unexpected existing model audit note")
            historical = historical.rstrip() + " " + extra.strip()
        if model_key == "qwen3.6" and not historical.strip():
            historical = ("Admission and logprob chunk alignment fixes qualified in isolated "
                          "and combined candidates. Original cross-engine differences remain "
                          "unresolved; the original failures are preserved in the campaign. ")
        ledger["models"][model_key] = historical.rstrip() + " " + note
    active = ("qwen36-current-correctness", "flash-current-correctness",
              "coder-current-correctness",
              "qwen36-composition-isolation")
    active_seconds = sum((now - timestamp(run["started_at"])).total_seconds()
                         for row in ledger["jobs"]
                         if row["phase"].startswith(active)
                         and row["status"] in ("claimed", "running")
                         for run in row["runs"] if run.get("started_at") and not run.get("ended_at"))
    ledger["active_elapsed_hours"] = active_seconds / 3600
    ledger["status"] = ("current_correctness_active" if any(
        row["phase"].startswith(active)
        and row["status"] in ("queued", "claimed", "running") for row in ledger["jobs"])
        else "completed_with_unresolved_correctness_and_slo_gates")
    ledger["last_checked_at"] = now.isoformat()
    ledger["updated_at"] = now.isoformat()
    ledger["next_action"] = (
        "Audit active current-candidate serial jobs before timing promotion."
        if ledger["status"] == "current_correctness_active" else
        "Coder selected serial screen passes and may advance to a balanced current-candidate screen. "
        "Qwen3.5, Qwen3.6 and Flash retain exact-output failures; a measured fix or separately "
        "registered quality policy is needed before a four-model timing claim."
    )
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    print(ledger["status"], "completed occupancy hours", round(ledger["completed_occupancy_hours"], 5))


if __name__ == "__main__":
    main()
