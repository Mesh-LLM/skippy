#!/usr/bin/env python3
"""Audit the sealed current Coder screen and keep relative and SLO verdicts separate."""

import argparse
import base64
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import runpy
import tarfile

from lib import profile_scheduler_suite as suite


ROOT = Path("target/scheduler-fixes")
STEM = "coder-current-screen-v1"
PRIOR = Path("target/scheduler-four-models/artifacts/coder30b-r0")
ARMS = suite.ARMS
WAVE_COUNTS = {"serial-short-a": 1, "serial-short-b": 1,
               "serial-long": 1, "mixed-c4": 4}


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            parts = PurePosixPath(member.name)
            require(not parts.is_absolute() and ".." not in parts.parts,
                    "unsafe archive path")
            path = destination.joinpath(*parts.parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile() and path.suffix in
                        (".json", ".jsonl", ".log", ".stderr"),
                        "unsafe archive entry")
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as handle:
                    path.write_bytes(handle.read())


def sealed_inputs(artifact_root):
    transport = read(ROOT / f"{STEM}.spec.transport.json")
    chunks = []
    for index, expected in enumerate(transport["chunks"]):
        stage_spec = ROOT / f"{STEM}.spec.stage-{index}.json"
        command = read(stage_spec)["spec"]["runtime"]["command"]
        encoded = re.search(r"data=base64\.b85decode\('([^']+)'\)", command)
        require(encoded is not None, f"missing sealed chunk {index}")
        chunk = base64.b85decode(encoded.group(1))
        require(hashlib.sha256(chunk).hexdigest() == expected["sha256"],
                f"sealed chunk digest {index}")
        require(read(artifact_root / f"stage-{index}/stage.json") == expected,
                f"remote stage receipt {index}")
        chunks.append(chunk)
    payload = b"".join(chunks)
    require(hashlib.sha256(payload).hexdigest() == transport["payload_sha256"],
            "sealed payload digest")
    files = json.loads(lzma.decompress(payload))
    provenance = json.loads(files["current-correctness-inputs.json"])
    require(provenance == read(ROOT / f"{STEM}.spec.inputs.json"),
            "sealed provenance drift")
    for name, expected in provenance["files"].items():
        require(name in files and hashlib.sha256(files[name].encode()).hexdigest() == expected,
                f"sealed file drift: {name}")
    return provenance, files


def outputs(artifact_root, round_index, arm, report_cell):
    directory = artifact_root / "evidence" / f"r{round_index}-{arm}"
    saved = [read(path) for path in (directory / "outputs" / arm).rglob("*.json")
             if path.name != "index.json"]
    require(len(saved) == 4, f"inferencekit outputs: {round_index}/{arm}")
    rows = {("inferencekit", record["id"]):
            hashlib.sha256(record["text"].encode()).hexdigest() for record in saved}
    for wave_name, wave in report_cell["waves"].items():
        require(len(wave["requests"]) == WAVE_COUNTS[wave_name],
                f"wave count: {round_index}/{arm}/{wave_name}")
        rows.update({(wave_name, prompt): request["text_sha256"]
                     for prompt, request in wave["requests"].items()})
    return rows


