#!/usr/bin/env python3
"""Render a sealed, bounded JY cell for the four-model cache-off campaign."""

import argparse
import ast
import base64
import hashlib
import json
import lzma
from pathlib import Path
import runpy
import shlex

from lib import profile_scheduler_suite as suite


def selected_source(path, names):
    source = Path(path).read_text()
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign)) or getattr(node, "name", None) in names:
            start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
            selected.extend(lines[start - 1:node.end_lineno])
            selected.append("\n\n")
    return "".join(selected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=suite.MODELS, required=True)
    parser.add_argument("--phase", choices=("screen", "load", "native-admission", "correctness", "correctness-stream", "timeline", "path-isolation", "path-trace", "path-batch4096", "path-micro455", "path-micro511", "batch-history", "flash-history"), required=True)
    parser.add_argument("--round", type=int, choices=range(4), action="append", required=True)
    parser.add_argument("--lease", type=int, default=3600)
    parser.add_argument("--concurrency", type=int, choices=(64, 128, 256), default=64)
    parser.add_argument("--ref-e2e-ms", type=float)
    parser.add_argument("--cooldown", type=int, default=30)
    parser.add_argument("--startup-timeout", type=int, choices=(300, 1800), default=300)
    parser.add_argument("--generation-queue-capacity", type=int, choices=(256,))
    parser.add_argument("--trace-baseline", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.trace_baseline and (args.phase != "batch-history" or args.model != "qwen3.5-4b-q4-k-m"):
        parser.error("baseline batch trace is registered only for Qwen3.5 batch-history")
    if args.phase in ("path-isolation", "path-trace", "path-batch4096", "path-micro455", "path-micro511") and args.model != "qwen3.6-35b-a3b-q4-k-m":
        parser.error("path-isolation is registered only for Qwen3.6")
    if args.phase == "batch-history" and args.model != "qwen3.5-4b-q4-k-m":
        parser.error("batch-history is registered only for Qwen3.5-4B")
    if args.phase == "flash-history" and args.model != "qwen3.8-flash-next-iq4-xs":
        parser.error("flash-history is registered only for the exact Flash pin")
    if args.generation_queue_capacity is not None and (
            args.model != "qwen3-coder-30b-a3b-q4-k-m" or
            args.phase != "load" or args.concurrency != 256):
        parser.error("explicit queue retest is registered only for Coder c256 load")
    paths = ["scripts/profile-scheduler-suite.py", "scripts/lib/profile_scheduler_suite.py",
             "scripts/lib/profile_kv_host.py", "scripts/profile-kv-scheduler.py",
             "scripts/profile-kv-scheduler-qualification.py"]
    files = {p: Path(p).read_text() for p in paths}
    runner = paths[0]
    if args.phase == "native-admission":
        del files[runner]
        runner = "scripts/profile-scheduler-native-admission.py"
        files[runner] = Path(runner).read_text()
        admission = "scripts/profile-scheduler-model-admission.py"
        files[admission] = selected_source(admission, {"metal_limit"})
    if args.phase in ("correctness", "correctness-stream"):
        del files[runner]
        runner = "scripts/profile-scheduler-correctness.py"
        files[runner] = Path(runner).read_text()
    if args.phase == "timeline":
        del files[runner]
        runner = "scripts/profile-scheduler-timeline.py"
        files[runner] = Path(runner).read_text()
        files["scripts/lib/profile_kv_xml.py"] = Path("scripts/lib/profile_kv_xml.py").read_text()
    if args.phase in ("path-isolation", "path-trace", "path-batch4096", "path-micro455", "path-micro511"):
        del files[runner]
        runner = "scripts/profile-scheduler-path-isolation.py"
        files[runner] = Path(runner).read_text()
    if args.phase == "batch-history":
        del files[runner]
        runner = "scripts/profile-scheduler-batch-history.py"
        files[runner] = Path(runner).read_text()
    if args.phase == "flash-history":
        del files[runner]
        runner = "scripts/profile-scheduler-flash-history.py"
        files[runner] = Path(runner).read_text()
    if args.model in suite.QUALIFICATION_IDS:
        files["ci/inference-artifacts.json"] = Path("ci/inference-artifacts.json").read_text()
    replay_functions = {"digest", "tokenize", "fixtures", "request", "wave"}
    if args.phase == "timeline":
        replay_functions.update(("trace_wave", "skippy_trace", "linked_schedule"))
    files[paths[3]] = selected_source(paths[3], replay_functions)
    files[paths[4]] = selected_source(paths[4], {"file_digest", "qualified_arms"})
    files[paths[2]] = selected_source(paths[2], {"HostUnavailable", "thermal_pressure", "save", "clean_env",
        "command", "post", "telemetry", "competing_work", "require_idle", "stop", "launch_args", "server", "body"})
    packed = base64.b85encode(lzma.compress(json.dumps(files).encode())).decode()
    command = ("set -eu\npython3 - <<'INSTALL'\nimport base64,json,lzma\nfrom pathlib import Path\n"
               "for name,data in json.loads(lzma.decompress(base64.b85decode(" + repr(packed) + "))).items():\n"
               " p=Path(name); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(data)\nINSTALL\n")
    command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                'python3 ' + runner + ' --cache "$HOME/.cache/skippy/kv-profile/'
                + suite.SOURCE + '" --output "$JIANYANG_ARTIFACT_DIR" ')
    options = ["--model", args.model, "--phase", args.phase, "--concurrency", str(args.concurrency),
               "--cooldown", str(args.cooldown), "--startup-timeout", str(args.startup_timeout)]
    for r in args.round:
        options += ["--round", str(r)]
    if args.ref_e2e_ms is not None:
        options += ["--ref-e2e-ms", str(args.ref_e2e_ms)]
    if args.generation_queue_capacity is not None:
        options += ["--generation-queue-capacity", str(args.generation_queue_capacity)]
    if args.phase in ("native-admission", "correctness", "correctness-stream", "timeline", "path-isolation", "path-trace", "path-batch4096", "path-micro455", "path-micro511", "batch-history", "flash-history"):
        options = ["--model", args.model]
    if args.phase in ("path-trace", "path-batch4096", "path-micro455", "path-micro511"):
        options += ["--trace-driver"]
        if args.phase == "path-batch4096":
            options += ["--batch-size", "4096"]
        if args.phase in ("path-micro455", "path-micro511"):
            options += ["--microbatch-size", args.phase.removeprefix("path-micro")]
    if args.phase == "correctness-stream":
        options += ["--stream-normal"]
    if args.trace_baseline:
        options += ["--trace-baseline"]
    if args.phase in ("correctness", "correctness-stream"):
        options += ["--startup-timeout", str(args.startup_timeout)]
    command += shlex.join(options) + "\n"
    if len(command) > 16384:
        raise ValueError(f"JY command exceeds limit: {len(command)}")
    manifest = {"source": suite.SOURCE, "patch": suite.PATCH, "model": args.model,
                "scripts": {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()}}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    util = runpy.run_path("scripts/profile-kv-spec.py")
    template = json.loads(Path("evals/kv-profiling/jy/measure.json.in").read_text())
    spec = util["replace"](template, {"@SOURCE_SHA@": suite.SOURCE, "@MODEL_MANIFEST_SHA256@": digest,
         "@COMMAND@": command, "@PHASE@": "scheduler-suite-" + args.phase, "@DURATION@": args.lease})
    job = spec["spec"]
    job["name"] = f"Cache-off {args.model} {args.phase} rounds {args.round}"
    job["labels"].update(campaign="scheduler-cache-off-four-models-20261124", patch=suite.PATCH)
    if args.phase in ("path-micro455", "path-micro511"):
        job["labels"]["campaign"] = "scheduler-fixes"
    if args.generation_queue_capacity is not None:
        job["labels"]["generation_queue_capacity"] = str(args.generation_queue_capacity)
    job["harness"].update(version="scheduler-suite-v1", warmup_requests=4 * len(args.round),
                          request_count=4 * len(args.round) * (11 if args.phase == "screen" else args.concurrency),
                          concurrency=[1, 4] if args.phase == "screen" else [args.concurrency])
    job["harness"]["trace"]["ref"] = "cache-off-synthetic-fixed-token-prompts-v1"
    if args.phase == "native-admission":
        job["harness"].update(warmup_requests=0, request_count=4, concurrency=[1])
    if args.phase in ("correctness", "correctness-stream"):
        job["harness"].update(warmup_requests=0,
                              request_count=32 * len(suite.CORRECTNESS_CASES[args.model]), concurrency=[1])
    if args.phase == "timeline":
        job["harness"].update(warmup_requests=4, request_count=16, concurrency=[4])
    if args.phase == "path-isolation":
        job["harness"].update(warmup_requests=0, request_count=32, concurrency=[1])
    if args.phase in ("path-trace", "path-batch4096", "path-micro455", "path-micro511"):
        job["harness"].update(warmup_requests=0, request_count=8, concurrency=[1])
    if args.phase == "batch-history":
        job["harness"].update(warmup_requests=0, request_count=68 if args.trace_baseline else 272, concurrency=[1, 16])
        if args.trace_baseline:
            job["name"] = "Qwen3.5 unchanged baseline c1/c16 scheduler trace"
            job["labels"].update(campaign="scheduler-fixes", phase="qwen35-batch-trace")
    if args.phase == "flash-history":
        job["harness"].update(warmup_requests=0, request_count=80, concurrency=[1, 4])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, indent=2) + "\n")
    args.out.with_suffix(".inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(args.out, "command bytes", len(command))


if __name__ == "__main__":
    main()
