#!/usr/bin/env python3
"""Build and cache the exact-source inference executables on the JY host."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from lib.ci_artifacts import digest, read_json, write_json
from lib.ci_process import cancel_on_signal, capture, command
from lib.inference_inputs import BINARIES, BUILD_IDENTITY, BINARY_DIR, check_executables, identity, lock

HARNESS_ROOT = Path(__file__).resolve().parents[1]


def build_policy():
    files = ("Justfile", "scripts/inference-build.py", "scripts/lib/inference_inputs.py")
    hashes = {name: digest(HARNESS_ROOT / name) for name in files}
    release = tomllib.loads(Path("Cargo.toml").read_text()).get("profile", {}).get("release", {})
    overrides = {k: v for k, v in os.environ.items() if k.startswith("CARGO_PROFILE_RELEASE_")}
    if str(overrides.get("CARGO_PROFILE_RELEASE_OPT_LEVEL", release.get("opt-level", 3))) != "3":
        raise ValueError("inference qualification requires release opt-level 3")
    return {"cargo_profile": "release", "rust_features": [], "native_link": "static",
            "rust_release": release, "release_overrides": overrides,
            "harness_commit": capture(["git", "-C", str(HARNESS_ROOT), "rev-parse", "HEAD"]).strip(),
            "harness_sha256": cache_key(hashes)}


def cache_key(inputs):
    data = json.dumps(inputs, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def native_inputs():
    # Bind Cargo's bundled archives and compiler outputs to the same source recipe.
    # Git tree entries include paths and blob identities, including removed patches.
    paths = ["llama_cpp", "Justfile", "crates/skippy-ffi", "scripts/build-llama.sh",
             "scripts/prepare-llama.sh", "scripts/prepare-whisper.sh", "scripts/lib/cuda-toolkit.sh",
             "scripts/inference-build.py"]
    return hashlib.sha256(capture(["git", "ls-tree", "-r", "HEAD", "--", *paths]).encode()).hexdigest()


def dependency_key(inputs):
    dependencies = {name: value for name, value in inputs.items() if name != "source"}
    if "policy" in dependencies:
        dependencies["policy"] = {k: v for k, v in dependencies["policy"].items() if k != "harness_commit"}
    return cache_key(dependencies)


def source_inputs(sha):
    environment = {
        name: value for name, value in os.environ.items()
        if name.startswith(("LLAMA_", "SKIPPY_", "CARGO_PROFILE_")) or name in {
            "CC", "CXX", "CUDACXX", "CFLAGS", "CXXFLAGS", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS"
        }
    }
    return {
        "schema": 3, "native_inputs_sha256": native_inputs(), "source": sha, "policy": build_policy(),
        "os": platform.system().lower(), "arch": platform.machine(),
        "compiler_extra_files": {path: digest(Path(path)) for path in
                                 filter(None, os.environ.get("SCCACHE_EXTRAFILES", "").split(os.pathsep))},
        "cargo_lock_sha256": digest(Path("Cargo.lock")), "rust": capture(["rustc", "-vV"]),
        "cuda": capture(["nvcc", "--version"]), "cxx": capture(["c++", "--version"]),
        "cmake": capture(["cmake", "--version"]), "backend": "cuda", "architectures": "121",
        "link": "static", "profile": "release", "environment": environment,
    }


def install_binaries(directory):
    BINARY_DIR.mkdir(parents=True, exist_ok=True)
    for name in BINARIES:
        shutil.copy(directory / name, BINARY_DIR / name)


def build(sha, root, inputs, key, cached):
    dependencies = dependency_key(inputs)
    cargo_root = root.parent / "cargo-v3"
    cargo_root.mkdir(parents=True, exist_ok=True)
    with lock(cargo_root / f"{dependencies}.lock"):
        cargo_target = cargo_root / dependencies
        native_identity = Path("target/inference-native-inputs.json").resolve()
        native_identity.parent.mkdir(parents=True, exist_ok=True)
        write_json(native_identity, {"schema": 2, "native_inputs_sha256": inputs["native_inputs_sha256"]})
        extra_files = [*filter(None, os.environ.get("SCCACHE_EXTRAFILES", "").split(os.pathsep)),
                       str(native_identity)]
        environment = {
            "CARGO_TARGET_DIR": str(cargo_target), "SCCACHE_DIR": str(root.parent / "compiler"),
            "SCCACHE_BASEDIRS": str(Path.cwd()), "CARGO_INCREMENTAL": "0", "SCCACHE_SERVER_PORT": "4227",
            "SCCACHE_CACHE_SIZE": "20G",
            "SCCACHE_EXTRAFILES": os.pathsep.join(extra_files),
        }
        sccache = shutil.which("sccache")
        if sccache:
            environment["RUSTC_WRAPPER"] = "sccache"
            try:
                command([sccache, "--stop-server"], overrides=environment, timeout=30)
            except subprocess.CalledProcessError:
                pass
            command([sccache, "--start-server"], overrides=environment, timeout=30)
        command(["just", "--justfile", str(HARNESS_ROOT / "Justfile"),
                 "--working-directory", str(Path.cwd()), "inference-build-uncached"],
                overrides=environment, timeout=7200)
        install_binaries(cargo_target / "release")
        build_id = identity(sha)
        build_id.update(cache_key=key, dependency_cache_key=dependencies, build_inputs=inputs, cache_hit=False)
        write_json(BUILD_IDENTITY, build_id)
        with tempfile.TemporaryDirectory(dir=root, prefix="publish-") as tmp:
            stage = Path(tmp) / "entry"
            stage.mkdir()
            for name in BINARIES:
                shutil.copy(BINARY_DIR / name, stage / name)
            write_json(stage / "identity.json", build_id)
            stage.rename(cached)
        if sccache:
            command([sccache, "--show-stats"], overrides=environment, timeout=30)


def main():
    sha = os.environ["JIANYANG_COMMIT"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or capture(["git", "rev-parse", "HEAD"]).strip() != sha:
        raise ValueError("build source differs from admitted JY commit")
    command(["git", "diff", "--exit-code", "HEAD"], timeout=30)
    inputs = source_inputs(sha)
    key = cache_key(inputs)
    command(["git", "-C", str(HARNESS_ROOT), "diff", "--exit-code", "HEAD"], timeout=30)
    root = Path(os.environ["HOME"]) / ".cache/skippy/inference-build-v3"
    root.mkdir(parents=True, exist_ok=True)
    cached = root / key
    with lock(root / f"{key}.lock"):
        if cached.exists():
            try:
                build_id = read_json(cached / "identity.json")
                if build_id.get("cache_key") != key:
                    raise ValueError("cache source or toolchain key mismatch")
                check_executables(cached, build_id, sha)
            except (OSError, ValueError, KeyError) as error:
                print(f"Rejecting invalid inference cache entry: {error}", file=sys.stderr)
                shutil.rmtree(cached)
            else:
                install_binaries(cached)
                write_json(BUILD_IDENTITY, dict(build_id, cache_hit=True))
                print(f"Inference executable cache HIT: {key}")
                return
        print(f"Inference executable cache MISS: {key}", flush=True)
        build(sha, root, inputs, key, cached)


if __name__ == "__main__":
    try:
        with cancel_on_signal():
            main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        sys.exit(str(error))
