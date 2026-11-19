#!/usr/bin/env python3
"""Run one fresh supported-family qualification pass."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from lib.ci_artifacts import digest, source
from lib.ci_process import cancel_on_signal
from lib.upgrade_build import build
from lib.upgrade_evidence import MANIFEST, cached, family, load, recipe_digest, write_receipt
from lib.upgrade_lanes import causal, workload
from lib.upgrade_source import PIN


def cache_root():
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).resolve(strict=True)
    if os.environ.get("HF_CACHE"):
        return (Path(os.environ["HF_CACHE"]) / "hub").resolve(strict=True)
    raise ValueError("qualification requires a prewarmed immutable HF_HUB_CACHE or HF_CACHE")


def certify(backend, pass_name):
    root = Path.cwd().resolve()
    candidate = source()
    models, cache = load(root), cache_root()
    recipe = recipe_digest(root)
    manifest = digest(root / MANIFEST)
    upstream = (root / PIN).read_text().strip()
    out = root / "target/llama-upgrade/evidence"
    out.mkdir(parents=True)
    admitted = []
    for model in models:
        files = {}
        target = cached(model["artifact"], cache, files)
        projector = cached(model["mmproj_artifact"], cache, files) if model.get("mmproj_artifact") else None
        if model.get("draft_artifact"):
            cached(model["draft_artifact"], cache, files)
        admitted.append((model, target, projector, files))

    binaries = build(root, backend, out)
    cpu = binaries if backend == "cpu" else build(root, "cpu", out)
    families = []
    for model, target, projector, files in admitted:
        directory = out / model["family"]
        directory.mkdir()
        if model["class"] == "causal_generation":
            causal(model, target, projector, binaries, directory)
        else:
            workload(model, target, projector, cpu, directory)
        families.append(family(model, files))
    if recipe != recipe_digest(root) or candidate != source() or digest(root / MANIFEST) != manifest:
        raise ValueError("qualification source changed during execution")
    write_receipt(out, {
        "schema_version": 1, "candidate_sha": candidate, "upstream_sha": upstream,
        "manifest_sha256": manifest, "native_recipe_sha256": recipe, "backend": backend,
        "pass": pass_name, "result": "pass", "families": families,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=("cpu", "metal", "cuda"))
    parser.add_argument("--suite", required=True, choices=("supported",))
    parser.add_argument("--pass", dest="pass_name", required=True, choices=("candidate", "independent"))
    args = parser.parse_args()
    try:
        with cancel_on_signal():
            certify(args.backend, args.pass_name)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
