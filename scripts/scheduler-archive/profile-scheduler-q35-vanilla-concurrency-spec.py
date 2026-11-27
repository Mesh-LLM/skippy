#!/usr/bin/env python3
"""Seal the unchanged-vanilla Qwen3.5 identical-peer concurrency probe."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    loaded = Path("target/scheduler-fixes/artifacts/q35-c128-fixed")
    serial = Path("target/scheduler-fixes/artifacts/q35-fixed-serial")
    old = json.loads((loaded / "suite.json").read_text())
    if old["identity"]["source_sha"] != "671c6ebbf7661b236ce44d352024c51741b08f9c":
        raise ValueError("source identity drift")
    if json.loads((serial / "audit.json").read_text())["response_count"] != 8:
        raise ValueError("fixed serial audit missing")
    select = runpy.run_path("scripts/profile-scheduler-suite-spec.py")["selected_source"]
    files = {
        "scripts/profile-scheduler-q35-vanilla-concurrency.py":
            Path("scripts/profile-scheduler-q35-vanilla-concurrency.py").read_text(),
        "scripts/lib/profile_scheduler_suite.py": Path("scripts/lib/profile_scheduler_suite.py").read_text(),
        "scripts/lib/profile_kv_host.py": Path("scripts/lib/profile_kv_host.py").read_text(),
        "scripts/profile-kv-scheduler.py": select("scripts/profile-kv-scheduler.py", {
            "digest", "tokenize", "fixtures", "request", "wave"}),
        "scripts/profile-kv-scheduler-qualification.py": select(
            "scripts/profile-kv-scheduler-qualification.py", {"file_digest", "qualified_arms"}),
        "ci/inference-artifacts.json": json.dumps({"qwen35-4b":
            json.loads(Path("ci/inference-artifacts.json").read_text())["qwen35-4b"]}),
    }
    provenance = {
        "files": {name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()},
        "fixture_sha256": "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e",
        "vanilla_binary_sha256": old["identity"]["files"]["llama-server"],
        "loaded_audit_sha256": hashlib.sha256((loaded / "audit.json").read_bytes()).hexdigest(),
        "fixed_serial_audit_sha256": hashlib.sha256((serial / "audit.json").read_bytes()).hexdigest(),
    }
    files["q35-vanilla-concurrency-inputs.json"] = json.dumps(provenance, indent=2) + "\n"
    for name, data in files.items():
        if name.endswith(".py"):
            compile(data, name, "exec")
    out = args.out
    runpy.run_path("scripts/profile-scheduler-q4-retest-spec.py")["seal"](
        files, "671c6ebbf7661b236ce44d352024c51741b08f9c", out,
        "scripts/profile-scheduler-q35-vanilla-concurrency.py", "q35-vanilla-concurrency",
        "Qwen3.5 unchanged vanilla identical-peer c1/c16 replay", 136,
        "q35-vanilla-concurrency-inputs.json")
    spec = json.loads(out.read_text())
    spec["spec"]["harness"].update(request_count=136, concurrency=[1, 16], warmup_requests=0)
    out.write_text(json.dumps(spec, indent=2) + "\n")
    print("final", hashlib.sha256(out.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
