"""Registered shape-order diagnostic; never a candidate speed qualification."""

import json
import os
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

from .ci_artifacts import digest, read_json, write_json
from .ci_process import command, start, stop
from .inference_probe import PARAGRAPHS, payload, send, validate_response

ROOT = Path(__file__).resolve().parents[2]
REGISTRATIONS = {kind: ROOT / f"ci/inference-noise-{kind}.json" for kind in ("pilot", "profile")}


def registration(kind="pilot"):
    return read_json(REGISTRATIONS[kind])


def schedule(baseline, kind="pilot"):
    if baseline != registration(kind)["baseline_sha"]:
        raise ValueError("noise pilot baseline differs from registration")
    return [{"experiment": "noise", "round": index, "arm": "a", "source_sha": baseline,
             "diagnostic": kind} for index in range(len(registration(kind)["arms"]))]


def requests(condition, repetitions):
    cells = [(shape, repeat) for shape in range(len(PARAGRAPHS)) for repeat in range(repetitions)]
    if condition == "cycled":
        cells.sort(key=lambda cell: (cell[1], cell[0]))
    elif condition != "blocked":
        raise ValueError("unknown noise pilot condition")
    return cells


def request(model, position, shape, repeat, warmup):
    result = payload(model, shape, warmup)
    text = result["messages"][0]["content"].split("\n", 1)[1]
    # Match tokenization length while preventing early-prefix reuse. Live gates
    # verify the token counts; textual width alone is not tokenization proof.
    salt = position * 100 + repeat * len(PARAGRAPHS) + shape
    result["messages"][0]["content"] = f'{"W" if warmup else "M"}{salt:03d}\n{text}'
    return result


def measure(model, output, arm, kind="pilot"):
    design = registration(kind)
    evidence = {"protocol": design["protocol"], "registration": design,
                "registration_sha256": digest(REGISTRATIONS[kind]), "arm": arm,
                "status": "incomplete", "samples": []}
    counts = {}
    try:
        for position, condition in enumerate(design["arms"][arm]):
            for shape, repeat in requests(condition, design["repetitions_per_condition_shape"]):
                sample = {"condition": condition, "position": position, "shape": shape, "repetition": repeat}
                evidence["samples"].append(sample)
                for warmup, name in ((True, "warmup"), (False, "measured")):
                    expected = request(model, position, shape, repeat, warmup)
                    record = {"started_at": datetime.now(timezone.utc).isoformat()}
                    sample[name] = record
                    send(expected, record)
                    record["completed_at"] = datetime.now(timezone.utc).isoformat()
                    count = validate_response(record, expected)
                    if count != counts.setdefault(shape, count):
                        raise ValueError("noise pilot prompt token shape changed")
        evidence["status"] = "passed"
    except BaseException as error:
        evidence.update(status="failed", error=str(error))
        raise
    finally:
        write_json(output / f"noise-{kind}.json", evidence)


def probe(arm, kind="pilot"):
    return partial(measure, arm=arm, kind=kind)


@contextmanager
def diagnostics(output, kind="pilot"):
    # Run the pinned wrapper outside measured requests. Its records and any
    # unsupported profiler capabilities remain in each uploaded arm archive.
    doctor = command([str(ROOT / "scripts/inferencekit.sh"), "doctor"], timeout=180, capture_stdout=True,
                     overrides={"INFERENCEKIT_RUNS_DIR": str(output / "inferencekit")})
    (output / "doctor.log").write_text(doctor)
    done = threading.Event()
    trace = "SKIPPY_TELEMETRY_OTLP_FILE"
    previous_trace = os.environ.get(trace)

    def host_samples():
        with (output / "host.jsonl").open("w") as stream:
            while not done.is_set():
                record = {"at": datetime.now(timezone.utc).isoformat(),
                          "load": os.getloadavg(), "cpu": Path("/proc/stat").read_text().splitlines()[0]}
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                done.wait(1)

    with (output / "gpu.csv").open("wb") as stream:
        gpu = start(["nvidia-smi", "--query-gpu=timestamp,temperature.gpu,power.draw,clocks.sm,clocks.mem,"
                     "utilization.gpu,utilization.memory,pstate", "--format=csv", "--loop-ms=1000"],
                    overrides={"TZ": "UTC"}, stdout=stream, stderr=subprocess.STDOUT)
        thread = threading.Thread(target=host_samples, daemon=True)
        thread.start()
        try:
            if kind == "profile":
                os.environ[trace] = str(output / "phases.jsonl")
            yield
            if gpu.poll() is not None:
                raise RuntimeError("noise pilot GPU telemetry stopped; inspect gpu.csv")
            if kind == "profile" and not (output / "phases.jsonl").stat().st_size:
                raise RuntimeError("noise phase profile is empty")
        finally:
            if previous_trace is None:
                os.environ.pop(trace, None)
            else:
                os.environ[trace] = previous_trace
            done.set()
            thread.join(timeout=5)
            stop(gpu)
