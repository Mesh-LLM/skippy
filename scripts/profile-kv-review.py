#!/usr/bin/env python3
"""Verify fetched inferencekit records and build local synthetic-output reviews."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


IK = ["uvx", "--offline", "--from", "inferencekit-cli==0.1.1", "inferencekit"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    state = tempfile.TemporaryDirectory(prefix="kv-review-")
    env = dict(os.environ, INFERENCEKIT_RUNS_DIR=state.name)
    records = []
    paths = sorted((args.root / "evidence").glob("*/evidence.json"))
    if not paths:
        raise ValueError("no fetched inferencekit evidence records")
    for path in paths:
        summary = json.loads(subprocess.check_output(
            IK + ["evidence", "show", str(path), "--view", "summary", "--json"], text=True, env=env))
        if not summary["integrity"]["id_ok"] or not summary["integrity"]["schema_ok"]:
            raise ValueError(f"evidence id/schema integrity failed: {path}")
        record = json.loads(path.read_text())
        artifacts = []
        for artifact in record["artifacts"]:
            local = path.parent / artifact["path"]
            digest = hashlib.sha256(local.read_bytes()).hexdigest()
            if digest != artifact["sha256"] or local.stat().st_size != artifact["bytes"]:
                raise ValueError(f"fetched record artifact digest/size mismatch: {local}")
            artifacts.append({"path": artifact["path"], "sha256": digest, "bytes": local.stat().st_size})
        records.append({"evidence_id": summary["evidence_id"], "integrity": summary["integrity"],
                        "coverage": summary["coverage"], "limitations": summary["limitations"],
                        "identity": summary["identity"], "artifacts_verified": artifacts})
    export = subprocess.check_output(IK + ["report"] + [str(p) for p in paths] +
                                     ["--format", "md", "--title", "Technical evidence export; quality unproven"], text=True, env=env)
    (args.output / "inferencekit-export.md").write_text(export)
    reviews = []
    for directory in sorted(args.root.glob("qwen*")):
        result = json.loads(subprocess.check_output(
            IK + ["review", "build", str(directory), "--reference", "llama", "--json"], text=True, env=env))
        destination = args.output / directory.name
        destination.mkdir(exist_ok=True)
        for name in ("index.html", "data.json"):
            shutil.copy2(directory / "review" / name, destination / name)
        reviews.append({"model": directory.name, "result": result,
                        "local_page": str(destination / "index.html")})
    (args.output / "audit.json").write_text(json.dumps({"records": records, "reviews": reviews,
        "scope": "Synthetic saved untagged outputs; no full quality proof. Technical exports aggregate extended rounds and do not replace the registered paired statistics.",
        "export_limitation": "inferencekit0.1.1 report.claims_of does not classify serve_benchmark records; its overall NOT RUN verdict is not a timing-coverage finding. Use the explicit audited campaign verdicts."}, indent=2) + "\n")
    state.cleanup()
    print(f"Verified {len(records)} evidence records/artifact hashes; built {len(reviews)} local review pages.")


if __name__ == "__main__":
    main()