def noninferiority(metric, interval):
    lower, upper = interval
    if metric == "output_tok_s_over_client_span":
        return "PASS" if lower > .98 else "FAIL" if upper < .98 else "UNPROVEN"
    return "PASS" if upper < 1.02 else "FAIL" if lower > 1.02 else "UNPROVEN"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path,
                        default=ROOT / "artifacts" / STEM)
    args = parser.parse_args()
    artifact_root = args.artifacts
    provenance, files = sealed_inputs(artifact_root)
    spec_path = ROOT / f"{STEM}.spec.json"
    spec = read(spec_path)["spec"]
    require(spec["budget"]["max_usd"] == 0 and
            spec["budget"]["max_duration_s"] == 21600 and
            spec["retries"] == 0 and
            spec["harness"]["request_count"] == 192,
            "registered budget/request contract")
    require(provenance["selected_screen_audit_sha256"] == digest(
        ROOT / "artifacts/coder-current-correctness-v3/audit.json"),
        "selected serial gate changed")
    extract(artifact_root / "suite-evidence.tar.gz", artifact_root / "evidence")
    report = read(artifact_root / "suite.json")
    fixture = read(artifact_root / "fixtures.json")
    prior = read(PRIOR / "suite.json")
    combined = read(ROOT / "artifacts/combined-logprob-build/scheduler-fix-build.json")
    composition = read(artifact_root / "current-composition.json")
    pin = read(Path("ci/inference-artifacts.json"))["qwen3-coder-30b-a3b"]
    require(pin == json.loads(files["ci/inference-artifacts.json"])["qwen3-coder-30b-a3b"] and
            composition["admission_sha256"] == provenance["admission_sha256"],
            "sealed CI pin/admission drift")
    build_root = ROOT / "artifacts/q4-metal-components-v2"
    native_build = read(build_root / "metal-components.json")
    fixed = next(v for v in native_build["variants"]
                 if v["composition"]["arm"] == "fixed")
    require(report["complete"] and "error" not in report, "incomplete screen")
    require(report["phase"] == "screen" and report["rounds"] == [0, 1, 2, 3]
            and report["lanes"] == 16 and report["cooldown_s"] == 120
            and report["prefix_cache"] is False
            and report["generation_queue_capacity"] is None,
            "registered screen settings")
    require(report["identity"] == prior["identity"] and
            digest(PRIOR / "suite.json") == provenance["prior_suite_sha256"],
            "original server/runtime identity")
    require(fixture == read(PRIOR / "fixtures.json") and
            digest(PRIOR / "fixtures.json") == provenance["fixture_sha256"],
            "original fixture drift")
    require(report["candidate"] == combined and
            combined["patch_sha256"] == provenance["combined_patch_sha256"] and
            composition["combined_binary"] == combined and
            composition["fixed_component"] == fixed and
            composition["serving_manifest_sha256"] == provenance["serving_manifest_sha256"]
            and composition["pin"] == pin,
            "combined server or fixed bundle drift")
    require(digest(build_root / "metal-components.json") ==
            provenance["files"]["component-build.json"] and
            digest(ROOT / "artifacts/combined-logprob-build/scheduler-fix-build.json") ==
            provenance["files"]["combined-build.json"],
            "sealed build input drift")
    serving_view = read(build_root / "fixed-manifest.json")
    serving_view["build"]["component_rebuild"] = serving_view.pop("component_rebuild")
    private = artifact_root / "evidence/combined-fixed/native-runtimes/skippy-native-runtime-darwin-aarch64-metal"
    require(read(private / "manifest.json") == serving_view and
            read(private / "component-source-manifest.json") ==
            read(build_root / "fixed-manifest.json"),
            "actual fixed native bundle identity")
    model = report["model"]
    require(model["key"] == "qwen3-coder-30b-a3b-q4-k-m" and
            model["repo"] == pin["repo"] and model["revision"] == pin["revision"] and
            [Path(f["name"]).name for f in model["files"]] ==
            [f["path"] for f in pin["files"]] and
            [(f["sha256"], f["expected_bytes"]) for f in model["files"]] ==
            [(f["sha256"], f["size"]) for f in pin["files"]],
            "exact CI Coder shard")
    require(len(report["cells"]) == 16 and
            [(cell["round"], cell["arm"]) for cell in report["cells"]] ==
            [(r, ARMS[i]) for r in range(4) for i in suite.ORDERS[r]],
            "registered cell order")
    for arm, preflight in report["preflight"].items():
        native = preflight["resolved"]
        argv = preflight["argv"]
        require(native["gpu_layers"] == 49 and native["n_ctx"] == ["131072"]
                and native["n_batch"] == ["512"] and native["n_ubatch"] == ["512"]
                and any("f16" in line for line in native["kv_buffers"]),
                f"native full-Metal/F16 settings: {arm}")
        if arm.startswith("skippy"):
            for flag, value in (("--ctx-size", "8192"),
                                ("--generation-concurrency", "16"),
                                ("--batch-size", "512"),
                                ("--microbatch-size", "512"),
                                ("--kv-cache-type-k", "f16"),
                                ("--kv-cache-type-v", "f16"),
                                ("--prefix-cache", "off"),
                                ("--kv-cache-disk", "off")):
                require(argv[argv.index(flag) + 1] == value,
                        f"Skippy launch setting {arm}/{flag}")
            if arm == "skippy-patched":
                require(argv[0] == str(Path(report["private_root"]) /
                                       "combined-fixed/skippy"),
                        "combined server binary route")
                require(argv[argv.index("--runtime-bundle") + 1] ==
                        str(Path(report["private_root"]) /
                            "combined-fixed/native-runtimes/skippy-native-runtime-darwin-aarch64-metal"),
                        "fixed native runtime route")
        else:
            for flag, value in (("-np", "16"), ("-c", "131072"),
                                ("-b", "512"), ("-ub", "512"),
                                ("-ctk", "f16"), ("-ctv", "f16"),
                                ("--cache-ram", "0")):
                require(argv[argv.index(flag) + 1] == value,
                        f"vanilla launch setting {arm}/{flag}")
            require("--no-cache-prompt" in argv, f"vanilla cache: {arm}")
    suite.PATCH = combined["patch_sha256"]
    auditor = runpy.run_path("scripts/profile-scheduler-suite-audit.py")
    audited = auditor["audit"](report, artifact_root / "evidence", fixture)
    by_cell = {(cell["round"], cell["arm"]): cell for cell in report["cells"]}
    text = {key: outputs(artifact_root, *key, cell) for key, cell in by_cell.items()}
    mismatches = []
    repeats = []
    round_drift = []
    for round_index in range(4):
        candidate = text[(round_index, "skippy-patched")]
        for other in ("llama-slots", "llama-unified"):
            reference = text[(round_index, other)]
            require(candidate.keys() == reference.keys(),
                    f"paired text coverage: {round_index}/{other}")
            mismatches.extend({"round": round_index, "candidate": "skippy-patched",
                               "reference": other, "wave": wave, "prompt": prompt,
                               "candidate_sha256": value,
                               "reference_sha256": reference[(wave, prompt)]}
                              for (wave, prompt), value in sorted(candidate.items())
                              if reference[(wave, prompt)] != value)
        for arm in ARMS:
            arm_text = text[(round_index, arm)]
            repeats.append({"round": round_index, "arm": arm,
                            "exact": arm_text[("serial-short-a", "short-0")] ==
                            arm_text[("serial-short-b", "short-0")]})
            require(len(by_cell[(round_index, arm)]["warmup"]["requests"]) == 1,
                    f"warmup request count: {round_index}/{arm}")
            if round_index:
                initial = text[(0, arm)]
                require(initial.keys() == arm_text.keys(),
                        f"cross-round text coverage: {round_index}/{arm}")
                round_drift.extend({"round": round_index, "arm": arm,
                                    "wave": wave, "prompt": prompt,
                                    "initial_sha256": initial[(wave, prompt)],
                                    "current_sha256": value}
                                   for (wave, prompt), value in sorted(arm_text.items())
                                   if initial[(wave, prompt)] != value)
    statistics = runpy.run_path("scripts/profile-scheduler-statistics.py")
    sources = []
    enriched = statistics["cells_for"](artifact_root, auditor, sources)
    group = statistics["summarize"]("Coder current-candidate balanced screen", [enriched])
    comparisons = {}
    for row in group["comparisons"]:
        if (row["comparison"] != "skippy-patched" or
                row["baseline"] not in ("llama-slots", "llama-unified")):
            continue
        bounds = row["metrics"]
        checks = {metric: noninferiority(metric, bounds[metric]["paired_percentile_95"])
                  for metric in ("ttft_p95_ms", "e2e_p95_ms", "tpot_p95_ms",
                                 "output_tok_s_over_client_span")}
        verdict = ("FAIL" if "FAIL" in checks.values() else
                   "UNPROVEN" if "UNPROVEN" in checks.values() else "PASS")
        comparisons[row["baseline"]] = {"bounds": bounds, "checks": checks,
                                        "noninferiority_verdict": verdict}
    require(set(comparisons) == {"llama-slots", "llama-unified"},
            "missing vanilla comparator")
    receipt = ROOT / f"{STEM}.probe.submission.txt"
    job_id = re.search(r"\bjob_[a-z0-9]+\b", receipt.read_text())
    require(job_id is not None, "missing model job receipt")
    result = {"schema": 1, "job_spec_sha256": digest(spec_path),
              "source_job": job_id.group(),
              "sealed_payload_sha256": read(ROOT / f"{STEM}.spec.transport.json")["payload_sha256"],
              "selected_serial_audit_sha256": provenance["selected_screen_audit_sha256"],
              "evidence_sources": sources,
              "cells_verified": len(audited["cells"]),
              "requests_verified": 192,
              "candidate_vs_vanilla_mismatches": mismatches,
              "serial_repeat_controls": repeats,
              "cross_round_output_drift": round_drift,
              "per_arm": group["summary"], "comparisons": comparisons,
              "exact_screen_pass": not mismatches and not round_drift and
                                   all(x["exact"] for x in repeats),
              "relative_noninferiority_pass": all(x["noninferiority_verdict"] == "PASS"
                                                  for x in comparisons.values())}
    result["relative_noninferiority_verdict"] = (
        "FAIL" if any(x["noninferiority_verdict"] == "FAIL"
                      for x in comparisons.values()) else
        "UNPROVEN" if not result["relative_noninferiority_pass"] else "PASS")
    result["measurement_disposition"] = (
        f"{len(audited['cells'])} cells/192 requests audited; "
        f"{len(mismatches)} current candidate vs vanilla exact-text differences; "
        f"{len(round_drift)} cross-round output changes; "
        f"relative noninferiority {result['relative_noninferiority_verdict']}; "
        f"candidate absolute TTFT/TPOT SLO {group['summary']['skippy-patched']['latency_slo_pass_rounds']}/4 rounds")
    (artifact_root / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    (artifact_root / "statistics.json").write_text(json.dumps(group, indent=2) + "\n")
    print(result["measurement_disposition"])


if __name__ == "__main__":
    main()
