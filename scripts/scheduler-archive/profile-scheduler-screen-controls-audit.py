#!/usr/bin/env python3
"""Bind serial correctness controls to their original screen and immutable inputs."""

import argparse
import hashlib
import json
import re
from pathlib import Path
import runpy

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def audit(controls, screen):
    checker = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-correctness-audit.py")))
    verified = checker["audit"](controls)
    record = read(controls / "correctness-controls.json")
    original = read(screen / "suite.json")
    assert original["complete"] and record["normal_transport"] == "streaming"
    for field in ("identity", "candidate", "model"):
        assert record[field] == original[field], field
    assert record["identity"]["source_sha"] == suite.SOURCE
    assert record["candidate"]["patch_sha256"] == suite.PATCH
    fixture_path = screen / "fixtures.json"
    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == record["fixture_sha256"]
    fixtures = read(fixture_path)
    prompts = {q["id"]: q for q in record["prompts"]}
    load = {q["id"]: q for q in fixtures["load"]}
    rows = []
    for cell in record["cells"]:
        arm = cell["arm"]
        directory = controls / "evidence" / f"r{cell['repeat']}-{arm}"
        expected = list(original["preflight"][arm]["argv"])
        if arm == "skippy-patched":
            old, new = [v["private_root"] + "/candidate/" for v in (original, record)]
            expected = [new + v[len(old):] if v.startswith(old) else v for v in expected]
        expected.insert(1, "--debug" if arm.startswith("skippy") else "--verbose")
        assert read(directory / "launch.json")["argv"] == expected
        native = suite.resolved(directory / "server.log", arm, 16)
        assert native == cell["resolved"]
        prior = original["preflight"][arm]["resolved"]
        assert {k: v for k, v in native.items() if k != "kv_buffers"} == {
            k: v for k, v in prior.items() if k != "kv_buffers"}
        # Raw buffer lines contain log timestamps; compare allocation values instead.
        pattern = r"size =\s*([\d.]+) MiB.*?K \(f16\):\s*([\d.]+) MiB, V \(f16\):\s*([\d.]+) MiB"
        buffers = [set(re.findall(pattern, "\n".join(v["kv_buffers"]))) for v in (native, prior)]
        assert buffers[0] and buffers[0] == buffers[1]
        source_cell = next(c for c in original["cells"] if c["arm"] == arm)
        for item in cell["responses"]:
            response = read(directory / item["file"])
            assert ("stream_evidence" in response) == item["mode"].startswith("normal")
            name = item["prompt"]
            text = load[name]["messages"][0]["content"] if name in load else fixtures["mixed"][name]["text"]
            assert hashlib.sha256(text.encode()).hexdigest() == prompts[name]["text_sha256"]
            if name in load:
                root = screen / "evidence" / Path(source_cell["directory"]).name
                inputs = list((root / "inputs" / arm).rglob(name + ".json"))
                outputs = list((root / "outputs" / arm).rglob(name + ".json"))
                assert len(inputs) == len(outputs) == 1
                assert read(inputs[0]) == host.body(record["model"], text, prompts[name]["output_tokens"], True)
                saved = read(outputs[0])
                assert not saved["error"]
                original_text = saved["text"]
            else:
                original_text = source_cell["waves"]["mixed-c4"]["requests"][name]["text"]
            rows.append({"repeat": cell["repeat"], "arm": arm, "prompt": name, "mode": item["mode"],
                         "text_matches_screen": response["choices"][0]["text"] == original_text})
    return {"responses_verified": verified["responses_verified"], "identities_and_native_settings": "PASS",
            "fixture_and_saved_load_request_bodies": "PASS", "comparisons": rows,
            "scope": "Serial controls versus same-arm screen text. Mixed prompts use the exact saved fixture; "
                     "only inferencekit prompts have saved request bodies. Serial/mixed differences do not isolate "
                     "concurrency, batching, history or the responsible operator. No equivalence clearance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--screen", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.controls, args.screen)
    (args.controls / "screen-comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    print(result["responses_verified"], "responses verified;",
          sum(not r["text_matches_screen"] for r in result["comparisons"]), "screen text differences")
