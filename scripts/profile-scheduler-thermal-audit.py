#!/usr/bin/env python3
"""Correlate saved JY thermal alerts with client and server wall-clock windows."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def audit(artifacts, ledger, job_id):
    campaign = json.loads(ledger.read_text())
    job = next(row for row in campaign["jobs"] if row["job_id"] == job_id)
    if Path(job["artifacts"]).resolve() != artifacts.resolve():
        raise ValueError("artifact directory does not match registered job")
    record = json.loads((artifacts / "suite.json").read_text())
    windows = []
    for cell in record["cells"]:
        directory = artifacts / "evidence" / Path(cell["directory"]).name
        if not cell.get("complete"):
            raise ValueError("cannot clear thermal observations against incomplete cells")
        evidence_id = cell["benchmark"]["data"]["measurement"]
        if not evidence_id.startswith("ev_") or "/" in evidence_id:
            raise ValueError("invalid measurement identity")
        evidence = json.loads((directory / "evidence" / evidence_id / "evidence.json").read_text())
        if evidence["evidence_id"] != evidence_id:
            raise ValueError("measurement identity mismatch")
        started, ended = [instant(evidence["provenance"][field]) for field in ("started", "ended")]
        before, after = [json.loads((directory / f"{name}.json").read_text())["wall_ns"] / 1e9
                         for name in ("before", "after")]
        if not before <= started < ended <= after:
            raise ValueError("benchmark outside server boundary timestamps")
        windows.append({"round": cell["round"], "arm": cell["arm"], "evidence_id": evidence_id,
                        "benchmark_start": started, "benchmark_end": ended,
                        "server_start": before, "server_end": after})
    observations = []
    for row in campaign.get("host_gate_observations", []):
        if row["job"] != job_id:
            continue
        telemetry = row["telemetry"]
        observed = instant(telemetry["at"])
        alert = telemetry.get("throttled") is True or telemetry.get("thermal") not in (None, "nominal")
        # JY timestamps are coarse. Treat a one-second overlap as ambiguous,
        # rather than clearing an alert right at a measurement boundary.
        timed = [w for w in windows if w["benchmark_start"] - 1 <= observed <= w["benchmark_end"] + 1]
        resident = [w for w in windows if w["server_start"] - 1 <= observed <= w["server_end"] + 1]
        label = lambda w: {k: w[k] for k in ("round", "arm", "evidence_id")}
        observations.append({"at": telemetry["at"], "thermal": telemetry.get("thermal"),
            "throttled": telemetry.get("throttled"), "alert": alert,
            "benchmark_overlaps": [label(w) for w in timed],
            "server_lifetime_overlaps": [label(w) for w in resident]})
    overlaps = [o for o in observations if o["alert"] and o["benchmark_overlaps"]]
    return {"job_id": job_id, "cells_checked": len(windows), "observations": observations,
            "timing_alert_overlap": bool(overlaps),
            "disposition": "HOLD: thermal alert overlaps a client window" if overlaps else
                "Saved JY alerts do not overlap the recorded client windows",
            "ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
            "scope": "Coarse JY observations only; absence of overlap is not proof of continuous nominal "
                     "conditions. Native request guards and boundaries must independently pass. "
                     "One-second boundary uncertainty is included; no timing or correctness gate is relaxed."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.ledger, args.job)
    (args.artifacts / "thermal-observations-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(result["disposition"])
