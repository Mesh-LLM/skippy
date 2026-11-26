#!/usr/bin/env python3
"""Audit exported timeline metadata; raw CPU/Metal row checks run on the host."""

import argparse
import hashlib
import json
from pathlib import Path

from lib import profile_scheduler_suite as suite


def audit(root, reference):
    record = json.loads((root / "timeline.json").read_text())
    baseline = json.loads((reference / "suite.json").read_text())
    fixture = json.loads((root / "fixtures.json").read_text())
    assert record["complete"] and "error" not in record and record["prefix_cache"] is False
    assert record["identity"]["source_sha"] == suite.SOURCE
    assert record["candidate"]["patch_sha256"] == suite.PATCH
    assert record["candidate"] == baseline["candidate"]
    assert record["identity"] == baseline["identity"]
    models = []
    for model in (record["model"], baseline["model"]):
        normalized = dict(model)
        identity = normalized.pop("source_model_sha256", None)
        assert identity is None or identity == suite.source_identity(model["files"])
        models.append(normalized)
    assert models[0] == models[1]
    assert hashlib.sha256((root / "fixtures.json").read_bytes()).hexdigest() == record["fixture_sha256"]
    assert (root / "fixtures.json").read_bytes() == (reference / "fixtures.json").read_bytes()
    assert len(record["cells"]) == 4 and {c["arm"] for c in record["cells"]} == set(suite.ARMS)
    rows, texts = [], {}
    for cell in record["cells"]:
        arm = cell["arm"]
        directory = root / "evidence" / arm
        assert cell["complete"]
        argv = list(cell["argv"])
        if arm.startswith("skippy"):
            for flag, value in (("--telemetry-level", "debug"), ("--telemetry-queue-capacity", "65536")):
                index = argv.index(flag)
                assert argv[index + 1] == value
                del argv[index:index + 2]
        expected_argv = list(baseline["preflight"][arm]["argv"])
        if arm == "skippy-patched":
            old_prefix = baseline["private_root"] + "/candidate/"
            new_prefix = record["private_root"] + "/candidate/"
            expected_argv = [new_prefix + value[len(old_prefix):] if value.startswith(old_prefix)
                             else value for value in expected_argv]
        assert argv == expected_argv
        launch = json.loads((directory / "launch.json").read_text())["argv"]
        expected = list(cell["argv"])
        expected.insert(1, "--debug" if arm.startswith("skippy") else "--verbose")
        assert launch == expected
        assert suite.resolved(directory / "server.log", arm, 16) == cell["resolved"]
        for boundary in ("before", "after"):
            saved = json.loads((directory / (boundary + ".json")).read_text())
            assert saved == cell[boundary] and saved["native_thermal_pressure_level"] == 0
        requests = cell["wave"]["requests"]
        assert set(requests) == {"short-8", "long-2", "long-3", "short-9"}
        for name, count in (("short-8", 128), ("long-2", 24), ("long-3", 24), ("short-9", 48)):
            q = requests[name]
            original = "short-0" if name == "short-8" else name
            assert q["finish_reason"] == "length" and q["cached_tokens"] == 0
            assert q["usage"]["prompt_tokens"] == fixture["mixed"][original]["tokens"]
            assert q["usage"]["completion_tokens"] == count and q["events_ms"]
            assert hashlib.sha256(q["text"].encode()).hexdigest() == q["text_sha256"]
            texts[arm, name] = q["text_sha256"]
        rows.append({"arm": arm, "requests_verified": len(requests),
            "telemetry_drops": cell.get("scheduler", {}).get("dropped_events"),
            "linked_requests": cell.get("links", {}).get("linked_requests"),
            "scheduler_iterations": cell.get("scheduler", {}).get("waves", {}).get("mixed-c4", {}).get("iterations")})
    return {"model": record["model"]["key"], "cells": rows,
        "exact_output_mismatches": [{"arm": arm, "prompt": name} for (arm, name), text in texts.items()
            if arm != "skippy-base" and text != texts["skippy-base", name]],
        "scope": "Diagnostic metadata only; complete trace windows and PID-attributed CPU/GPU rows require separate private-host analysis; no speed or quality claim"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.reference)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
