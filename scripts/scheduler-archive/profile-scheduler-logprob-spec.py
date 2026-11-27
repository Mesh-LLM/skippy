#!/usr/bin/env python3
"""Seal the registered before/after admission replay into a bounded JY job."""

import argparse
import base64
import hashlib
import json
import lzma
from pathlib import Path
import runpy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--build", type=Path, help="Explicit verified candidate build identity; defaults to the isolated fix")
    args = parser.parse_args()
    runner = runpy.run_path("scripts/profile-scheduler-logprob-retest.py")
    select = runpy.run_path("scripts/profile-scheduler-suite-spec.py")["selected_source"]
    files = {p: Path(p).read_text() for p in (
        "scripts/profile-scheduler-logprob-retest.py", "scripts/lib/profile_scheduler_suite.py",
        "ci/inference-artifacts.json")}
    if args.build:
        built = json.loads(args.build.read_text())
        if built['source'] != runner['suite'].SOURCE:
            raise ValueError('candidate source drift')
        for field in ('patch_sha256', 'binary_sha256'):
            if len(built[field]) != 64 or any(c not in '0123456789abcdef' for c in built[field]):
                raise ValueError('invalid candidate digest')
        path = 'scripts/profile-scheduler-logprob-retest.py'
        for constant, field in (('FIX', 'patch_sha256'), ('BINARY_SHA', 'binary_sha256')):
            old = constant + ' = "' + runner[constant] + '"'
            if files[path].count(old) != 1:
                raise ValueError('candidate identity declaration changed')
            files[path] = files[path].replace(old, constant + ' = "' + built[field] + '"')
            runner[constant] = built[field]
    files["scripts/profile-scheduler-path-isolation.py"] = select(
        "scripts/profile-scheduler-path-isolation.py", {"guarded"})
    files["scripts/lib/profile_kv_host.py"] = select("scripts/lib/profile_kv_host.py", {
        "HostUnavailable", "thermal_pressure", "save", "clean_env", "command", "post",
        "telemetry", "competing_work", "require_idle", "stop", "launch_args", "server", "body"})
    files["scripts/profile-kv-scheduler.py"] = select("scripts/profile-kv-scheduler.py", {
        "digest", "tokenize", "fixtures", "request", "wave"})
    files["scripts/profile-kv-scheduler-qualification.py"] = select(
        "scripts/profile-kv-scheduler-qualification.py", {"file_digest", "qualified_arms"})
    for name, source in files.items():
        if name.endswith(".py"):
            compile(source, name, "exec")
    manifest = {"source": runner["suite"].SOURCE, "patch": runner["FIX"], "binary_sha256": runner['BINARY_SHA'],
                "scripts": {p: hashlib.sha256(s.encode()).hexdigest() for p, s in files.items()}}
    packed = base64.b85encode(lzma.compress(json.dumps(files).encode())).decode()
    command = ("set -eu\npython3 - <<'INSTALL'\nimport base64,json,lzma\nfrom pathlib import Path\n"
               "for name,data in json.loads(lzma.decompress(base64.b85decode(" + repr(packed) + "))).items():\n"
               " p=Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)\nINSTALL\n")
    command += ('env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
                'python3 scripts/profile-scheduler-logprob-retest.py --cache "$HOME/.cache/skippy/kv-profile/'
                + runner["suite"].SOURCE + '" --output "$JIANYANG_ARTIFACT_DIR"\n')
    if len(command) > 16384:
        raise ValueError(f"command too large: {len(command)}")
    replace = runpy.run_path("scripts/profile-kv-spec.py")["replace"]
    spec = replace(json.loads(Path("evals/kv-profiling/jy/measure.json.in").read_text()), {
        "@SOURCE_SHA@": manifest["source"], "@MODEL_MANIFEST_SHA256@": hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "@COMMAND@": command, "@PHASE@": "logprob-prefill-retest", "@DURATION@": 5400})
    job = spec["spec"]
    job["name"] = "Qwen3.6 logprob prefill alignment baseline/fix replay"
    job["labels"].update(campaign="scheduler-fixes", patch=runner["FIX"])
    job["harness"].update(warmup_requests=0, request_count=48, concurrency=[1])
    args.out.write_text(json.dumps(spec, indent=2) + "\n")
    args.out.with_suffix(".inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(args.out, "command bytes", len(command))


if __name__ == "__main__":
    main()
