"""Materialize the source-bound inference qualification matrix."""

import re
from pathlib import Path

from .ci_artifacts import read_json
from .inference_host import known

LADDER = Path(__file__).resolve().parents[2] / "ci/inference-ladder.json"
SELECTIONS = {"spark": ("spark",), "metal": ("metal",), "all": ("spark", "metal")}


def row_id(key, mode, platform):
    # Spark ids predate the platform dimension; receipts and comments match them.
    return f"{key}-{mode}" if platform == "spark" else f"{key}-{mode}-{platform}"


def template_key(profile, mode, platform):
    return ("inference-" + ("pr-" if profile == "pr" else "")
            + ("" if platform == "spark" else f"{platform}-") + mode)


def ladder_models():
    ladder = read_json(LADDER)
    if set(ladder) != {"schema_version", "models"} or type(ladder["schema_version"]) is not int:
        raise ValueError("invalid inference ladder schema")
    models = ladder["models"]
    if ladder["schema_version"] != 1 or not isinstance(models, list) or not models:
        raise ValueError("unsupported or empty inference ladder")
    ids, names = set(), set()
    for model in models:
        if set(model) != {"id", "model", "architecture", "pr", "split"}:
            raise ValueError("invalid inference model fields")
        key, name, architecture = model["id"], model["model"], model["architecture"]
        if (
            not isinstance(key, str) or not re.fullmatch(r"[a-z0-9-]+", key) or key in ids
            or not isinstance(name, str) or not name.startswith("Qwen/") or name in names
            or architecture not in {"hybrid-dense", "full-attention-moe", "hybrid-moe", "qwen4-preview"}
            or type(model["pr"]) is not bool or type(model["split"]) is not bool
        ):
            raise ValueError(f"invalid or duplicate inference model: {key}")
        ids.add(key)
        names.add(name)
    return models


def plan(profile, sha, platform="spark"):
    if profile not in {"pr", "nightly", "release"}:
        raise ValueError("unknown inference profile")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise ValueError("source must be an immutable 40-digit commit SHA")
    host = known(platform)
    checks = (["gpu-startup", "scored-quality", "streaming-smoke"] if profile == "pr"
              else ["correctness", "scored-quality", "agentx-performance"])
    rows = []
    for model in ladder_models():
        if profile == "pr" and not model["pr"]:
            continue
        for mode, machines in (("standalone", 1), ("split", 2)):
            if mode in host["modes"] and (mode == "standalone" or model["split"]):
                rows.append({"id": row_id(model["id"], mode, platform), "model_key": model["id"],
                             "model": model["model"],
                             "architecture": model["architecture"], "mode": mode,
                             "machines": machines, "checks": checks, "platform": platform})
    if not rows:
        raise ValueError("profile selects no inference models")
    return {
        "schema_version": 1, "status": "planned-not-executed", "profile": profile,
        "source_sha": sha, "platform": platform, "hardware": host["hardware"],
        "execution": "jy-native-job-or-atomic-formation-outside-actions-container",
        "performance_claim": ("smoke-only" if profile == "pr"
                              else "extended-qualification-requires-pinned-harness-and-repeats"),
        "rows": rows,
    }


def matrix(profile, sha, include_smoke=False, platform="spark"):
    if platform not in SELECTIONS:
        raise ValueError(f"unknown platform selection: {platform}")
    hosts = SELECTIONS[platform]
    rows = [{**row, "configuration": row["id"], "profile": profile}
            for host in hosts for row in plan(profile, sha, host)["rows"]]
    if include_smoke:
        if profile != "nightly":
            raise ValueError("the additional smoke matrix requires the nightly profile")
        rows.extend(
            {**row, "id": f'smoke-{row["id"]}', "configuration": row["id"], "profile": "pr"}
            for host in hosts for row in plan("pr", sha, host)["rows"]
        )
    return rows
