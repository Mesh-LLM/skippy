#!/usr/bin/env python3
"""Verify native admission evidence before allowing timed qualification screens."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy

from lib import profile_scheduler_suite as suite


def audit(root, reference):
    record = json.loads((root / "native-admission.json").read_text())
    prior = json.loads((reference / "suite.json").read_text())
    assert record["complete"] and "error" not in record
    assert record["identity"] == prior["identity"]
    assert record["candidate"] == prior["candidate"]
    assert record["identity"]["source_sha"] == suite.SOURCE
    assert record["candidate"]["patch_sha256"] == suite.PATCH
    model = record["model"]
    pin = json.loads(Path("ci/inference-artifacts.json").read_text())[suite.QUALIFICATION_IDS[model["key"]]]
    assert model["repo"] == pin["repo"] and model["revision"] == pin["revision"]
    assert len(model["files"]) == len(pin["files"])
    for actual, expected in zip(model["files"], pin["files"]):
        assert actual["sha256"] == expected["sha256"]
        assert actual["expected_bytes"] == expected["size"]
        assert actual["path"].endswith("/" + model["revision"] + "/" + expected["path"])
    assert model["source_model_sha256"] == suite.source_identity(model["files"])
    assert len(record["arms"]) == 4 and {a["arm"] for a in record["arms"]} == set(suite.ARMS)
    memory = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-native-admission.py")))["memory_record"]
    rows, digests = [], set()
    for cell in record["arms"]:
        arm = cell["arm"]
        directory = root / "evidence" / arm
        assert cell["complete"]
        assert suite.resolved(directory / "server.log", arm, 16) == cell["resolved"]
        argv = json.loads((directory / "launch.json").read_text())["argv"]
        engine = "skippy" if arm.startswith("skippy") else "llama"
        expected = list(prior["preflight"][arm]["argv"])
        if arm == "skippy-patched":
            old, new = [v["private_root"] + "/candidate/" for v in (prior, record)]
            expected = [new + v[len(old):] if v.startswith(old) else v for v in expected]
        model_flag = "--model-path" if engine == "skippy" else "-m"
        expected[expected.index(model_flag) + 1] = model["path"]
        expected = [model["key"] if v == prior["model"]["key"] else v for v in expected]
        if engine == "skippy":
            expected[expected.index("--startup-timeout-secs") + 1] = "1800"
        expected.insert(1, "--debug" if engine == "skippy" else "--verbose")
        assert argv == expected
        for name in ("before", "resident", "after"):
            telemetry = json.loads((directory / (name + ".json")).read_text())
            assert telemetry["native_thermal_pressure_level"] == 0
        for name in ("memory_before_generation", "memory_after_generation"):
            saved = cell[name]
            assert saved["minimum_headroom_bytes"] == 2 * 1024 ** 3 and saved["admitted"]
            assert saved["headroom_bytes"] >= saved["minimum_headroom_bytes"]
        saved = cell["memory_after_generation"]
        calculated = memory((directory / "server.log").read_text(), saved["recommended_max_working_set_bytes"])
        for key, value in calculated.items():
            if isinstance(value, float):
                assert math.isclose(value, saved[key], rel_tol=1e-12, abs_tol=0.001)
            else:
                assert value == saved[key]
        q = cell["generation"]["requests"]["probe"]
        assert q["finish_reason"] == "length" and q["cached_tokens"] == 0
        assert q["usage"]["prompt_tokens"] == 8 and q["usage"]["completion_tokens"] == 32
        assert q["events_ms"] and hashlib.sha256(q["text"].encode()).hexdigest() == q["text_sha256"]
        digests.add(q["text_sha256"])
        rows.append({"arm": arm, "headroom_gib": calculated["headroom_bytes"] / 1024 ** 3,
                     "gpu_layers": cell["resolved"]["gpu_layers"],
                     "native_n_seq_max": cell["resolved"]["n_seq_max"], "active_lanes": 16})
    assert len(digests) == 1
    return {"model": model["key"], "native_admission": "PASS", "arms": rows,
            "probe_outputs_exact": True,
            "scope": "Untimed 8-input/32-output probe, full Metal offload and logged memory headroom only. Input token identity checked by completed runner; timed preflight will record full fixture IDs. Not transient or concurrent peak-memory proof, or full numerical qualification."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.reference)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
