#!/usr/bin/env python3
"""Audit the registered Qwen3.5 concurrency/history diagnostic, retaining differences."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(root, prior, loaded, history=None):
    record = read(root / "batch-history.json")
    original = read(prior / "correctness-controls.json")
    load = read(loaded / "suite.json")
    require(record["complete"] and "error" not in record, "incomplete diagnostic")
    require(original["complete"] and original["normal_transport"] == "streaming", "invalid serial reference")
    require(load["complete"] and load["offered_concurrency"] == 128, "invalid load reference")
    for field in ("identity", "candidate"):
        require(record[field] == original[field] == load[field], "binary identity changed")
    require(record["identity"]["source_sha"] == suite.SOURCE and
            record["candidate"]["patch_sha256"] == suite.PATCH, "source/candidate drift")
    model = dict(record["model"])
    require(model["key"] == "qwen3.5-4b-q4-k-m", "wrong qualification model")
    for other in (original["model"], load["model"]):
        normalized = dict(other)
        normalized.setdefault("source_model_sha256", suite.source_identity(normalized["files"]))
        require(normalized == model, "model identity changed")
    require(model["source_model_sha256"] == suite.source_identity(model["files"]), "aggregate identity changed")
    fixture_path = loaded / "fixtures.json"
    require(hashlib.sha256(fixture_path.read_bytes()).hexdigest() == record["fixture_sha256"]
            == original["fixture_sha256"] == "422555f06141441de540134ed889458c290b9114dfe557c034463fa33fbfbd5e",
            "fixture drift")
    require(record["prompt"] == "load-0190", "unregistered subject")
    subject = next(r for r in read(fixture_path)["load"] if r["id"] == record["prompt"])
    require(subject["native_prompt_tokens"] == 512 and subject["max_tokens"] == 32, "subject counts changed")
    body = host.body(model, subject["messages"][0]["content"], 32, stream=True)
    trace = record.get('trace_baseline', False)
    arms = ('skippy-base',) if trace else suite.ARMS
    require(not trace or history is not None, 'trace requires archived history reference')
    keys = {(first, arm) for first in (1, 16) for arm in arms}
    require(len(record["cells"]) == len(keys), "wrong process count")
    seen, results, comparisons = set(), {}, []
    for cell in record["cells"]:
        first, arm = cell["first"], cell["arm"]
        require((first, arm) in keys and (first, arm) not in seen, "unknown/duplicate process")
        seen.add((first, arm))
        directory = root / "evidence" / f"c{first}-first-{arm}"
        previous = prior / "evidence" / f"r0-{arm}"
        launch = read(previous / "launch.json")
        old, new = [r["private_root"] + "/candidate/" for r in (original, record)]
        launch["argv"] = [new + s[len(old):] if s.startswith(old) else s for s in launch["argv"]]
        if trace:
            launch['argv'] += ['--telemetry-level', 'debug', '--telemetry-queue-capacity', '65536']
            launch['environment_names'] = sorted(launch['environment_names'] + ['SKIPPY_TELEMETRY_STDERR'])
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
        serial = read(previous / "load-0190-normal.json")["choices"][0]["text"]
        loaded_text = {}
        for c in load["cells"]:
            if c["arm"] != arm:
                continue
            d = loaded / "evidence" / Path(c["directory"]).name
            inputs, outputs = [list((d / kind).rglob("load-0190.json")) for kind in ("inputs", "outputs")]
            require(len(inputs) == len(outputs) == 1 and read(inputs[0]) == body, "archived load request changed")
            saved = read(outputs[0])
            require(not saved["error"], "archived subject failed")
            require(c["round"] not in loaded_text, "duplicate archived round")
            loaded_text[c["round"]] = saved["text"]
        require(set(loaded_text) == {0, 1, 2, 3}, "missing archived rounds")
        sizes = (first, 16 if first == 1 else 1) * 2
        require(cell["complete"] and len(cell["waves"]) == 4, "incomplete process")
        for index, (item, size) in enumerate(zip(cell["waves"], sizes)):
            require(item["index"] == index and item["concurrency"] == size and
                    item["file"] == f"{index}-c{size}.json", "wave registration changed")
            path = directory / item["file"]
            require(hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], "wave hash mismatch")
            wave = read(path)
            names = {f"subject-{i:02d}" for i in range(size)}
            require(set(wave["requests"]) == names, "missing/extra response")
            require(read(directory / f"{index}-requests.json") == {n: body for n in names}, "request drift")
            texts = []
            for name, response in wave["requests"].items():
                require(response["usage"]["prompt_tokens"] == 512 and
                        response["usage"]["completion_tokens"] == 32 and
                        response["finish_reason"] == "length" and response["cached_tokens"] == 0,
                        "token/cache/completion gate failed")
                require(response["offset_ms"] == 0 and response["send_ms"] < response["end_ms"], "invalid request window")
                start = wave["start_unix_ns"] + response["send_ms"] * 1e6
                end = wave["start_unix_ns"] + response["end_ms"] * 1e6
                require(boundaries[0]["wall_ns"] <= start < end <= boundaries[1]["wall_ns"], "request outside server lifetime")
                text = response["text"]
                require(hashlib.sha256(text.encode()).hexdigest() == response["text_sha256"], "text hash mismatch")
                texts.append(text)
                if trace:
                    archived = read(history/'evidence'/directory.name/item['file'])
                    require(text == archived['requests'][name]['text'], 'trace changed archived history output')
                comparisons.append({"first": first, "arm": arm, "wave": index, "concurrency": size,
                    "request": name, "text_sha256": response["text_sha256"],
                    "matches_archived_serial": text == serial,
                    "matches_archived_load_rounds": [r for r, value in loaded_text.items() if value == text]})
            results[first, arm, index] = texts
    require(seen == keys and len(comparisons) == 68 * len(arms), "missing registered responses")
    controls = []
    for arm in arms:
        serial = results[1, arm, 0][0]
        controls.append({"arm": arm,
            "all_serial_text_exact": all(text == serial for first, indexes in ((1, (0, 2)), (16, (1, 3)))
                                         for i in indexes for text in results[first, arm, i]),
            "fresh_c16_matches_fresh_serial": [text == serial for text in results[16, arm, 0]],
            "waves": [{"first": first, "index": i, "unique_texts": len(set(results[first, arm, i])),
                       "serial_matches": sum(t == serial for t in results[first, arm, i])}
                      for first in (1, 16) for i in range(4)]})
    return {"responses_verified": len(comparisons), "controls": controls, "comparisons": comparisons,
            "scope": "Offered concurrency/history diagnostic, not controlled native batch shape. "
                     "No timing claims, tolerance relaxation, operator attribution or original c128 clearance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("artifacts", "prior", "loaded"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument('--history', type=Path)
    args = parser.parse_args()
    result = audit(args.artifacts, args.prior, args.loaded, args.history)
    (args.artifacts / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["controls"], indent=2))
