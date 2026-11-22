#!/usr/bin/env python3
"""Run one fresh supported-family qualification pass."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from lib.ci_artifacts import digest, source, read_json, write_json
from lib.ci_process import cancel_on_signal
from lib.upgrade_build import build
from lib.upgrade_evidence import MANIFEST, cached, family, load, recipe_digest, write_receipt
from lib.upgrade_lanes import causal, workload
from lib.upgrade_standalone import standalone
from lib.upgrade_suite import selected_models, SELECTION
from lib.upgrade_source import PIN


def cache_root():
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).resolve(strict=True)
    if os.environ.get("HF_CACHE"):
        return (Path(os.environ["HF_CACHE"]) / "hub").resolve(strict=True)
    raise ValueError("qualification requires a prewarmed immutable HF_HUB_CACHE or HF_CACHE")


def certify(backend, pass_name, suite="routine"):
    if backend not in {"cpu", "cuda"}:
        raise ValueError("upgrade family qualification requires CPU or CUDA")
    root = Path.cwd().resolve()
    candidate = source()
    registry, cache = load(root), cache_root()
    split_models = selected_models(root, registry, backend, suite)
    selected = {model["family"]: model for model in split_models}
    models = ([selected.get(model["family"], model) for model in registry]
              if backend == "cpu" else split_models)
    selection_hash = digest(root / SELECTION)
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

    selection = read_json(root / SELECTION)
    write_json(out / "compatibility-inventory.json", selection)
    if not selection.get("upstream_architectures") or selection.get("new_uncovered_architectures"):
        raise ValueError("candidate upstream inventory is missing or new architectures lack real fixtures")
    binaries = build(root, backend, out)
    cpu = binaries if backend == "cpu" else None
    families = []
    for model, target, projector, files in admitted:
        directory = out / model["family"]
        directory.mkdir()
        if model["class"] == "causal_generation":
            if backend == "cpu":
                standalone(model, target, binaries, directory / "standalone")
            if model["family"] in selected:
                causal(model, target, projector, binaries, directory)
        else:
            workload(model, target, projector, cpu, directory)
        lanes = (["standalone-oracle"] if backend == "cpu" and model["class"] == "causal_generation" else [])
        if model["family"] in selected:
            lanes += family(model)["lanes"]
        families.append(dict(family(model, files), lanes=lanes))
    if recipe != recipe_digest(root) or candidate != source() or digest(root / MANIFEST) != manifest:
        raise ValueError("qualification source changed during execution")
    write_receipt(out, {
        "schema_version": 1, "candidate_sha": candidate, "upstream_sha": upstream,
        "manifest_sha256": manifest, "selection_sha256": selection_hash, "suite": suite, "native_recipe_sha256": recipe, "backend": backend,
        "pass": pass_name, "result": "pass", "families": families,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=("cpu", "cuda"))
    parser.add_argument("--suite", choices=("routine", "full", "supported"), default="routine")
    parser.add_argument("--pass", dest="pass_name", choices=("candidate",), default="candidate")
    args = parser.parse_args()
    try:
        with cancel_on_signal():
            certify(args.backend, args.pass_name, "full" if args.suite == "supported" else args.suite)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
