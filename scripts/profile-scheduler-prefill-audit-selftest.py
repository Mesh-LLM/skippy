#!/usr/bin/env python3
"""Offline auditor mutations using copied evidence; never native qualification."""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import runpy
import shutil
import tempfile

from lib import profile_scheduler_suite as suite


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior", type=Path, required=True)
    args = parser.parse_args()
    audit = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-prefill-audit.py")))["audit"]
    fix = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-prefill-retest.py")))["FIX"]
    original = json.loads((args.prior / "path-isolation.json").read_text())
    with tempfile.TemporaryDirectory(prefix="prefill-audit-synthetic-") as temp:
        root = Path(temp)
        built = {"source": suite.SOURCE, "patch_sha256": fix, "binary_sha256": "synthetic-only",
                 "candidate_path": "synthetic-only"}
        save(root / "build.json", built)
        record = {"complete": True, "private_root": "/synthetic/private", "patch_sha256": fix,
                  "fixed_candidate": built, "identity": original["identity"],
                  "original_candidate": original["candidate"], "model": original["model"],
                  "fixture_sha256": original["fixture_sha256"], "cells": []}
        for batch in (4096, 512):
            for first in ("normal", "logprobs"):
                source = args.prior / "evidence" / f"{first}-first-skippy-base-long-2"
                original_cell = next(c for c in original["cells"] if c["first"] == first)
                for arm in ("skippy-base", "skippy-patched"):
                    name = f"b{batch}-{first}-first-{arm}"
                    directory = root / "evidence" / name
                    shutil.copytree(source, directory)
                    launch = json.loads((directory / "launch.json").read_text())
                    argv = launch["argv"]
                    argv[argv.index("--batch-size") + 1] = str(batch)
                    if arm == "skippy-patched":
                        linked = Path(record["private_root"]) / "fixed-links/candidate"
                        argv[0] = str(linked / "skippy")
                        at = argv.index("--runtime-bundle") + 1
                        argv[at] = str(linked / "native-runtimes" / Path(argv[at]).name)
                    save(directory / "launch.json", launch)
                    log = directory / "server.log"
                    log.write_text(re.sub(r"(\bn_batch\s*=\s*)512\b", rf"\g<1>{batch}", log.read_text()))
                    cell = {"batch_size": batch, "first": first, "arm": arm, "directory": name,
                            "complete": True, "resolved": suite.resolved(log, arm, 16, batch), "attempts": []}
                    for index, old_item in enumerate(original_cell["responses"]):
                        item = copy.deepcopy(old_item)
                        item["file"] = Path(item["file"]).name
                        item["status"] = 200
                        if batch == 4096 and arm == "skippy-base" and item["mode"] == "logprobs":
                            item["status"] = 400
                            save(directory / item["file"], {"http_status": 400,
                                 "body": "scheduler iteration exceeds the 2048-token configured iteration limit"})
                        item["sha256"] = hashlib.sha256((directory / item["file"]).read_bytes()).hexdigest()
                        cell["attempts"].append(item)
                    record["cells"].append(cell)
        save(root / "prefill-retest.json", record)
        result = audit(root, args.prior, root / "build.json")
        assert result["responses_verified"] == 28 and result["expected_baseline_failures_verified"] == 4
        target = root / "evidence/b512-normal-first-skippy-patched/0-normal-request.json"
        request = json.loads(target.read_text())
        request["cache_prompt"] = True
        save(target, request)
        try:
            audit(root, args.prior, root / "build.json")
        except ValueError as error:
            assert str(error) == "request changed", str(error)
        else:
            raise AssertionError("changed request accepted")
        request["cache_prompt"] = False
        save(target, request)
        record["cells"][0]["attempts"][1]["status"] = 200
        save(root / "prefill-retest.json", record)
        try:
            audit(root, args.prior, root / "build.json")
        except ValueError as error:
            assert str(error) == "baseline failure not reproduced", str(error)
        else:
            raise AssertionError("missing baseline rejection accepted")
    print("Synthetic 28-response/4-error accounting passed; request and error mutations rejected. Not native qualification.")


if __name__ == "__main__":
    main()
