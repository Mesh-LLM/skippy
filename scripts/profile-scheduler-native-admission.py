#!/usr/bin/env python3
"""Untimed exact-model loading, generation and conservative memory admission."""

import argparse
import json
from pathlib import Path
import re
import runpy
import tarfile
import time

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def memory_record(log, limit):
    # Shard/cache lines are separate allocations. sched_reserve instead prints
    # current scheduler totals per backend, once per completed reserve pass.
    # Sum same-name backends within a pass; keep their high-water totals across
    # passes. Counting every reserve report as a new allocation is incorrect.
    buffers, compute_pass, compute_peaks = {}, {}, {}
    reserve_passes = 0
    gross_mib = 0
    for line in log.splitlines():
        if "sched_reserve: reserve took" in line:
            for key, value in compute_pass.items():
                compute_peaks[key] = max(compute_peaks.get(key, 0), value)
            compute_pass = {}
            reserve_passes += 1
        for device, category, size in re.findall(
                r"(MTL\d+(?:_Mapped)?|CPU(?:_Mapped)?)\s+(model|KV|RS|compute|output)\s+buffer size =\s*([\d.]+) MiB", line):
            key = device + " " + category
            value = float(size)
            gross_mib += value
            if category == "compute":
                if "sched_reserve:" not in line:
                    raise ValueError("unrecognized compute allocation log producer")
                compute_pass[key] = compute_pass.get(key, 0) + value
            else:
                buffers[key] = buffers.get(key, 0) + value
    if compute_pass or not reserve_passes:
        raise ValueError("incomplete scheduler reserve memory report")
    buffers.update(compute_peaks)
    if not all(any(k.endswith(" " + name) for k in buffers) for name in ("model", "KV", "compute")):
        raise ValueError("native memory allocation coverage incomplete")
    total = sum(buffers.values()) * 1024 ** 2
    return {"host_and_metal_buffers_mib": buffers, "logged_allocation_bytes": total,
            "gross_allocation_report_bytes": gross_mib * 1024 ** 2,
            "scheduler_reserve_reports": reserve_passes,
            "recommended_max_working_set_bytes": limit,
            "headroom_bytes": limit - total,
            "minimum_headroom_bytes": 2 * 1024 ** 3,
            "admitted": total + 2 * 1024 ** 3 <= limit,
            "limitation": "Summed shard/cache/output allocations plus per-backend high-water scheduler totals; includes host buffers; not a transient or all-concurrency peak-memory guarantee"}


def run(args, root, report):
    identity, candidate, roots, model, replay = suite.inputs(args.cache, args.model, root)
    report.update(identity=identity, candidate=candidate, model=model)
    limit = runpy.run_path("scripts/profile-scheduler-model-admission.py")["metal_limit"]()
    reference_ids = None
    for arm in suite.ARMS:
        directory = root / arm
        directory.mkdir()
        env = host.clean_env(directory)
        suite.nominal(env, soak=60)
        engine, argv = suite.settings(roots[arm], model, arm, 16)
        if engine == "skippy":
            argv[argv.index("--startup-timeout-secs") + 1] = "1800"
        row = {"arm": arm, "complete": False}
        report["arms"].append(row)
        host.save(args.output / "native-admission.json", report)
        print("NATIVE ADMISSION", arm, flush=True)
        with host.server(roots[arm], model, engine, directory, env, 16,
                         diagnostic=True, argv=argv, startup_timeout=1860) as child:
            row["resolved"] = suite.resolved(directory / "server.log", arm, 16)
            row["memory_before_generation"] = memory_record((directory / "server.log").read_text(), limit)
            if not row["memory_before_generation"]["admitted"]:
                raise ValueError(f"{arm}: less than 2 GiB logged Metal headroom")
            text = "Describe one concrete observation about a garden."
            ids = suite.effective_tokens(model, engine, text)
            if reference_ids is None:
                reference_ids = ids
            if ids != reference_ids:
                raise ValueError("native admission token IDs differ")
            fixture = {"probe": {"text": text, "tokens": len(ids)}}
            suite.nominal(env, child.pid, 60)
            row["generation"] = replay["wave"](model, fixture, (("probe", 0, 32),),
                False, env, child.pid, capture_text=True)
            row["memory_after_generation"] = memory_record((directory / "server.log").read_text(), limit)
            host.telemetry(directory / "resident.json", env)
            if not row["memory_after_generation"]["admitted"]:
                raise ValueError(f"{arm}: post-generation Metal headroom failed")
        row["complete"] = True
        host.save(args.output / "native-admission.json", report)
    report["complete"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=suite.MODELS, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = args.cache / f"scheduler-native-admission-{time.time_ns()}"
    root.mkdir()
    report = {"complete": False, "private_root": str(root), "arms": [],
              "timing": "diagnostic only; no performance measurements"}
    try:
        run(args, root, report)
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        host.save(args.output / "native-admission.json", report)
        with tarfile.open(args.output / "native-admission-evidence.tar.gz", "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
                    archive.add(path, arcname=str(path.relative_to(root)))


if __name__ == "__main__":
    main()
