#!/usr/bin/env python3
"""Seal the direct native probe; no native library or model replacements."""
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
    parser.add_argument("--batch-probe", action="store_true")
    parser.add_argument("--activation-probe", action="store_true")
    parser.add_argument("--projection-reference", action="store_true")
    parser.add_argument("--gate-reference", action="store_true")
    parser.add_argument("--scale-reference", action="store_true")
    parser.add_argument("--gate-metal", action="store_true")
    args = parser.parse_args()
    if sum((args.batch_probe, args.activation_probe, args.projection_reference, args.gate_reference, args.scale_reference, args.gate_metal)) > 1:
        parser.error('choose one probe')
    select = runpy.run_path("scripts/profile-scheduler-suite-spec.py")["selected_source"]
    source = "671c6ebbf7661b236ce44d352024c51741b08f9c"
    files = {p: Path(p).read_text() for p in (
        "scripts/profile-scheduler-native-probe.cpp", "scripts/profile-scheduler-native-probe.just",
        "scripts/profile-scheduler-native-probe.py")}
    runner = 'scripts/profile-scheduler-native-probe.py'
    pin_key = 'qwen36-35b-a3b'
    if args.batch_probe:
        runner = 'scripts/profile-scheduler-batch-probe.py'
        files = {p: Path(p).read_text() for p in (
            runner, 'scripts/profile-scheduler-batch-probe.cpp', 'scripts/profile-scheduler-batch-probe.just')}
        files['scripts/profile-scheduler-native-probe.py'] = select(
            'scripts/profile-scheduler-native-probe.py', {'digest', 'prepare'})
        pin_key = 'qwen35-4b'
    if args.activation_probe:
        runner = 'scripts/profile-scheduler-activation-probe.py'
        files = {p: Path(p).read_text() for p in (
            runner, 'scripts/profile-scheduler-activation-probe.cpp', 'scripts/profile-scheduler-activation-probe.just')}
        files['scripts/profile-scheduler-native-probe.py'] = select(
            'scripts/profile-scheduler-native-probe.py', {'digest', 'prepare'})
        pin_key = 'qwen35-4b'
    if args.projection_reference:
        runner = 'scripts/profile-scheduler-projection-reference.py'
        files = {p: Path(p).read_text() for p in (
            runner, 'scripts/profile-scheduler-projection-reference.cpp', 'scripts/profile-scheduler-projection-reference.just')}
        files['scripts/profile-scheduler-native-probe.py'] = select(
            'scripts/profile-scheduler-native-probe.py', {'digest', 'prepare'})
        files['projection-inputs.json'] = Path('target/scheduler-fixes/projection-inputs.json').read_text()
        pin_key = 'qwen35-4b'
    if args.gate_reference:
        runner = 'scripts/profile-scheduler-gate-reference.py'
        files = {p: Path(p).read_text() for p in (
            runner, 'scripts/profile-scheduler-gate-reference.cpp', 'scripts/profile-scheduler-gate-reference.just')}
        files['scripts/profile-scheduler-native-probe.py'] = select(
            'scripts/profile-scheduler-native-probe.py', {'digest', 'prepare'})
        files['gate-inputs.json'] = Path('target/scheduler-fixes/gate-inputs.json').read_text()
        pin_key = 'qwen35-4b'
    if args.scale_reference:
        runner = 'scripts/profile-scheduler-scale-reference.py'
        files = {p: Path(p).read_text() for p in (
            runner, 'scripts/profile-scheduler-scale-reference.cpp', 'scripts/profile-scheduler-scale-reference.just')}
        files['scripts/profile-scheduler-native-probe.py'] = select(
            'scripts/profile-scheduler-native-probe.py', {'digest', 'prepare'})
        files['scale-inputs.json'] = Path('target/scheduler-fixes/scale-inputs.json').read_text()
        pin_key = 'qwen35-4b'
    if args.gate_metal:
        runner = 'scripts/profile-scheduler-gate-metal.py'
        files = {p: Path(p).read_text() for p in (
            runner, 'scripts/profile-scheduler-gate-metal.cpp', 'scripts/profile-scheduler-gate-metal.just')}
        files['scripts/profile-scheduler-native-probe.py'] = select(
            'scripts/profile-scheduler-native-probe.py', {'digest', 'prepare'})
        files['gate-metal-inputs.json'] = Path('target/scheduler-fixes/gate-metal-inputs.json').read_text()
        pin_key = 'qwen35-4b'
    files["native-headers.json"] = Path("target/scheduler-fixes/native-headers/manifest.json").read_text()
    files["ci/inference-artifacts.json"] = json.dumps({pin_key:
        json.loads(Path("ci/inference-artifacts.json").read_text())[pin_key]})
    files["scripts/lib/profile_scheduler_suite.py"] = select(
        "scripts/lib/profile_scheduler_suite.py", {"nominal", "resolved"})
    files["scripts/lib/profile_kv_host.py"] = select("scripts/lib/profile_kv_host.py", {
        "HostUnavailable", "thermal_pressure", "save", "clean_env", "command", "telemetry", "competing_work"})
    for name, data in files.items():
        if name.endswith(".py"):
            compile(data, name, "exec")
    manifest = {"source": source, "scripts": {
        name: hashlib.sha256(data.encode()).hexdigest() for name, data in files.items()}}
    packed = base64.b85encode(lzma.compress(json.dumps(files).encode())).decode()
    command = ("set -eu\npython3 - <<'INSTALL'\nimport base64,json,lzma\nfrom pathlib import Path\n"
        "for name,data in json.loads(lzma.decompress(base64.b85decode(" + repr(packed) + "))).items():\n"
        " p=Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)\nINSTALL\n"
        'env -i PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" '
        'python3 ' + runner + ' --cache "$HOME/.cache/skippy/kv-profile/'
        + source + '" --output "$JIANYANG_ARTIFACT_DIR"\n')
    if len(command) > 16384:
        raise ValueError(f"command too large: {len(command)}")
    replace = runpy.run_path("scripts/profile-kv-spec.py")["replace"]
    spec = replace(json.loads(Path("evals/kv-profiling/jy/measure.json.in").read_text()), {
        "@SOURCE_SHA@": source, "@MODEL_MANIFEST_SHA256@": hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "@COMMAND@": command, "@PHASE@": "qwen36-native-logits-probe", "@DURATION@": 3600})
    spec["spec"]["labels"]["campaign"] = "scheduler-fixes"
    spec["spec"]["harness"].update(warmup_requests=0, request_count=8, concurrency=[1])
    if args.batch_probe:
        spec['spec']['name'] = 'Qwen3.5 unchanged native decode batch/residency probe'
        spec['spec']['labels']['phase'] = 'qwen35-native-batch-probe'
        spec['spec']['harness']['concurrency'] = [1, 16]
    if args.activation_probe:
        spec['spec']['name'] = 'Qwen3.5 unchanged library activation localization control'
        spec['spec']['labels']['phase'] = 'qwen35-activation-probe'
        spec['spec']['harness']['concurrency'] = [1, 16]
    if args.projection_reference:
        spec['spec']['name'] = 'Qwen3.5 private captured projection arithmetic reference'
        spec['spec']['labels']['phase'] = 'qwen35-projection-reference'
        spec['spec']['harness']['request_count'] = 12
        spec['spec']['budget']['max_duration_s'] = 900
    if args.gate_reference:
        spec['spec']['name'] = 'Qwen3.5 private gate projection arithmetic controls'
        spec['spec']['labels']['phase'] = 'qwen35-gate-reference'
        spec['spec']['harness']['request_count'] = 12
        spec['spec']['budget']['max_duration_s'] = 900
    if args.scale_reference:
        spec['spec']['name'] = 'Qwen3.5 private Q4_K scale division controls'
        spec['spec']['labels']['phase'] = 'qwen35-scale-reference'
        spec['spec']['harness']['request_count'] = 12
        spec['spec']['budget']['max_duration_s'] = 900
    if args.gate_metal:
        spec['spec']['name'] = 'Qwen3.5 isolated original Metal gate projection replay'
        spec['spec']['labels']['phase'] = 'qwen35-gate-metal'
        spec['spec']['harness']['request_count'] = 12
        spec['spec']['budget']['max_duration_s'] = 900
    args.out.write_text(json.dumps(spec, indent=2) + "\n")
    args.out.with_suffix(".inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(args.out, "command bytes", len(command))


if __name__ == "__main__":
    main()
