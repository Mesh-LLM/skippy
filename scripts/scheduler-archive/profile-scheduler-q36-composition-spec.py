#!/usr/bin/env python3
"""Seal the two missing Qwen3.6 server/Metal composition corners."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = Path("target/scheduler-fixes")
    components = root / "artifacts/q4-metal-components-v2"
    component_audit = json.loads((components / "audit.json").read_text())
    combined_audit = json.loads((root / "artifacts/combined-logprob-retest/audit.json").read_text())
    if not component_audit["pass"] or combined_audit["responses_verified"] != 44 or \
            combined_audit["fixed_responses_verified"] != 24 or \
            combined_audit["expected_baseline_errors"] != 4:
        raise ValueError("qualified component/server prerequisite missing")
    prior = json.loads((root / "qwen36-current-correctness.spec.inputs.json").read_text())
    current_audit = json.loads((root / "artifacts/qwen36-current-correctness/audit.json").read_text())
    if current_audit["exact_cross_engine_pass"] or current_audit["responses_verified"] != 64:
        raise ValueError("registered Qwen3.6 failure not reproduced")
    select = runpy.run_path("scripts/profile-scheduler-suite-spec.py")["selected_source"]
    scripts = ("scripts/profile-scheduler-q36-composition.py",
               "scripts/profile-scheduler-correctness.py",
               "scripts/profile-scheduler-serving-bundle.py")
    files = {name: Path(name).read_text() for name in scripts}
    files["scripts/lib/profile_scheduler_suite.py"] = Path("scripts/lib/profile_scheduler_suite.py").read_text()
    files["scripts/lib/profile_kv_host.py"] = Path("scripts/lib/profile_kv_host.py").read_text()
    files["scripts/profile-kv-scheduler.py"] = select(
        "scripts/profile-kv-scheduler.py", {"digest", "tokenize", "request", "wave"})
    files["scripts/profile-kv-scheduler-qualification.py"] = select(
        "scripts/profile-kv-scheduler-qualification.py", {"file_digest", "qualified_arms"})
    files["scripts/profile-scheduler-native-probe.py"] = select(
        "scripts/profile-scheduler-native-probe.py", {"digest"})
    files["scripts/profile-scheduler-q4-retest.py"] = select(
        "scripts/profile-scheduler-q4-retest.py", {"component", "checked"})
    files["component-build.json"] = (components / "metal-components.json").read_text()
    files["combined-build.json"] = (root / "artifacts/combined-logprob-build/scheduler-fix-build.json").read_text()
    files["component-audit.json"] = (components / "audit.json").read_text()
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())["qwen36-35b-a3b"]
    files["ci/inference-artifacts.json"] = json.dumps({"qwen36-35b-a3b": pin})
    fixed = next(v for v in json.loads(files["component-build.json"])["variants"]
                 if v["composition"]["arm"] == "fixed")
    manifest_bytes = runpy.run_path("scripts/profile-scheduler-serving-bundle.py")["manifest_bytes"]
    fixed_manifest = json.loads((components / "fixed-manifest.json").read_text())
    inputs = {"model": "qwen3.6-35b-a3b-q4-k-m",
              "cases": {"short-0": 128, "long-2": 24},
              "files": {name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()},
              "fixture_sha256": prior["fixture_sha256"],
              "original_binary_sha256": prior["original_binary_sha256"],
              "original_manifest_sha256": prior["original_manifest_sha256"],
              "combined_binary_sha256": json.loads(files["combined-build.json"])["binary_sha256"],
              "fixed_manifest_sha256": fixed["manifest_sha256"],
              "fixed_serving_manifest_sha256": hashlib.sha256(manifest_bytes(fixed_manifest)).hexdigest(),
              "component_audit_sha256": digest(components / "audit.json"),
              "combined_audit_sha256": digest(root / "artifacts/combined-logprob-retest/audit.json"),
              "current_audit_sha256": digest(root / "artifacts/qwen36-current-correctness/audit.json")}
    if inputs["fixed_serving_manifest_sha256"] != prior["serving_manifest_sha256"]:
        raise ValueError("fixed serving manifest identity changed")
    files["composition-inputs.json"] = json.dumps(inputs, indent=2) + "\n"
    for name, data in files.items():
        if name.endswith(".py"):
            compile(data, name, "exec")
    seal = runpy.run_path("scripts/profile-scheduler-q4-retest-spec.py")["seal"]
    seal(files, "671c6ebbf7661b236ce44d352024c51741b08f9c", args.out,
         "scripts/profile-scheduler-q36-composition.py", "qwen36-composition-isolation",
         "Qwen3.6 original and fixed Metal component isolation", 32, "composition-inputs.json")
    spec = json.loads(args.out.read_text())
    spec["spec"]["harness"].update(request_count=32, concurrency=[1], warmup_requests=0)
    spec["spec"]["budget"]["max_duration_s"] = 3600
    args.out.write_text(json.dumps(spec, indent=2) + "\n")
    print("final", digest(args.out))


if __name__ == "__main__":
    main()
