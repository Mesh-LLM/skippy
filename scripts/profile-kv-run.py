#!/usr/bin/env python3
"""Compare and profile the four pinned KV architecture fixtures on one Mac."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
import time
import traceback
from pathlib import Path

from lib.profile_kv_capture import capture
from lib.profile_kv_host import HostUnavailable, IK, bench, body, clean_env, command, make_prompts, post, require_idle, save, server


def interrupted(signum, _frame):
    raise KeyboardInterrupt(f"received signal {signum}")


def validate_record(record):
    for row in record["data"].get("request_accounting", {}).items():
        if row[0] in ("failed", "failed_requests") and row[1]:
            raise ValueError(f"benchmark requests failed: {row}")
    for limitation in record.get("limitations", []):
        if any(text in limitation for text in ("did not generate exactly", "request(s) failed", "disagree with usage")):
            raise ValueError(limitation)


def run_model(cache, model, root, output, env, pilot, screen=False, timing_only=False, paired_rounds=None, reference_root=None):
    directory = root / model["key"]
    directory.mkdir()
    records, previous = [], {}
    prompt_path = None
    prompts = None
    if reference_root:
        source = reference_root / model["key"] / "prompts.jsonl"
        prompt_path = directory / "prompts.jsonl"
        shutil.copy2(source, prompt_path)
        prompts = [json.loads(line) for line in prompt_path.read_text().splitlines()]
        save(directory / "reference.json", {"source": str(source),
             "prompts_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "scope": "Completed diagnostic timing/settings from the prior screen, not new comparison samples"})
    if not pilot and not timing_only and not reference_root:
        for engine in ("llama", "skippy"):
            diagnostic = directory / ("settings-" + engine)
            diagnostic.mkdir()
            require_idle(env)
            with server(cache, model, engine, diagnostic, env, diagnostic=True):
                save(diagnostic / "response.json", post("/v1/completions", body(model, "The orchard keeper records", 4)))
    # Legacy campaigns use five ABBA blocks. The registered timing phase uses
    # six alternating AB/BA pairs; paired rounds, never requests, are its units.
    blocks = 0 if reference_root else 1 if pilot or screen else paired_rounds or 5
    for block in range(blocks):
        if paired_rounds:
            order = ("llama", "skippy") if block % 2 == 0 else ("skippy", "llama")
        else:
            order = ("llama", "skippy") if pilot else ("llama", "skippy", "skippy", "llama")
        for position, engine in enumerate(order):
            cell = directory / f"b{block}-p{position}-{engine}"
            cell.mkdir()
            print("COOLDOWN", model["key"], block, position, engine, "30 seconds, servers stopped", flush=True)
            time.sleep(30)
            require_idle(env)
            print("CELL", model["key"], block, position, engine, flush=True)
            with server(cache, model, engine, cell, env) as child:
                if prompt_path is None:
                    prompt_path, prompts = make_prompts(directory)
                response = post("/v1/completions", body(model, prompts[0]["messages"][0]["content"], 16))
                save(cell / "smoke.json", response)
                if response.get("usage", {}).get("completion_tokens") != 16:
                    raise ValueError(f"smoke output length: {response.get('usage')}")
                round_index = sum(row["engine"] == engine for row in records)
                record = bench(model, engine, prompt_path, directory, cell, env, previous.get(engine), child.pid,
                               round_index=round_index)
                validate_record(record)
                previous[engine] = record["evidence_id"]
                records.append({"block": block, "position": position, "engine": engine,
                                "evidence_id": record["evidence_id"], "data": record["data"],
                                "limitations": record.get("limitations", [])})
                shutil.copy2(cell / "bench.json", output / f"{model['key']}-b{block}-p{position}-{engine}.json")
                if pilot:
                    result = capture(child, model, prompts[0], cell / "profile", env, seconds=12)
                    save(output / f"pilot-{engine}-capture.json", result)
                    if result["status"] != "CAPTURED":
                        raise RuntimeError(f"{engine} profiler failed: {result.get('error')}")
            save(output / "progress.json", {"model": model["key"], "block": block,
                                           "position": position, "engine": engine, "wall_ns": time.time_ns()})
    captures = []
    if not pilot and not timing_only:
        for engine in ("llama", "skippy"):
            cell = directory / f"profile-{engine}"
            cell.mkdir()
            time.sleep(30)
            require_idle(env)
            with server(cache, model, engine, cell, env) as child:
                for prompt in (prompts[0], prompts[2]):
                    result = capture(child, model, prompt, cell / prompt["bucket"], env)
                    result.update(engine=engine, model=model["key"], bucket=prompt["bucket"])
                    captures.append(result)
                    save(output / f"{model['key']}-{engine}-{prompt['bucket']}-capture.json", result)
                    if result["status"] != "CAPTURED":
                        raise RuntimeError(f"required capture failed: {result.get('error')}")
    return {"model": model["key"], "records": records, "captures": captures}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--screen", action="store_true", help="One ABBA block and both profiles for every model; diagnostic baselines only")
    parser.add_argument("--timing-only", action="store_true", help="Fresh repeated timing; use separately recorded screen captures/settings")
    parser.add_argument("--paired-rounds", type=int, help="Even number of fresh AB/BA paired rounds instead of ABBA blocks")
    parser.add_argument("--model-key", action="append", help="Select an exact pinned fixture key; default is all four")
    parser.add_argument("--profiles-only", action="store_true", help="Capture only; use completed diagnostic timings/prompts from reference-root")
    parser.add_argument("--reference-root", type=Path)
    args = parser.parse_args()
    if args.paired_rounds is not None and (not args.timing_only or args.paired_rounds < 4 or args.paired_rounds % 2):
        parser.error("paired rounds require timing-only mode and an even round count of at least four")
    if args.profiles_only and (not args.reference_root or args.pilot or args.screen or args.timing_only):
        parser.error("profiles-only requires reference-root and cannot be combined with other modes")
    if args.reference_root and not args.profiles_only:
        parser.error("reference-root is only valid with profiles-only")
    cache = args.cache.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    identity = json.loads((cache / "build-identity.json").read_text())
    for name, expected in identity["files"].items():
        if hashlib.file_digest((cache / name).open("rb"), "sha256").hexdigest() != expected:
            raise ValueError(f"build artifact mismatch: {name}")
    campaign = ("pilot" if args.pilot else "screen" if args.screen else "profiles" if args.profiles_only else "campaign") + "-" + str(time.time_ns())
    root = cache / campaign
    root.mkdir()
    env = clean_env(root)
    doctor = command(IK + ["doctor", "--json"], env, capture_output=True, text=True)
    (root / "doctor.json").write_text(doctor.stdout)
    (output / "doctor.json").write_text(doctor.stdout)
    if not args.pilot:
        require_idle(env)
        tool = next((cache / "native-runtimes").glob("*/tools/skippy-gpu-benchmark"))
        reading = command([str(tool), "--json"], env, timeout=180, capture_output=True, text=True)
        bandwidth = {"command": [str(tool), "--json"], "data": json.loads(reading.stdout),
                     "source_sha": identity["source_sha"], "buffer_mib": 512,
                     "scope": "supplied native Metal read probe; shader and cache assumptions need audit before a ceiling claim"}
        save(root / "bandwidth.json", bandwidth)
        save(output / "bandwidth.json", bandwidth)
    models = json.loads((cache / "models.json").read_text())
    if args.model_key:
        selected = set(args.model_key)
        unknown = selected - {model["key"] for model in models}
        if unknown:
            raise ValueError(f"unknown pinned fixture keys: {sorted(unknown)}")
        models = [model for model in models if model["key"] in selected]
    for model in models:
        model.update(skippy_version=identity["source_sha"], llama_version=identity["upstream_sha"])
    plan = {"status": "running", "private_root": str(root), "identity": identity,
            "models": [m["key"] for m in models], "budget_approved": True,
            "approval": "User approved the corrected four-model plan and three-hour host budget in this conversation.",
            "blocks": 0 if args.profiles_only else 1 if args.pilot or args.screen else args.paired_rounds or 5,
            "order": "alternating AB/BA pairs; A=llama B=skippy" if args.paired_rounds else "ABBA per block; A=llama B=skippy",
            "statistical_unit": "fresh-process paired round" if args.paired_rounds else "ABBA block",
            "baseline_poll": 0, "baseline_poll_batch": 0, "cooldown_between_processes_s": 30,
            "timing_only": args.timing_only,
            "profiles_only": args.profiles_only,
            "reference_root": str(args.reference_root) if args.profiles_only else None,
            "scope": "profiles using prior diagnostic timing scale" if args.profiles_only else "diagnostic screen, not qualified speed comparison" if args.screen else "full repeated timing",
            "contexts": [512, 4096], "outputs": [256, 64], "context_capacity_per_lane": 8192,
            "kv_type": "f16", "prefix_cache": "off", "native_mtp": "disabled",
            "raw_traces": "Private on host; never uploaded", "results": []}
    save(output / "campaign.json", plan)
    try:
        for model in models[:1] if args.pilot else models:
            try:
                result = run_model(cache, model, root, output, env, args.pilot, args.screen, args.timing_only,
                                   args.paired_rounds, args.reference_root if args.profiles_only else None)
                plan["results"].append(result)
            except Exception as error:
                plan["results"].append({"model": model["key"], "status": "FAILED", "error": str(error)})
                print(traceback.format_exc(), flush=True)
                if args.pilot or isinstance(error, HostUnavailable):
                    raise
            save(output / "campaign.json", plan)
        plan["status"] = "failed" if any(r.get("status") == "FAILED" for r in plan["results"]) else "completed"
    except BaseException:
        plan["status"] = "interrupted-or-failed"
        raise
    finally:
        save(output / "campaign.json", plan)
        # Preserve synthetic request evidence and logs. Raw Instruments traces stay private.
        import tarfile
        with tarfile.open(output / "request-evidence.tar.gz", "w:gz") as archive:
            for path in root.rglob("*"):
                if path.is_file() and not any(p.endswith(".trace") for p in path.parts) and path.name != "toc.xml":
                    archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    print("CAMPAIGN", json.dumps({"status": plan["status"], "private_root": str(root)}), flush=True)
    return 0 if plan["status"] == "completed" else 1


if __name__ == "__main__":
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    sys.exit(main())
