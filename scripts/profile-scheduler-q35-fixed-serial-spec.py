#!/usr/bin/env python3
"""Seal the Qwen3.5 fixed-Metal serial prompt control on the existing pins."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = Path("target/scheduler-fixes")
    build = root / "artifacts/q4-metal-components-v2"
    serving = root / "artifacts/q4-serving-retest-v2"
    loaded = root / "artifacts/q35-c128-fixed"
    if not json.loads((build / "audit.json").read_text())["pass"]:
        raise ValueError("component audit missing")
    if not json.loads((serving / "audit.json").read_text())["controls"]["combined-fixed"]["all68_equal_fresh_serial"]:
        raise ValueError("fixed serving control missing")
    if len(json.loads((loaded / "audit.json").read_text())["exact_output_mismatches"]) != 12:
        raise ValueError("loaded mismatch fixture drift")
    select = runpy.run_path("scripts/profile-scheduler-suite-spec.py")["selected_source"]
    files = {
        "scripts/profile-scheduler-q35-fixed-serial.py": Path("scripts/profile-scheduler-q35-fixed-serial.py").read_text(),
        "scripts/profile-scheduler-serving-bundle.py": Path("scripts/profile-scheduler-serving-bundle.py").read_text(),
        "scripts/profile-scheduler-native-probe.py": select("scripts/profile-scheduler-native-probe.py", {"digest"}),
        "scripts/profile-scheduler-q4-retest.py": select("scripts/profile-scheduler-q4-retest.py", {"component", "checked"}),
        "scripts/lib/profile_scheduler_suite.py": select("scripts/lib/profile_scheduler_suite.py", {"nominal", "resolved"}),
        "scripts/lib/profile_kv_host.py": select("scripts/lib/profile_kv_host.py", {
            "HostUnavailable", "thermal_pressure", "save", "clean_env", "command", "telemetry",
            "competing_work", "require_idle", "stop", "launch_args", "server", "post", "body"}),
        "scripts/profile-kv-scheduler.py": select("scripts/profile-kv-scheduler.py", {"digest", "tokenize", "request", "wave"}),
        "component-build.json": (build / "metal-components.json").read_text(),
        "combined-build.json": (root / "artifacts/combined-logprob-build/scheduler-fix-build.json").read_text(),
    }
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())["qwen35-4b"]
    files["ci/inference-artifacts.json"] = json.dumps({"qwen35-4b": pin})
    source_manifest = json.loads((build / "fixed-manifest.json").read_text())
    view_bytes = runpy.run_path("scripts/profile-scheduler-serving-bundle.py")["manifest_bytes"](source_manifest)
    prior = json.loads((serving / "q4-serving.json").read_text())
    inputs = {
        "files": {name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()},
        "original_manifest_sha256": "c061fe7e9a78935354e9694676b64b0ef6c0ecdfd772dfeda09f4fe75df6e481",
        "original_binary_sha256": prior["arms"]["native-fixed"]["binary_sha256"],
        "fixture_sha256": "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e",
        "serving_manifest_sha256": hashlib.sha256(view_bytes).hexdigest(),
        "component_audit_sha256": hashlib.sha256((build / "audit.json").read_bytes()).hexdigest(),
        "serving_audit_sha256": hashlib.sha256((serving / "audit.json").read_bytes()).hexdigest(),
        "loaded_audit_sha256": hashlib.sha256((loaded / "audit.json").read_bytes()).hexdigest(),
    }
    files["q35-fixed-serial-inputs.json"] = json.dumps(inputs, indent=2) + "\n"
    for name, data in files.items():
        if name.endswith(".py"):
            compile(data, name, "exec")
    seal = runpy.run_path("scripts/profile-scheduler-q4-retest-spec.py")["seal"]
    seal(files, "671c6ebbf7661b236ce44d352024c51741b08f9c", args.out,
         "scripts/profile-scheduler-q35-fixed-serial.py", "q35-fixed-serial",
         "Qwen3.5 fixed Metal fresh serial prompt controls", 8, "q35-fixed-serial-inputs.json")
    spec = json.loads(args.out.read_text())
    spec["spec"]["harness"].update(request_count=8, concurrency=[1], warmup_requests=0)
    args.out.write_text(json.dumps(spec, indent=2) + "\n")
    print("final", hashlib.sha256(args.out.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
