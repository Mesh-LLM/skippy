#!/usr/bin/env python3
"""Fetch, qualify, or report a hosted bundle using an installed trusted harness."""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from lib import ci_artifacts as artifacts
from lib.ci_process import cancel_on_signal, capture
from lib.ci_smoke import smoke
from lib.ci_targets import targets
from lib.inference_checks import request, score
from lib.lab_artifact import VARIANT, fetch, report, verify_input


def probe(endpoint, output):
    endpoint = endpoint.removesuffix("/v1")
    score("qwen3-ci", output, "pr", endpoint=endpoint)
    samples = []
    first_text = None
    # One warm-up followed by three serial requests. This includes HTTP,
    # prefill and decode time, not isolated decode throughput or a regression gate.
    prompt = "List the integers from 1 to 100, separated by commas."
    for trial in range(4):
        start = time.monotonic()
        response = json.loads(request("qwen3-ci", prompt, 128, 120, endpoint=endpoint))
        seconds = time.monotonic() - start
        tokens = response["usage"]["completion_tokens"]
        if type(tokens) is not int or tokens <= 0 or seconds <= 0:
            raise ValueError("empty or invalid performance sample")
        text = response["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("performance response missing assistant text")
        if first_text is not None and text != first_text:
            raise ValueError("performance repeats produced different text")
        first_text = text
        if trial:
            samples.append({"seconds": seconds, "completion_tokens": tokens,
                            "end_to_end_tokens_per_second": tokens / seconds, "response": response})
        artifacts.write_json(output / "performance.json", {
            "scope": "warm serial HTTP throughput; includes prefill; not a regression gate",
            "prompt": prompt, "max_tokens": 128, "temperature": 0, "seed": 42,
            "warmup_requests": 1, "samples": samples,
        })
    return samples


def hardware():
    return {
        "chip": capture(["sysctl", "-n", "machdep.cpu.brand_string"]).strip(),
        "model": capture(["sysctl", "-n", "hw.model"]).strip(),
        "memory_bytes": int(capture(["sysctl", "-n", "hw.memsize"]).strip()),
        "os_build": capture(["sw_vers", "-buildVersion"]).strip(),
    }


def qualify(directory):
    receipt = verify_input(directory)
    harness = artifacts.source()
    if capture(["git", "status", "--porcelain", "--untracked-files=all", "--", "scripts", "ci", "Justfile"]).strip():
        raise ValueError("installed harness must have no modified or untracked policy/code")
    hosts, runtimes = targets()
    runtime = runtimes[VARIANT]
    output = directory / "evidence"
    output.mkdir(exist_ok=True)
    if (output / "lab-result.json").exists():
        raise ValueError("qualification already attempted; fetch into a fresh directory to repeat")
    result = {
        "schema_version": 1, "input": receipt, "harness_source": harness,
        "platform": platform.platform(), "machine": platform.machine(),
        "scope": "Qwen3 dense Metal offload, scored endpoint repeats and bounded HTTP performance",
        "status": "failed",
    }
    try:
        result["hardware"] = hardware()
        # Fetch/report are separate credentialed operations. Neither the
        # --version probe nor the server inherits those process credentials.
        with tempfile.TemporaryDirectory() as home:
            child_env = {"PATH": os.defpath, "HOME": home, "USERPROFILE": home, "LANG": "C.UTF-8"}
            smoke(hosts[runtime["host"]], runtime, directory, expected_source=receipt["source"],
                  probe=probe, child_env=child_env)
        performance = artifacts.read_json(output / "performance.json")
        samples = performance["samples"]
        result.update(status="passed", model_sha256=artifacts.read_json(output / "smoke.json")["model_sha256"],
                      median_end_to_end_tokens_per_second=statistics.median(
                          sample["end_to_end_tokens_per_second"] for sample in samples))
    except (Exception, KeyboardInterrupt) as error:
        result["error"] = str(error)
        raise
    finally:
        result["evidence"] = artifacts.files(output)
        artifacts.write_json(output / "lab-result.json", result)
        print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("fetch")
    download.add_argument("run_id", type=int)
    download.add_argument("directory", type=Path)
    for name in ("run", "report"):
        commands.add_parser(name).add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.command == "fetch":
        if args.run_id <= 0:
            raise ValueError("run id must be positive")
        print(json.dumps(fetch(args.run_id, directory), indent=2))
    elif args.command == "run":
        qualify(directory)
    else:
        report(directory)


if __name__ == "__main__":
    try:
        with cancel_on_signal():
            main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
