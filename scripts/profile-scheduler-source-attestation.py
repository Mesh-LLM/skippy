#!/usr/bin/env python3
"""Bind the PR source diff to the server and native patches tested on Metal."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_recipe_identity():
    root = Path("llama_cpp/patches")
    patches = [(path.name, path) for path in sorted(root.glob("*.patch"))]
    for subdir in ("model_support", "generated"):
        for name in (root / subdir / "series").read_text().splitlines():
            patches.append((f"{subdir}/{name}", root / subdir / name))
    hasher = hashlib.sha256()
    hasher.update(b"mesh-llm-skippy-patch-queue-v2\0")
    hasher.update(len(patches).to_bytes(8, "little"))
    for name, path in patches:
        for value in (name.encode(), path.read_bytes()):
            hasher.update(len(value).to_bytes(8, "little"))
            hasher.update(value)
    recipe_path = Path("crates/skippy-api/src/native-recipe.json")
    recipe = json.loads(recipe_path.read_text())
    if recipe != {
        "llama_upstream_sha": Path("llama_cpp/upstream.txt").read_text().strip(),
        "patch_queue_sha256": hasher.hexdigest(),
    }:
        raise ValueError("packaged native recipe differs from ordered patch queue")
    return recipe_path, recipe


def main():
    root = Path("target/scheduler-fixes")
    rust_patch = root / "combined-logprob-u1.patch"
    native_build_patch = root / "q4-scale-native.patch"
    native_pr_patch = Path(
        "llama_cpp/patches/0060-ggml-metal-preserve-q4-k-scales-before-nibble-adjustment.patch")
    combined = json.loads((root / "artifacts/combined-logprob-build/scheduler-fix-build.json").read_text())
    component = json.loads((root / "artifacts/q4-metal-components-v2/metal-components.json").read_text())
    subprocess.run(["git", "apply", "--reverse", "--check", str(rust_patch)], check=True)
    if digest(rust_patch) != combined["patch_sha256"]:
        raise ValueError("tested Rust patch differs from root diff")
    paths = re.findall(r"^diff --git a/([^ ]+) b/[^\n]+$", rust_patch.read_text(), re.M)
    if len(paths) != len(set(paths)) or not paths or any(
        not name.startswith("crates/") or ".." in Path(name).parts for name in paths
    ):
        raise ValueError("unexpected tested Rust patch file list")
    with tempfile.TemporaryDirectory(prefix="scheduler-source-attestation-") as scratch:
        scratch_root = Path(scratch)
        for name in paths:
            prior = subprocess.run(["git", "show", combined["source"] + ":" + name], capture_output=True)
            if prior.returncode == 0:
                path = scratch_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(prior.stdout)
        subprocess.run(["git", "apply", str(rust_patch.absolute())], cwd=scratch_root, check=True)
        if any((scratch_root / name).read_bytes() != Path(name).read_bytes() for name in paths):
            raise ValueError("current Rust file differs from tested patch output")
    changed = subprocess.check_output(["git", "diff", "origin/main", "--name-only", "--", "crates"], text=True).splitlines()
    added = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "crates"],
                                    text=True).splitlines()
    if set(changed + added) != set(paths) | {"crates/skippy-api/src/native-recipe.json"}:
        raise ValueError("Rust worktree has changes outside tested patch")
    if digest(native_build_patch) != digest(native_pr_patch):
        raise ValueError("PR native patch differs from tested Metal patch")
    if not component["complete"] or not any(
        v["composition"]["arm"] == "fixed" for v in component["variants"]
    ):
        raise ValueError("fixed native build missing")
    build_script = Path("scripts/build-llama.sh").read_text()
    if "test-skippy-q4-scale-metal" not in build_script:
        raise ValueError("native test target absent from source recipe")
    recipe_path, recipe = native_recipe_identity()
    record = {
        "source_commit": combined["source"],
        "tested_rust_patch_sha256": digest(rust_patch),
        "rust_files_exactly_match_tested_patch": True,
        "rust_files": paths,
        "tested_native_patch_sha256": digest(native_build_patch),
        "pr_native_patch_sha256": digest(native_pr_patch),
        "combined_server_binary_sha256": combined["binary_sha256"],
        "native_component_report_sha256": digest(root / "artifacts/q4-metal-components-v2/metal-components.json"),
        "native_build_script_sha256": digest(Path("scripts/build-llama.sh")),
        "packaged_native_recipe_sha256": digest(recipe_path),
        "packaged_patch_queue_sha256": recipe["patch_queue_sha256"],
        "ported_main_commit": subprocess.check_output(["git", "rev-parse", "origin/main"], text=True).strip(),
        "ported_llama_upstream_sha": recipe["llama_upstream_sha"],
        "ported_runtime_hardware_qualified": False,
        "scope": "PR Rust files match the tested patch and the native patch bytes are unchanged. The tested binary used the earlier source commit; the main upgrade needs a new hardware qualification. Correctness and performance verdicts remain in model audits.",
    }
    destination = Path("evals/kv-profiling/pr-evidence/source-attestation.json")
    destination.write_text(json.dumps(record, indent=2) + "\n")
    print(destination, digest(destination))


if __name__ == "__main__":
    main()
