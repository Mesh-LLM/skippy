"""Bounded Instruments captures; raw traces never enter JY artifacts."""

import json
import subprocess
import time
import xml.etree.ElementTree as ET

from .profile_kv_host import HostUnavailable, URL, body, command, competing_work, require_idle, save, stop, thermal_pressure
from urllib.request import Request, urlopen


def stream(model, prompt, tokens):
    request = Request(URL + "/v1/completions", data=json.dumps(body(model, prompt, tokens, True)).encode(),
                      headers={"Content-Type": "application/json"})
    result = {"start_wall_ns": time.time_ns(), "start_monotonic_ns": time.monotonic_ns(), "events_ns": []}
    usage = None
    with urlopen(request, timeout=180) as response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            event = json.loads(data)
            if event.get("error"):
                raise RuntimeError(str(event["error"]))
            if event.get("usage"):
                usage = event["usage"]
            if any(choice.get("text") for choice in event.get("choices", [])):
                result["events_ns"].append(time.monotonic_ns())
    result.update(end_wall_ns=time.time_ns(), end_monotonic_ns=time.monotonic_ns(), usage=usage)
    if not usage or usage.get("completion_tokens") != tokens:
        raise ValueError(f"capture output length mismatch: {usage}")
    return result


def capture(child, model, prompt, directory, env, seconds=20):
    require_idle(env, child.pid)
    directory.mkdir(parents=True)
    trace = directory / "metal.trace"
    args = ["xcrun", "xctrace", "record", "--template", "Metal System Trace",
            "--instrument", "Time Profiler", "--attach", str(child.pid),
            "--time-limit", f"{seconds}s", "--output", str(trace), "--no-prompt"]
    stream(model, prompt["messages"][0]["content"], prompt["max_tokens"])
    started = time.time_ns()
    requests = []
    with (directory / "capture.log").open("wb") as output:
        profiler = subprocess.Popen(args, env=env, stdout=output, stderr=output, start_new_session=True)
        try:
            time.sleep(5)
            deadline = time.monotonic() + seconds - 5
            while time.monotonic() < deadline and profiler.poll() is None:
                competitors = competing_work(env, child.pid)
                if competitors:
                    raise HostUnavailable(f"competing work during capture: {competitors}")
                pressure = thermal_pressure()
                if pressure:
                    raise HostUnavailable(f"non-nominal thermal pressure during capture: {pressure}")
                requests.append(stream(model, prompt["messages"][0]["content"], prompt["max_tokens"]))
            profiler.wait(timeout=120)
        finally:
            if profiler.poll() is None:
                stop(profiler)
    result = {"trace": str(trace), "argv": args, "environment_names": sorted(env),
              "start_wall_ns": started, "exit_code": profiler.returncode, "requests": requests,
              "timing_scope": "observed under tracing; never end-to-end performance evidence"}
    if profiler.returncode == 0 and trace.exists():
        toc = command(["xcrun", "xctrace", "export", "--input", str(trace), "--toc"],
                      env, timeout=120, capture_output=True, text=True)
        (directory / "toc.xml").write_text(toc.stdout)
        tree = ET.fromstring(toc.stdout)
        result["tables"] = [dict(node.attrib) for node in tree.iter("table")]
        result["status"] = "CAPTURED"
    else:
        result["status"] = "FAILED"
        result["error"] = (directory / "capture.log").read_text(errors="replace")[-8000:]
    save(directory / "capture.json", result)
    return result
