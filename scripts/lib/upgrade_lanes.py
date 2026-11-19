"""Execute causal and independent workload qualification against admitted fixtures."""

import json
import re

from .ci_artifacts import write_json
from .ci_process import command
from .upgrade_build import logged
from .upgrade_evidence import lanes


def topology_splits(value, layers):
    boundaries = value["boundaries"]
    two, three = value["two_stage_splits"], value["three_stage_splits"]
    declared = {row["layer"] for row in boundaries}
    if (declared != set(range(1, layers)) or len(boundaries) != len(declared)
            or len(two) != 1 or len(three) != 2 or three[0] >= three[1]
            or any(row["decision"] not in {"accepted", "rejected"} for row in boundaries)):
        raise ValueError("invalid or incomplete topology boundary classification")
    accepted = {row["layer"] for row in boundaries if row["decision"] == "accepted"}
    if any(cut not in accepted for cut in [*two, *three]):
        raise ValueError("topology selected an unapproved cut")
    return two[0], ",".join(map(str, three))


def offloaded(directory):
    for path in directory.rglob("*.log"):
        with path.open(encoding="utf-8", errors="replace") as stream:
            if any(re.search(r"offloaded [1-9][0-9]*/[^\n]*GPU", line) for line in stream):
                return True
    return False


def multimodal(model, target, projector, bins, out, split):
    env = dict(bins.environment(), SKIPPY_MM_MODEL=str(target), SKIPPY_MM_PROJECTOR=str(projector),
               SKIPPY_MM_IMAGE=str(bins.root / "ci/llama-canary/fixtures/multimodal-smoke.png"),
               SKIPPY_MM_ACTIVATION_WIDTH=str(model["execution"]["activation_width"]),
               SKIPPY_MM_SPLIT_LAYER=str(split), SKIPPY_MM_CTX_SIZE="2048", SKIPPY_MM_MAX_TOKENS="256",
               SKIPPY_MM_N_GPU_LAYERS="0" if bins.backend == "cpu" else "999")
    for name in (
        "real_multimodal_local_smoke_when_fixture_is_set",
        "real_multimodal_local_prefill_failure_releases_lane_when_fixture_is_set",
        "real_multimodal_length_limit_then_next_image_uses_clean_lane_when_fixture_is_set",
        "real_multimodal_split_smoke_when_fixture_is_set",
    ):
        path = out / name
        logged([str(bins.test), f"frontend::tests::multimodal::{name}", "--exact",
                "--nocapture", "--test-threads=1"], env, path, 7200)
        if ("1 passed" not in path.with_suffix(".stdout.log").read_text()
                or "skipping" in path.with_suffix(".stderr.log").read_text()):
            raise ValueError("multimodal qualification test did not execute")


def coordinate(model):
    artifact = model["artifact"]
    return f'{artifact["repo"]}:{artifact["selector"]}'


def causal(model, target, projector, bins, out):
    execution = model["execution"]
    layers = execution["trunk_layers"] + execution["mtp_layers"]
    if layers >= 2**32:
        raise ValueError("layer count overflow")
    width = str(execution["activation_width"])
    model_id = coordinate(model)
    env = bins.environment()
    topology = command([str(bins.bin / "skippy-topology-plan"), model_id, str(layers), width],
                       overrides=env, capture_stdout=True)
    (out / "topology.json").write_bytes(topology.encode("utf-8"))
    split, splits = topology_splits(json.loads(topology), layers)
    args = ["bash", "scripts/family-certify.sh", "--family", model["family"], "--target-model",
            str(target), "--model-id", model_id, "--layer-end", str(layers), "--split-layer",
            str(split), "--splits", splits, "--activation-width", width, "--n-gpu-layers",
            "0" if bins.backend == "cpu" else "999", "--startup-timeout-secs", "900", "--skip-build",
            "--skip-speculative", "--require-lanes", "--cert-root", str(out), "--run-id", "parity"]
    if execution["mtp_layers"]:
        args.append("--require-native-mtp-draft")
    logged(args, dict(env, FAMILY_BATTERY_BIN_DIR=str(bins.bin)), out / "parity", 7200)
    if bins.backend != "cpu" and not offloaded(out):
        raise ValueError(f'{model["family"]} produced no evidence of actual GPU layer offload')
    if projector is not None:
        multimodal(model, target, projector, bins, out, split)
    write_json(out / "lanes.json", lanes(model))


def workload(model, target, projector, bins, out):
    if bins.backend != "cpu":
        raise ValueError("independent workload oracle requires the CPU source build")
    kind = model["class"]
    args = ["bash", "scripts/skippy-workload-certify.sh", "--class", kind,
            "--lane", kind.replace("_", "-") + "-smoke", "--model-path", str(target),
            "--model-id", coordinate(model), "--work-dir", str(out), "--require-oracle",
            "--startup-timeout-secs", "900"]
    if projector is not None:
        args += ["--projector-path", str(projector)]
    option, binary = {
        "encoder_decoder": ("--oracle-completion", "llama-completion"),
        "speech_synthesis": ("--oracle-tts", "llama-tts"),
    }.get(kind, ("--oracle-server", "llama-server"))
    args += [option, str(bins.native / "bin" / binary)]
    env = dict(bins.environment(), SKIPPY_WORKLOAD_NATIVE_BUILD_DIR=str(bins.native),
               SKIPPY_WORKLOAD_CANDIDATE_BIN_DIR=str(bins.bin), SKIPPY_UPGRADE_SERVING_TEST_BIN=str(bins.test),
               SKIPPY_WORKLOAD_LAYER_END=str(model["execution"]["trunk_layers"]), SKIPPY_WORKLOAD_N_GPU_LAYERS="0")
    logged(args, env, out / "workload", 7200)
    log = (out / "workload.stdout.log").read_text(encoding="utf-8")
    if "1 passed" not in log or "local-monolithic oracle passed" not in log:
        raise ValueError("workload qualification did not execute fixture smoke and independent oracle")
