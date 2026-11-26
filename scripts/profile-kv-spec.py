#!/usr/bin/env python3
"""Render immutable JY specs from the checked-in campaign templates."""

import argparse
import base64
import hashlib
import json
import shlex
import subprocess
import zlib
from pathlib import Path


def replace(value, replacements):
    if isinstance(value, dict):
        return {k: replace(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [replace(v, replacements) for v in value]
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        for key, item in replacements.items():
            value = value.replace(key, str(item))
    return value


def install(files):
    contents = {name: Path(name).read_text() for name in files}
    encoded = base64.b64encode(zlib.compress(json.dumps(contents).encode(), 9)).decode()
    return ("python3 - <<'INSTALL_KV_PROFILE'\nimport base64, json, zlib\nfrom pathlib import Path\n"
            "files = json.loads(zlib.decompress(base64.b64decode(" + repr(encoded) + ")))\n"
            "for name, data in files.items():\n p = Path(name); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data)\n"
            "INSTALL_KV_PROFILE\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("build", "pilot", "screen", "profiles", "timing", "campaign", "inspect", "analyze", "correctness", "correctness-repair", "attribution", "cold-control", "chunk-control", "candidate", "candidate-resume", "scheduler-cold", "scheduler-cache", "scheduler-trace", "scheduler-cache-trace", "scheduler-metal-trace", "scheduler-cache-analysis", "scheduler-cache-mixed", "scheduler-cache-mixed-trace"))
    parser.add_argument("--pilot-root")
    parser.add_argument("--capture-root")
    parser.add_argument("--duration-s", type=int)
    parser.add_argument("--model-key", action="append")
    parser.add_argument("--round", type=int, choices=range(6), action="append")
    parser.add_argument("--row-control", action="store_true")
    parser.add_argument("--arm", choices=("llama-slots", "llama-unified", "skippy", "skippy-ordinary"), action="append")
    parser.add_argument("--wave-cooldown", type=int, choices=range(0, 31), default=0, metavar="SECONDS")
    parser.add_argument("--cell-cooldown", type=int, choices=range(0, 61), default=15, metavar="SECONDS")
    parser.add_argument("--focus-mixed", action="store_true")
    parser.add_argument("--reference-root")
    parser.add_argument("--resume-record", type=Path)
    parser.add_argument("--source", default=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if len(args.source) != 40 or any(c not in "0123456789abcdef" for c in args.source):
        parser.error("source must be an immutable full Git SHA")
    cache = "$HOME/.cache/skippy/kv-profile/" + args.source
    if args.model_key:
        known = {model["key"] for model in json.loads(Path("evals/kv-validation/models.json").read_text())}
        if ((args.phase not in ("pilot", "screen", "profiles", "timing", "campaign")
             and not args.phase.startswith("scheduler-")) or set(args.model_key) - known):
            parser.error("model selection requires an inference phase and exact pinned fixture keys")
    if args.phase == "build":
        command = "set -eu\ncd skippy\n" + install(["scripts/profile-kv-build.sh"])
        command += "mkdir -p target\ncat > target/profile-build.just <<'JUST'\nset working-directory := \"..\"\nbuild:\n    @bash scripts/profile-kv-build.sh\nJUST\n"
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'JIANYANG_ARTIFACT_DIR="$JIANYANG_ARTIFACT_DIR" PROFILE_SOURCE_SHA=' + shlex.quote(args.source)
                    + ' just --justfile target/profile-build.just build\n')
        template = "build"
    elif args.phase in ("candidate", "candidate-resume"):
        if not args.reference_root:
            parser.error("candidate needs --reference-root")
        files = ["scripts/profile-kv-candidate.py", "scripts/lib/profile_kv_host.py"]
        if args.phase == "candidate-resume":
            if not args.resume_record:
                parser.error("candidate-resume needs --resume-record")
            files += ["scripts/profile-kv-candidate-audit.py", str(args.resume_record)]
        command = "set -eu\n" + install(files)
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-candidate.py --cache "' + cache
                    + '" --reference-root ' + shlex.quote(args.reference_root)
                    + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        if args.phase == "candidate-resume":
            command = command.rstrip() + ' --resume-record ' + shlex.quote(str(args.resume_record)) + '\n'
        template = "measure"
    elif args.phase == "scheduler-cache-analysis":
        if not args.capture_root or args.duration_s is None:
            parser.error("scheduler cache analysis needs --capture-root and --duration-s")
        command = "set -eu\n" + install(["scripts/profile-kv-cache-analyze.py", "scripts/lib/profile_kv_host.py",
                                        "scripts/lib/profile_kv_xml.py"])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-cache-analyze.py --capture-root ' + shlex.quote(args.capture_root)
                    + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        template = "measure"
    elif args.phase.startswith("scheduler-"):
        if not args.model_key or not args.round or args.duration_s is None:
            parser.error("scheduler replays need --model-key, --round and --duration-s")
        mode = args.phase.removeprefix("scheduler-")
        command = "set -eu\n" + install(["scripts/profile-kv-scheduler.py", "scripts/lib/profile_kv_host.py",
                                        "scripts/lib/profile_kv_xml.py"])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-scheduler.py --cache "' + cache
                    + '" --mode ' + mode + ' --output "$JIANYANG_ARTIFACT_DIR"'
                    + ''.join(' --model ' + shlex.quote(key) for key in args.model_key)
                    + ''.join(' --round ' + str(round_index) for round_index in args.round)
                    + ''.join(' --arm ' + arm for arm in (args.arm or []))
                    + ' --wave-cooldown ' + str(args.wave_cooldown)
                    + ' --cell-cooldown ' + str(args.cell_cooldown)
                    + (' --focus-mixed' if args.focus_mixed else '')
                    + (' --row-control' if args.row_control else '') + '\n')
        template = "measure"
    elif args.phase == "chunk-control":
        fixture = "evals/kv-profiling/followup-cold-fixture.json"
        command = "set -eu\n" + install(["scripts/profile-kv-chunk-control.py", "scripts/lib/profile_kv_host.py", fixture])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-chunk-control.py --cache "' + cache
                    + '" --fixture ' + shlex.quote(fixture) + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        template = "measure"
    elif args.phase == "cold-control":
        fixture = "evals/kv-profiling/followup-cold-fixture.json"
        command = "set -eu\n" + install(["scripts/profile-kv-cold-control.py", "scripts/lib/profile_kv_host.py", fixture])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-cold-control.py --cache "' + cache
                    + '" --fixture ' + shlex.quote(fixture) + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        template = "measure"
    elif args.phase == "attribution":
        if not args.reference_root:
            parser.error("attribution needs --reference-root")
        command = "set -eu\n" + install(["scripts/profile-kv-attribution.py", "scripts/lib/profile_kv_host.py",
                                          "scripts/lib/profile_kv_capture.py", "scripts/lib/profile_kv_xml.py"])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-attribution.py --cache "' + cache
                    + '" --reference-root ' + shlex.quote(args.reference_root)
                    + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        template = "measure"
    elif args.phase in ("correctness", "correctness-repair"):
        if not args.reference_root:
            parser.error("correctness needs --reference-root")
        command = "set -eu\n" + install(["scripts/profile-kv-correctness.py", "scripts/lib/profile_kv_host.py"])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-correctness.py --cache "' + cache
                    + '" --reference-root ' + shlex.quote(args.reference_root)
                    + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        if args.phase == "correctness-repair":
            command = command.rstrip() + ' --variant skippy-base --variant skippy-ordinary --top-logprobs 5 --reuse-verified-model --skip-tool-inventory\n'
        template = "measure"
    elif args.phase == "analyze":
        if not args.capture_root:
            parser.error("analyze needs --capture-root")
        command = "set -eu\n" + install(["scripts/profile-kv-analyze.py", "scripts/lib/profile_kv_host.py", "scripts/lib/profile_kv_xml.py"])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-analyze.py --capture-root ' + shlex.quote(args.capture_root)
                    + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        template = "measure"
    elif args.phase == "inspect":
        if not args.pilot_root:
            parser.error("inspect needs --pilot-root")
        command = "set -eu\n" + install(["scripts/profile-kv-inspect.py", "scripts/lib/profile_kv_host.py"])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-inspect.py --cache "' + cache
                    + '" --pilot-root ' + shlex.quote(args.pilot_root)
                    + ' --output "$JIANYANG_ARTIFACT_DIR"\n')
        template = "measure"
    else:
        command = "set -eu\n" + install(["scripts/profile-kv-run.py", "scripts/lib/profile_kv_host.py",
                                        "scripts/lib/profile_kv_capture.py"])
        command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                    'python3 scripts/profile-kv-run.py --cache "' + cache
                    + '" --output "$JIANYANG_ARTIFACT_DIR"' + (' --pilot' if args.phase == 'pilot' else ' --screen' if args.phase == 'screen' else ' --timing-only --paired-rounds 6' if args.phase == 'timing' else '')
                    + ''.join(' --model-key ' + shlex.quote(key) for key in (args.model_key or [])) + '\n')
        if args.phase == "profiles":
            if not args.reference_root:
                parser.error("profiles needs --reference-root")
            command = command.rstrip() + ' --profiles-only --reference-root ' + shlex.quote(args.reference_root) + '\n'
        template = "measure"
    replacements = {"@SOURCE_SHA@": args.source, "@COMMAND@": command, "@PHASE@": args.phase,
                    "@DURATION@": 900 if args.phase in ("pilot", "inspect", "profiles") else 300 if args.phase == "analyze" else 2400 if args.phase == "screen" else 4800,
                    "@MODEL_MANIFEST_SHA256@": hashlib.sha256(Path("evals/kv-validation/models.json").read_bytes()).hexdigest()}
    spec = replace(json.loads(Path(f"evals/kv-profiling/jy/{template}.json.in").read_text()), replacements)
    selected_count = len(set(args.model_key)) if args.model_key else 4
    if args.phase == "scheduler-cache-analysis":
        spec["spec"]["harness"].update(version="scheduler-kv-replay-v1", request_count=1,
                                        args={"inference_requests": 0, "scope": "offline indexing of existing private traces"})
    elif args.phase.startswith("scheduler-"):
        harness = spec["spec"]["harness"]
        cache_replay = args.phase in ("scheduler-cache", "scheduler-cache-trace")
        mixed_only = args.phase.startswith("scheduler-cache-mixed")
        request_count = 19 if cache_replay else 4 if mixed_only else 6 if args.focus_mixed else 14
        harness.update(version="scheduler-kv-replay-v1",
                       concurrency=[1, 4] if cache_replay or mixed_only or args.focus_mixed else [1, 2, 4],
                       warmup_requests=1, request_count=request_count
                       * (len(set(args.arm)) if args.arm else 3 + args.row_control)
                       * len(args.model_key) * len(args.round))
        harness["trace"]["ref"] = "resident-prefix-churn" if cache_replay else "staggered-prefill-with-decode-anchors"
        spec["spec"]["labels"]["campaign"] = "skippy-scheduler-kv-20261124"
    elif args.phase == "candidate-resume":
        record = json.loads(args.resume_record.read_text())
        complete = 0
        while {r["arm"] for r in record["timing"] if r["pair"] == complete} == {"base", "ordinary"}:
            complete += 1
        if complete >= 6:
            parser.error("candidate already has all six pairs")
        spec["spec"]["harness"]["request_count"] = 8 * (6 - complete)
        spec["spec"]["harness"]["args"] = {"quality_requests": 0, "timed_requests": 8 * (6 - complete), "scope": "same preregistered candidate; rerun whole interrupted pair; admit only complete count/host-valid pairs"}
    elif args.phase == "candidate":
        spec["spec"]["harness"]["request_count"] = 48
        spec["spec"]["harness"]["args"] = {"quality_requests": 40, "timed_requests": 48, "scope": "single-flag candidate; six fresh AB/BA pairs on hybrid-MoE after four-model exact screen"}
    elif args.phase == "chunk-control":
        spec["spec"]["harness"]["request_count"] = 12
        spec["spec"]["harness"]["args"] = {"timed_requests": 0, "scope": "single-token microbatch numerical diagnostic, not optimization"}
    elif args.phase == "cold-control":
        spec["spec"]["harness"]["request_count"] = 28
        spec["spec"]["harness"]["args"] = {"timed_requests": 0, "scope": "exact-token cold-prefill numerical controls"}
    elif args.phase == "attribution":
        spec["spec"]["harness"]["request_count"] = 18
        spec["spec"]["harness"]["args"] = {"timed_requests": 0, "scope": "native GPU diagnostic op-class attribution"}
    elif args.phase in ("correctness", "correctness-repair"):
        spec["spec"]["harness"]["request_count"] = 12 if args.phase == "correctness-repair" else 24
        spec["spec"]["harness"]["args"] = {"timed_requests": 0, "scope": "fixed-prompt diagnostic controls"}
    elif args.phase == "pilot":
        spec["spec"]["harness"]["request_count"] = 8
    elif args.phase == "profiles":
        spec["spec"]["harness"]["request_count"] = 1
        spec["spec"]["harness"]["args"] = {"timed_requests": 0, "reference_root": args.reference_root}
    elif args.phase in ("screen", "timing", "campaign"):
        per_model = {"screen": 16, "timing": 48, "campaign": 80}[args.phase]
        spec["spec"]["harness"]["request_count"] = per_model * selected_count
    if args.model_key:
        spec["spec"]["labels"]["selected_models"] = ",".join(sorted(set(args.model_key)))
    if args.duration_s is not None:
        if not 60 <= args.duration_s <= 10800:
            parser.error("duration must be 60..10800 seconds, within the remaining approved cumulative budget")
        spec["spec"]["budget"]["max_duration_s"] = args.duration_s
    if len(command) > 16384:
        raise ValueError("JY command exceeds its 16384 character limit")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
