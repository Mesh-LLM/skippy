"""Build each upgrade qualification lane independently and record its tools."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ci_artifacts import digest, write_json
from .ci_process import command
from .upgrade_source import PIN, prepared_head


def logged(args, environment, path, timeout):
    report = {}
    try:
        with path.with_suffix(".stdout.log").open("wb") as stdout, path.with_suffix(".stderr.log").open("wb") as stderr:
            command(args, timeout=timeout, overrides=environment, stdout=stdout, stderr=stderr)
        report.update(success=True, outcome="Exited", cleanup_complete=True, exit_code=0)
    except (OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report.update(success=False, outcome=type(error).__name__, cleanup_complete=False,
                      exit_code=getattr(error, "returncode", None))
        raise
    finally:
        write_json(path.with_suffix(".result.json"), report)


@dataclass(frozen=True)
class Binaries:
    root: Path
    native: Path
    bin: Path
    test: Path
    backend: str

    def environment(self):
        return {"LLAMA_STAGE_BUILD_DIR": str(self.native), "LLAMA_STAGE_BACKEND": self.backend,
                "LLAMA_STAGE_LINK_MODE": "static", "HF_HUB_OFFLINE": "1"}


def build(root, backend, output):
    native = root / "target/llama-upgrade/native" / backend
    cargo = root / "target/llama-upgrade/cargo" / backend
    if native.exists() or cargo.exists():
        raise ValueError("qualification native and Cargo directories must be absent")
    environment = {
        "LLAMA_WORKDIR": str(root / ".deps/llama.cpp"), "LLAMA_PIN_FILE": str(root / PIN),
        "LLAMA_PATCH_DIR": str(root / "llama_cpp/patches"), "LLAMA_STAGE_BACKEND": backend,
        "LLAMA_STAGE_LINK_MODE": "static", "LLAMA_STAGE_BUILD_DIR": str(native),
        "LLAMA_BUILD_DIR": str(native), "LLAMA_STAGE_BUILD_TESTS": "OFF",
        "LLAMA_STAGE_FULL_REPLAY": "OFF", "LLAMA_STAGE_WORKLOAD_ORACLE": "ON" if backend == "cpu" else "OFF",
        "CARGO_TARGET_DIR": str(cargo),
    }
    logged(["just", "llama-build"], environment, output / f"native-build-{backend}", 7200)
    prepared_head(root)
    # Normal CI owns the complete graph/patch suite before these real-weight jobs.
    logged(["just", "with-lld", "cargo", "build", "--locked", "-p", "skippy-cli", "-p",
            "skippy-correctness", "-p", "skippy-package-builder", "-p", "skippy-topology", "--bins"],
           environment, output / f"rust-build-{backend}", 7200)
    messages = command(["just", "with-lld", "cargo", "test", "--locked", "-p", "skippy-serving",
                        "--lib", "--no-run", "--message-format=json"],
                       overrides=environment, timeout=7200, capture_stdout=True)
    tests = set()
    for line in messages.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if (value.get("reason") == "compiler-artifact" and value["profile"]["test"] is True
                and value["target"]["name"] == "skippy_serving" and value.get("executable")):
            tests.add(Path(value["executable"]))
    if len(tests) != 1:
        raise ValueError("expected exactly one source-built serving test executable")
    test = tests.pop()
    binaries = cargo / "debug"
    identities = {name: digest(binaries / name) for name in
                  ("skippy", "skippy-correctness", "skippy-package-builder", "skippy-topology-plan")}
    identities["skippy-serving-tests"] = digest(test)
    if backend == "cpu":
        identities.update({name: digest(native / "bin" / name) for name in
                           ("llama-server", "llama-completion", "llama-tts")})
    write_json(output / f"executables-{backend}.json", dict(sorted(identities.items())))
    return Binaries(root, native, binaries, test, backend)
