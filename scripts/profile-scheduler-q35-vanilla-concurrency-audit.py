#!/usr/bin/env python3
"""Audit unchanged-vanilla Qwen3.5 c1/c16 text distributions."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

from lib import profile_kv_host as host
from lib import profile_scheduler_suite as suite


ARMS = ("llama-slots", "llama-unified")


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, label):
    if not ok:
        raise ValueError(label)


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            name = PurePosixPath(member.name)
            require(not name.is_absolute() and ".." not in name.parts, "unsafe archive path")
            target = destination.joinpath(*name.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile() and target.suffix in (".json", ".log"), "unsafe archive member")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as handle:
                    target.write_bytes(handle.read())


def loaded_text(root, arm):
    values = []
    for round_number in range(4):
        files = tuple((root / "evidence" / f"r{round_number}-{arm}" / "evidence").glob(
            f"*/outputs/{arm}/load-c128/p512-o32/load-0100.json"))
        require(len(files) == 1, "missing c128 output")
        values.append(read(files[0])["text"])
    require(len(set(values)) == 1, "c128 comparator drift")
    return values[0]


def peak_inflight(responses):
    events = []
    for response in responses.values():
        events += [(response["send_ms"], 1), (response["end_ms"], -1)]
    current = maximum = 0
    for _, change in sorted(events):
        current += change
        maximum = max(maximum, current)
    return maximum


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--loaded", type=Path, required=True)
    parser.add_argument("--serial", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    root = args.artifacts
    extract(root / "vanilla-concurrency-evidence.tar.gz", root / "evidence")
    record = read(root / "vanilla-concurrency.json")
    inputs = read(args.spec.with_suffix(".inputs.json"))
    old = read(args.loaded / "suite.json")
    fixture = read(args.loaded / "fixtures.json")
    pin = read(Path("ci/inference-artifacts.json"))["qwen35-4b"]
    require(record["complete"] and "error" not in record and len(record["cells"]) == 4,
            "incomplete probe")
    require(record["provenance"] == inputs and record["identity"] == old["identity"] and
            record["pin"] == pin and record["model"]["repo"] == pin["repo"] and
            record["model"]["revision"] == pin["revision"] and
            record["model"]["files"][0]["sha256"] == pin["files"][0]["sha256"],
            "model/binary/provenance drift")
    require(inputs["loaded_audit_sha256"] == digest(args.loaded / "audit.json") and
            inputs["fixed_serial_audit_sha256"] == digest(Path("target/scheduler-fixes/artifacts/q35-fixed-serial/audit.json")) and
            record["fixture_sha256"] == inputs["fixture_sha256"] == digest(args.loaded / "fixtures.json"),
            "reference hash drift")
    subject = next(row for row in fixture["load"] if row["id"] == "load-0100")
    require(subject["native_prompt_tokens"] == 512 and subject["max_tokens"] == 32,
            "fixture token counts")
    body = host.body(record["model"], subject["messages"][0]["content"], 32, stream=True)
    require(record["request"] == body, "request body drift")
    expected = [(first, arm) for first in (1, 16)
                for arm in (ARMS if first == 1 else tuple(reversed(ARMS)))]
    require([(cell["first"], cell["arm"]) for cell in record["cells"]] == expected,
            "process order")
    results = {}
    count = 0
    for cell in record["cells"]:
        first, arm = cell["first"], cell["arm"]
        directory = root / "evidence" / cell["directory"]
        require(cell["directory"] == f"c{first}-first-{arm}" and cell["complete"], "process identity")
        launch = read(directory / "launch.json")["argv"]
        require(launch[0] == str(Path(record["identity"]["cache"]) / "llama-build/bin/llama-server") and
                "--no-cache-prompt" in launch and
                (("--no-kv-unified" in launch) == (arm == "llama-slots")) and
                (("--kv-unified" in launch) == (arm == "llama-unified")), "vanilla launch/cache/layout")
        native = suite.resolved(directory / "server.log", arm, 16)
        require(native == cell["resolved"] and native["gpu_layers"] == 33, "native setting/offload")
        before, after = (read(directory / f"{name}.json") for name in ("before", "after"))
        require(before["native_thermal_pressure_level"] == after["native_thermal_pressure_level"] == 0,
                "thermal boundary")
        sizes = (first, 16 if first == 1 else 1) * 2
        require(len(cell["waves"]) == 4, "wave count")
        for index, (item, size) in enumerate(zip(cell["waves"], sizes)):
            require(item["index"] == index and item["concurrency"] == size and
                    item["file"] == f"{index}-c{size}.json", "wave registration")
            path = directory / item["file"]
            require(digest(path) == item["sha256"], "wave file hash")
            wave = read(path)
            names = {f"subject-{number:02d}" for number in range(size)}
            require(set(wave["requests"]) == names and
                    read(directory / f"{index}-requests.json") == {name: body for name in names},
                    "exact wave requests/responses")
            require(peak_inflight(wave["requests"]) == size, "offered concurrency not achieved")
            texts = []
            for response in wave["requests"].values():
                require(response["usage"]["prompt_tokens"] == 512 and
                        response["usage"]["completion_tokens"] == 32 and
                        response["cached_tokens"] == 0 and response["finish_reason"] == "length" and
                        response["offset_ms"] == 0 and response["send_ms"] < response["end_ms"] and
                        hashlib.sha256(response["text"].encode()).hexdigest() == response["text_sha256"],
                        "completion/cache/text integrity")
                start = wave["start_unix_ns"] + response["send_ms"] * 1e6
                end = wave["start_unix_ns"] + response["end_ms"] * 1e6
                require(before["wall_ns"] <= start < end <= after["wall_ns"], "request outside server lifetime")
                texts.append(response["text"])
                count += 1
            results[first, arm, index] = texts
    require(count == 136, "response count")
    summary = {}
    for arm in ARMS:
        serial = [read(args.serial / "evidence" / f"r{round_number}-{arm}" / "load-0100-normal.json")
                  ["choices"][0]["text"] for round_number in (0, 1)]
        require(serial[0] == serial[1], "archived serial drift")
        loaded = loaded_text(args.loaded, arm)
        solo = [text for first in (1, 16) for index in range(4)
                if (index % 2 == 0) == (first == 1) for text in results[first, arm, index]]
        grouped = [text for first in (1, 16) for index in range(4)
                   if (index % 2 == 1) == (first == 1) for text in results[first, arm, index]]
        require(len(solo) == 4 and len(grouped) == 64, "per-arm response count")
        distribution = Counter(hashlib.sha256(text.encode()).hexdigest() for text in grouped)
        examples = {hashlib.sha256(text.encode()).hexdigest(): text[:100] for text in grouped}
        summary[arm] = {
            "serial_exact_count": sum(text == serial[0] for text in solo),
            "serial_count": len(solo),
            "grouped_matches_serial": sum(text == serial[0] for text in grouped),
            "grouped_matches_c128": sum(text == loaded for text in grouped),
            "grouped_count": len(grouped),
            "grouped_distribution": dict(sorted(distribution.items())),
            "grouped_variants": [{"sha256": sha, "count": count, "prefix": examples[sha]}
                                 for sha, count in sorted(distribution.items())],
            "serial_sha256": hashlib.sha256(serial[0].encode()).hexdigest(),
            "c128_sha256": hashlib.sha256(loaded.encode()).hexdigest(),
        }
    output = {"job_id": "job_8m3anrpf87t0h1j", "spec_sha256": digest(args.spec),
              "responses_verified": count, "processes": 4, "waves": 16,
              "model_pin_exact": True, "cache_off": True, "full_metal": True,
              "summary": summary,
              "disposition": "Unchanged vanilla c1/c16 load-0100 outputs audited; exact c128 cross-engine mismatches remain failed. No timing or native operator attribution."}
    (root / "audit.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
