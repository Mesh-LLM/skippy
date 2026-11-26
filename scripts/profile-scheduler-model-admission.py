#!/usr/bin/env python3
"""Inspect exact CI qualification model inputs without fetching or loading weights."""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import time


KEYS = {"qwen35-4b": "qwen3.5-4b-q4-k-m",
        "qwen3-coder-30b-a3b": "qwen3-coder-30b-a3b-q4-k-m",
        "qwen36-35b-a3b": "qwen3.6-35b-a3b-q4-k-m",
        "qwen38-flash-next": "qwen3.8-flash-next-iq4-xs"}


def metal_limit():
    metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
    objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.objc_msgSend.restype = ctypes.c_uint64
    device = metal.MTLCreateSystemDefaultDevice()
    if not device:
        raise RuntimeError("Metal device unavailable")
    return objc.objc_msgSend(device, objc.sel_registerName(b"recommendedMaxWorkingSetSize"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", choices=KEYS, action="append")
    args = parser.parse_args()
    manifests = json.loads(Path("ci/inference-artifacts.json").read_text())
    ladder = json.loads(Path("ci/inference-ladder.json").read_text())
    roster = [m for m in ladder["models"] if m["pr"]]
    if set(m["id"] for m in roster) != set(KEYS):
        raise ValueError("qualification roster changed; refresh this admission mapping")
    stores = [Path("/Users/Shared/jianyang/model-store/huggingface/hub"),
              Path.home() / ".cache/huggingface/hub"]
    report = {"created_unix_s": time.time(), "models": [],
              "host_memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])),
              "metal_recommended_max_working_set_bytes": metal_limit(),
              "manifest_sha256": hashlib.sha256(Path("ci/inference-artifacts.json").read_bytes()).hexdigest(),
              "runtime_support": "UNPROVEN until exact native load and generation succeed"}
    args.output.mkdir(parents=True, exist_ok=True)
    cache_record = args.cache / "scheduler-qualification-models-v1.json"
    admitted = json.loads(cache_record.read_text()) if cache_record.exists() else []
    for model in roster:
        if args.model_id and model["id"] not in args.model_id:
            continue
        pin = manifests[model["id"]]
        item = {"qualification_id": model["id"], "key": KEYS[model["id"]],
                "repo": pin["repo"], "revision": pin["revision"], "files": []}
        print("ADMIT", item["key"], flush=True)
        report["models"].append(item)
        for file in pin["files"]:
            suffix = Path("models--" + pin["repo"].replace("/", "--")) / "snapshots" / pin["revision"] / file["path"]
            path = next((root / suffix for root in stores if (root / suffix).is_file()), None)
            row = {"name": file["path"], "sha256": file["sha256"], "expected_bytes": file["size"],
                   "present": path is not None, "verified": False}
            if path:
                row.update(path=str(path), actual_bytes=path.stat().st_size)
                with path.open("rb") as stream:
                    row["actual_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                row["verified"] = row["actual_sha256"] == row["sha256"] and row["actual_bytes"] == row["expected_bytes"]
            item["files"].append(row)
            (args.output / "model-admission.json").write_text(json.dumps(report, indent=2) + "\n")
            print("SHARD", file["path"], "verified", row["verified"], flush=True)
        item["verified"] = all(f["verified"] for f in item["files"])
        item["weight_bytes"] = sum(f["expected_bytes"] for f in item["files"])
        item["recommended_working_set_less_file_bytes"] = report["metal_recommended_max_working_set_bytes"] - item["weight_bytes"]
        item["capacity_verdict"] = "UNPROVEN: file bytes exclude KV, recurrent state, graphs and scratch"
        if item["verified"]:
            item["path"] = item["files"][0]["path"]
            admitted = [row for row in admitted if row["key"] != item["key"]]
            admitted.append(item)
            cache_record.write_text(json.dumps(admitted, indent=2) + "\n")
        (args.output / "model-admission.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
