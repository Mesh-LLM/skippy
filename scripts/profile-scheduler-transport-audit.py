#!/usr/bin/env python3
"""Compare serial transports with saved loaded requests without clearing mismatches."""

import argparse
import hashlib
import json
from pathlib import Path

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def choices(root, record):
    return {(c["repeat"], c["arm"], q["prompt"], q["mode"]):
            read(root / "evidence" / f"r{c['repeat']}-{c['arm']}" / q["file"])["choices"][0]
            for c in record["cells"] for q in c["responses"]}


def audit(stream, serial, loaded):
    a, b = [read(p / "correctness-controls.json") for p in (stream, serial)]
    load = read(loaded / "suite.json")
    assert a["complete"] and b["complete"] and load["complete"]
    assert a["normal_transport"] == "streaming" and b.get("normal_transport") in (None, "non-streaming")
    for field in ("identity", "candidate", "model", "prompts", "fixture_sha256"):
        assert a[field] == b[field]
    assert a["identity"] == load["identity"] and a["candidate"] == load["candidate"]
    assert a["identity"]["source_sha"] == suite.SOURCE and a["candidate"]["patch_sha256"] == suite.PATCH
    models = []
    for model in (a["model"], load["model"]):
        normalized = dict(model)
        identity = normalized.pop("source_model_sha256", None)
        assert identity is None or identity == suite.source_identity(model["files"])
        models.append(normalized)
    assert models[0] == models[1]
    assert hashlib.sha256((loaded / "fixtures.json").read_bytes()).hexdigest() == a["fixture_sha256"]
    fixtures = {q["id"]: q for q in read(loaded / "fixtures.json")["load"]}
    for root, record in ((stream, a), (serial, b)):
        for cell in record["cells"]:
            arm = cell["arm"]
            expected = list(load["preflight"][arm]["argv"])
            if arm == "skippy-patched":
                old, new = [v["private_root"] + "/candidate/" for v in (load, record)]
                expected = [new + v[len(old):] if v.startswith(old) else v for v in expected]
            expected.insert(1, "--debug" if arm.startswith("skippy") else "--verbose")
            directory = root / "evidence" / f"r{cell['repeat']}-{arm}"
            assert read(directory / "launch.json")["argv"] == expected
            assert suite.resolved(directory / "server.log", arm, 16) == cell["resolved"]
            for item in cell["responses"]:
                response = read(directory / item["file"])
                assert ("stream_evidence" in response) == (root == stream and item["mode"].startswith("normal"))
    aa, bb = choices(stream, a), choices(serial, b)
    transport_differences = [list(k) for k in aa if aa[k]["text"] != bb[k]["text"]]
    rows = []
    for arm in suite.ARMS:
        for prompt in a["prompts"]:
            name = prompt["id"]
            text = fixtures[name]["messages"][0]["content"]
            assert hashlib.sha256(text.encode()).hexdigest() == prompt["text_sha256"]
            expected_body = host.body(a["model"], text, prompt["output_tokens"], True)
            matches = []
            for cell in load["cells"]:
                if cell["arm"] != arm:
                    continue
                directory = loaded / "evidence" / Path(cell["directory"]).name
                inputs = list((directory / "inputs").rglob(name + ".json"))
                outputs = list((directory / "outputs").rglob(name + ".json"))
                assert len(inputs) == len(outputs) == 1
                assert read(inputs[0]) == expected_body
                saved = read(outputs[0])
                assert not saved["error"] and saved["usage"]["completion_tokens"] == prompt["output_tokens"]
                matches.append({"round": cell["round"], "text_exact": saved["text"] == aa[0, arm, name, "normal"]["text"]})
            rows.append({"arm": arm, "prompt": name, "loaded_comparisons": matches})
    return {"serial_responses_compared": len(aa), "transport_text_differences": transport_differences,
            "saved_streaming_request_bodies_verified": sum(len(r["loaded_comparisons"]) for r in rows),
            "loaded_comparisons": rows,
            "scope": "Exact serial streaming/non-streaming comparison and identical saved load request bodies. Loaded outputs remain discrepant; concurrency, batching and request-history effects are not separated. No responsible operator or full equivalence verdict."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("stream", "serial", "loaded"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.stream, args.serial, args.loaded)
    (args.stream / "transport-vs-loaded.json").write_text(json.dumps(result, indent=2) + "\n")
    print(result["serial_responses_compared"], "serial responses;", len(result["transport_text_differences"]), "transport differences")
