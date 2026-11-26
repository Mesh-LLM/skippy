#!/usr/bin/env python3
"""Seal current-candidate serial correctness replays for exact CI model pins."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy


MODELS = {
    "qwen36": ("qwen3.6-35b-a3b-q4-k-m", "qwen36-35b-a3b",
               "target/scheduler-four-models/artifacts/admission-partial/model-admission.json",
               "target/scheduler-four-models/artifacts/qwen36-r0/suite.json"),
    "flash": ("qwen3.8-flash-next-iq4-xs", "qwen38-flash-next",
              "target/scheduler-four-models/artifacts/flash-admission/model-admission.json",
              "target/scheduler-four-models/artifacts/flash-screen-r0/suite.json"),
    "coder": ("qwen3-coder-30b-a3b-q4-k-m", "qwen3-coder-30b-a3b",
              "target/scheduler-four-models/artifacts/admission-partial/model-admission.json",
              "target/scheduler-four-models/artifacts/coder30b-r0/suite.json"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=tuple(MODELS), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--screen", action="store_true")
    args = parser.parse_args()
    phase = args.out.name.removesuffix(".spec.json")
    allowed = (("coder-current-screen-v1",) if args.screen else
               (f"{args.model}-current-correctness",
                f"{args.model}-current-correctness-v2",
                f"{args.model}-current-correctness-v3"))
    if phase not in allowed or (args.screen and args.model != "coder"):
        raise ValueError("unregistered correctness spec phase")
    key, pin_key, admission_path, prior_path = MODELS[args.model]
    root = Path("target/scheduler-fixes")
    component_root = root / "artifacts/q4-metal-components-v2"
    if not json.loads((component_root / "audit.json").read_text())["pass"]:
        raise ValueError("fixed native component audit missing")
    combined_audit = json.loads((root / "artifacts/combined-logprob-retest/audit.json").read_text())
    if (combined_audit["responses_verified"] != 44 or
            combined_audit["fixed_responses_verified"] != 24 or
            combined_audit["expected_baseline_errors"] != 4 or
            not all(c["text_exact_across_modes_repeats_batches"] for c in combined_audit["controls"])):
        raise ValueError("combined server audit missing")
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())[pin_key]
    admission = json.loads(Path(admission_path).read_text())
    admitted = next(m for m in admission["models"] if m["key"] == key)
    if not admitted["verified"] or admitted["repo"] != pin["repo"] or admitted["revision"] != pin["revision"]:
        raise ValueError("exact model not admitted")
    if [(f["sha256"], f["expected_bytes"]) for f in admitted["files"]] != [
        (f["sha256"], f["size"]) for f in pin["files"]
    ]:
        raise ValueError("admitted shards differ from CI pin")
    prior = json.loads(Path(prior_path).read_text())
    if prior["identity"]["source_sha"] != "671c6ebbf7661b236ce44d352024c51741b08f9c":
        raise ValueError("original suite source changed")
    selector = runpy.run_path("scripts/profile-scheduler-suite-spec.py")["selected_source"]
    scripts = ("scripts/profile-scheduler-current-correctness.py",
               "scripts/profile-scheduler-correctness.py",
               "scripts/profile-scheduler-serving-bundle.py")
    files = {name: Path(name).read_text() for name in scripts}
    if args.screen:
        files["scripts/profile-scheduler-suite.py"] = Path("scripts/profile-scheduler-suite.py").read_text()
    files["scripts/lib/profile_scheduler_suite.py"] = Path("scripts/lib/profile_scheduler_suite.py").read_text()
    files["scripts/lib/profile_kv_host.py"] = Path("scripts/lib/profile_kv_host.py").read_text()
    files["scripts/profile-kv-scheduler.py"] = selector(
        "scripts/profile-kv-scheduler.py", {"digest", "tokenize", "fixtures", "request", "wave"})
    files["scripts/profile-kv-scheduler-qualification.py"] = selector(
        "scripts/profile-kv-scheduler-qualification.py", {"file_digest", "qualified_arms"})
    files["scripts/profile-scheduler-native-probe.py"] = selector(
        "scripts/profile-scheduler-native-probe.py", {"digest"})
    files["scripts/profile-scheduler-q4-retest.py"] = selector(
        "scripts/profile-scheduler-q4-retest.py", {"component", "checked"})
    files["component-build.json"] = (component_root / "metal-components.json").read_text()
    files["combined-build.json"] = (root / "artifacts/combined-logprob-build/scheduler-fix-build.json").read_text()
    files["ci/inference-artifacts.json"] = json.dumps({pin_key: pin})
    files["admission.json"] = json.dumps(admission)
    source_manifest = json.loads((component_root / "fixed-manifest.json").read_text())
    serving_bytes = runpy.run_path("scripts/profile-scheduler-serving-bundle.py")["manifest_bytes"](source_manifest)
    provenance = {
        "model": key, "pin_key": pin_key,
        "files": {name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()},
        "fixture_sha256": hashlib.sha256(
            Path(prior_path).with_name("fixtures.json").read_bytes()).hexdigest(),
        "admission_sha256": hashlib.sha256(Path(admission_path).read_bytes()).hexdigest(),
        "original_manifest_sha256": prior["identity"]["files"]["native-runtimes/skippy-native-runtime-darwin-aarch64-metal/manifest.json"],
        "original_binary_sha256": prior["identity"]["files"]["skippy"],
        "vanilla_binary_sha256": prior["identity"]["files"]["llama-server"],
        "serving_manifest_sha256": hashlib.sha256(serving_bytes).hexdigest(),
        "combined_patch_sha256": json.loads(files["combined-build.json"])["patch_sha256"],
        "component_audit_sha256": hashlib.sha256((component_root / "audit.json").read_bytes()).hexdigest(),
        "combined_audit_sha256": hashlib.sha256((root / "artifacts/combined-logprob-retest/audit.json").read_bytes()).hexdigest(),
        "prior_suite_sha256": hashlib.sha256(Path(prior_path).read_bytes()).hexdigest(),
    }
    if args.screen:
        selected = root / "artifacts/coder-current-correctness-v3/audit.json"
        selected_audit = json.loads(selected.read_text())
        if (not selected_audit["selected_exact_pass"] or
                selected_audit["responses_verified"] != 64 or
                selected_audit["job_spec_sha256"] != hashlib.sha256(
                    (root / "coder-current-correctness-v3.spec.json").read_bytes()).hexdigest()):
            raise ValueError("current Coder serial screen not qualified")
        provenance["selected_screen_audit_sha256"] = hashlib.sha256(selected.read_bytes()).hexdigest()
    files["current-correctness-inputs.json"] = json.dumps(provenance, indent=2) + "\n"
    for name, data in files.items():
        if name.endswith(".py"):
            compile(data, name, "exec")
    seal = runpy.run_path("scripts/profile-scheduler-q4-retest-spec.py")["seal"]
    seal(files, "671c6ebbf7661b236ce44d352024c51741b08f9c", args.out,
         "scripts/profile-scheduler-current-correctness.py --screen" if args.screen else
         "scripts/profile-scheduler-current-correctness.py", phase,
         "Coder combined Skippy four-round balanced screen" if args.screen else
         f"{args.model} combined Skippy serial correctness replay", 192 if args.screen else 64,
         "current-correctness-inputs.json")
    spec = json.loads(args.out.read_text())
    spec["spec"]["budget"]["max_duration_s"] = 21600 if args.screen else 10800
    spec["spec"]["harness"].update(request_count=192 if args.screen else 64,
                                     concurrency=[1, 4] if args.screen else [1],
                                     warmup_requests=16 if args.screen else 0)
    args.out.write_text(json.dumps(spec, indent=2) + "\n")
    print("final", hashlib.sha256(args.out.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
