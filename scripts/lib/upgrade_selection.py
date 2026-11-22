"""Bind touched builders and compatibility inventory to the prepared upstream source."""

import json
import hashlib

from .ci_artifacts import write_json
from .ci_process import command
from .upgrade_source import PIN, git
from .upgrade_suite import SELECTION, parse_factory


def prepare_selection(checkout, base_pin):
    source = checkout / ".deps/llama.cpp"
    new_pin = (checkout / PIN).read_text().strip()
    if base_pin != new_pin:
        command(["git", "-C", str(source), "fetch", "--no-tags", "--depth=1", "origin", base_pin], timeout=600)
    paths = git(source, "diff", "--name-only", base_pin, new_pin, "--", "src/models").splitlines()
    families = json.loads((checkout / "ci/llama-canary/generated-family-map.json").read_text())["families"]
    manifest = json.loads((checkout / "ci/llama-canary/model-fixtures.json").read_text())["models"]
    known = {model["family"] for model in manifest}
    additional = sorted(name for name, owners in families.items() if name in known and set(owners) & set(paths))
    def architectures(pin):
        return parse_factory(git(source, "show", f"{pin}:src/llama-arch.cpp"),
                             git(source, "show", f"{pin}:src/llama-model.cpp"))
    inventory = architectures(new_pin)
    previous = architectures(base_pin)
    covered = {model["architecture"] for model in manifest}
    write_json(checkout / SELECTION, {
        "schema_version": 1, "base_upstream_sha": base_pin, "upstream_sha": new_pin,
        "changed_model_sources": sorted(paths), "additional_families": additional,
        "upstream_architectures": inventory,
        "new_uncovered_architectures": sorted(set(inventory) - set(previous) - covered),
        "architectures_without_real_fixture": sorted(set(inventory) - covered),
    })


def write_native_recipe(checkout):
    """Retain source identity for packaged embedding APIs without an architecture allowlist."""
    directory = checkout / "llama_cpp/patches"
    patches = [(path.name, path) for path in sorted(directory.glob("*.patch"))]
    for subdir in ("model_support", "generated"):
        folder = directory / subdir
        for name in (folder / "series").read_text().splitlines():
            if not name or "/" in name or "\\" in name:
                raise ValueError("invalid native patch series member")
            patches.append((f"{subdir}/{name}", folder / name))
    digest = hashlib.sha256()
    digest.update(b"mesh-llm-skippy-patch-queue-v2\0")
    digest.update(len(patches).to_bytes(8, "little"))
    for name, path in patches:
        for value in (name.encode("utf-8"), path.read_bytes()):
            digest.update(len(value).to_bytes(8, "little"))
            digest.update(value)
    write_json(checkout / "crates/skippy-api/src/native-recipe.json", {
        "llama_upstream_sha": (checkout / PIN).read_text().strip(),
        "patch_queue_sha256": digest.hexdigest(),
    })
