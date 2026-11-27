#!/usr/bin/env python3
"""Audit Flash serial/mixed order controls without clearing the original failures."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(root, prior, screen):
    record, original, baseline = [read(p / name) for p, name in (
        (root, "flash-history.json"), (prior, "correctness-controls.json"), (screen, "suite.json"))]
    protocol = runpy.run_path(str(Path(__file__).with_name("profile-scheduler-flash-history.py")))
    require(record["complete"] and "error" not in record, "incomplete diagnostic")
    require(original["complete"] and original["normal_transport"] == "streaming" and baseline["complete"],
            "incomplete references")
    for field in ("identity", "candidate", "model"):
        require(record[field] == original[field] == baseline[field], "identity changed: " + field)
    require(record["model"]["key"] == protocol["MODEL"] and record["identity"]["source_sha"] == suite.SOURCE
            and record["candidate"]["patch_sha256"] == suite.PATCH, "source/model drift")
    require(record["model"]["source_model_sha256"] == suite.source_identity(record["model"]["files"]),
            "aggregate identity drift")
    fixture_path = screen / "fixtures.json"
    require(hashlib.sha256(fixture_path.read_bytes()).hexdigest() == record["fixture_sha256"]
            == original["fixture_sha256"] == protocol["FIXTURE_SHA"], "fixture drift")
    fixture = read(fixture_path)
    rows = dict(fixture["mixed"], **{"short-8": fixture["mixed"]["short-0"]})
    keys = {(first, arm) for first in ("serial", "mixed") for arm in suite.ARMS}
    require(len(record["cells"]) == 8, "wrong process count")
    seen, texts, comparisons = set(), {}, []
    for cell in record["cells"]:
        first, arm = cell["first"], cell["arm"]
        require((first, arm) in keys and (first, arm) not in seen, "unknown/duplicate process")
        seen.add((first, arm))
        directory = root / "evidence" / f"{first}-first-{arm}"
        previous = prior / "evidence" / f"r0-{arm}"
        launch = read(previous / "launch.json")
        old, new = [r["private_root"] + "/candidate/" for r in (original, record)]
        launch["argv"] = [new + s[len(old):] if s.startswith(old) else s for s in launch["argv"]]
        require(read(directory / "launch.json") == launch, "launch settings changed")
        native = suite.resolved(directory / "server.log", arm, 16)
        reference = suite.resolved(previous / "server.log", arm, 16)
        require(native == cell["resolved"], "native report mismatch")
        require({k: v for k, v in native.items() if k != "kv_buffers"} ==
                {k: v for k, v in reference.items() if k != "kv_buffers"}, "native settings changed")
        pattern = r"size =\s*([\d.]+) MiB.*?K \(f16\):\s*([\d.]+) MiB, V \(f16\):\s*([\d.]+) MiB"
        buffers = [set(re.findall(pattern, "\n".join(n["kv_buffers"]))) for n in (native, reference)]
        require(buffers[0] and buffers[0] == buffers[1], "KV allocations changed")
        boundaries = [read(directory / f"{name}.json") for name in ("before", "after")]
        require(all(b["native_thermal_pressure_level"] == 0 for b in boundaries), "thermal boundary failed")
        serial = read(previous / "short-9-normal.json")["choices"][0]["text"]
        old_cells = [c for c in baseline["cells"] if c["arm"] == arm]
        require(len(old_cells) == 1, "ambiguous archived screen")
        mixed = old_cells[0]["waves"]["mixed-c4"]["requests"]
        labels = (first, "mixed" if first == "serial" else "serial") * 2
        require(cell["complete"] and len(cell["waves"]) == 4, "incomplete process")
        for index, (item, label) in enumerate(zip(cell["waves"], labels)):
            schedule = protocol["SCHEDULES"][label]
            require(item["index"] == index and item["label"] == label and
                    item["schedule"] == [list(r) for r in schedule] and
                    item["file"] == f"{index}-{label}.json", "wave registration changed")
            path = directory / item["file"]
            require(hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], "wave hash mismatch")
            wave = read(path)
            bodies = {n: host.body(record["model"], rows[n]["text"], count, stream=True) for n, _, count in schedule}
            require(read(directory / f"{index}-requests.json") == bodies, "request drift")
            require(set(wave["requests"]) == set(bodies), "missing/extra response")
            for name, offset, count in schedule:
                response = wave["requests"][name]
                require(response["usage"]["prompt_tokens"] == rows[name]["tokens"] and
                        response["usage"]["completion_tokens"] == count and
                        response["finish_reason"] == "length" and response["cached_tokens"] == 0,
                        "token/cache/completion gate failed")
                require(response["offset_ms"] == offset * 1000 and
                        response["send_ms"] < response["end_ms"], "invalid request window")
                start = wave["start_unix_ns"] + response["send_ms"] * 1e6
                end = wave["start_unix_ns"] + response["end_ms"] * 1e6
                require(boundaries[0]["wall_ns"] <= start < end <= boundaries[1]["wall_ns"], "request outside server lifetime")
                text = response["text"]
                require(hashlib.sha256(text.encode()).hexdigest() == response["text_sha256"], "text hash mismatch")
                comparisons.append({"first": first, "arm": arm, "wave": index, "label": label,
                    "prompt": name, "text_sha256": response["text_sha256"],
                    "matches_archived_serial": text == serial if name == "short-9" else None,
                    "matches_archived_mixed": text == mixed[name]["text"]})
                if name == "short-9":
                    texts[first, arm, index] = text
    require(seen == keys and len(comparisons) == 80 and len(texts) == 32, "missing registered responses")
    controls = []
    for arm in suite.ARMS:
        serial = [texts[first, arm, i] for first, indexes in (("serial", (0, 2)), ("mixed", (1, 3))) for i in indexes]
        mixed = [texts[first, arm, i] for first, indexes in (("serial", (1, 3)), ("mixed", (0, 2))) for i in indexes]
        controls.append({"arm": arm, "serial_text_all_exact": len(set(serial)) == 1,
            "mixed_text_all_exact": len(set(mixed)) == 1,
            "fresh_first_serial_vs_mixed_exact": texts["serial", arm, 0] == texts["mixed", arm, 0],
            "all_serial_vs_mixed_exact": len(set(serial + mixed)) == 1})
    return {"responses_verified": len(comparisons), "controls": controls, "comparisons": comparisons,
            "scope": "Fresh-process wave-order control; mixed peers and scheduling remain coupled. "
                     "No operator attribution, timing claim, tolerance relaxation or serial load-0001 clearance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("artifacts", "prior", "screen"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.artifacts, args.prior, args.screen)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["controls"], indent=2))
