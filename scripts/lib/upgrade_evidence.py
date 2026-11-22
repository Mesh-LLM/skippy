"""Validate the supported family roster and bind qualification to exact inputs."""

import hashlib
import re

from .ci_artifacts import archive_path, digest, read_json, write_json
from .upgrade_source import validate_sha

MANIFEST = "ci/llama-canary/model-fixtures.json"
RECIPE = ("llama_cpp", "scripts/build-llama.sh", "scripts/prepare-llama.sh", "skippy_api",
          "crates/skippy-ffi", "ci/llama-canary", "ci/inference-artifacts.json",
          "ci/inference-ladder.json", "crates/skippy-api/src/native-recipe.json")
ARTIFACTS = ("artifact", "mmproj_artifact", "draft_artifact")
WORKLOADS = {"embedding", "rerank", "encoder_decoder", "ocr", "speech_synthesis", "speech_recognition"}


def inventory(root, paths):
    output = []

    def collect(path):
        if path.is_symlink():
            raise ValueError(f"symlink is not admitted: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            relative.encode("utf-8")
            output.append(relative)
        elif path.is_dir():
            for item in path.iterdir():
                collect(item)
        else:
            raise ValueError(f"special or missing file: {path}")

    for path in paths:
        collect(path)
    return sorted(output)


def recipe_digest(root):
    state = hashlib.sha256()
    for relative in inventory(root, [root / path for path in RECIPE]):
        state.update(relative.encode("utf-8") + b"\0")
        with (root / relative).open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                state.update(block)
        state.update(b"\0")
    return state.hexdigest()


def lanes(model):
    kind, profile = model["class"], model["profile"]
    if kind == "causal_generation" and profile == "full":
        output = ["single-step", "chain", "state-handoff"]
        if model["execution"]["mtp_layers"] > 0:
            output.append("native-mtp-heads")
        if model.get("mmproj_artifact") is not None:
            output.append("multimodal-smoke")
        return output
    if kind in WORKLOADS and profile == "workload-oracle":
        return [kind + "-smoke", kind + "-oracle"]
    raise ValueError(f"unsupported qualification contract: {kind} {profile}")


def artifact_files(artifact):
    revision = validate_sha(artifact["revision"])
    repo = artifact["repo"]
    parts = repo.split("/")
    if len(parts) != 2 or any(not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9._-]*", part) for part in parts):
        raise ValueError("invalid immutable model repository")
    files, identities = artifact["files"], artifact["file_integrity"]
    if not artifact["selector"] or not files or len(files) != len(identities) or set(files) != set(identities):
        raise ValueError("invalid immutable model coordinate")
    for filename in files:
        path = archive_path(filename)
        integrity = identities[filename]
        if (not path.parts or not re.fullmatch(r"[0-9a-fA-F]{64}", integrity["blob_id"])
                or type(integrity["size_bytes"]) is not int or not 0 < integrity["size_bytes"] < 2**64):
            raise ValueError("invalid model file integrity")
        yield filename, f"{repo}@{revision}/{filename}", integrity


def load(root):
    data = read_json(root / MANIFEST)
    if type(data["schema_version"]) is not int or data["schema_version"] != 1 or not data["models"]:
        raise ValueError("invalid supported family manifest")
    seen = set()
    for model in data["models"]:
        name = model["family"]
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in seen or name in {".", ".."}:
            raise ValueError("invalid or duplicate family label")
        seen.add(name)
        execution = model["execution"]
        for key, minimum in (("trunk_layers", 1), ("activation_width", 1), ("mtp_layers", 0)):
            if type(execution[key]) is not int or not minimum <= execution[key] < 2**32:
                raise ValueError("invalid family execution shape")
        if execution["speculative_policy"] not in {"disabled", "mtp-if-present"}:
            raise ValueError("invalid family execution policy")
        lanes(model)
        for key in ARTIFACTS:
            if model.get(key) is not None:
                list(artifact_files(model[key]))
    return data["models"]


def family(model, files=None):
    if files is None:
        files = {identity: integrity["blob_id"] for key in ARTIFACTS if model.get(key) is not None
                 for _, identity, integrity in artifact_files(model[key])}
    return {"family": model["family"], "profile": model["profile"],
            "revision": model["artifact"]["revision"], "files": dict(sorted(files.items())),
            "lanes": lanes(model)}


def cached(artifact, cache, files):
    snapshot = (cache / ("models--" + artifact["repo"].replace("/", "--")) /
                "snapshots" / artifact["revision"])
    admitted = []
    for relative, identity, integrity in artifact_files(artifact):
        path = snapshot / relative
        if not path.is_file() or path.stat().st_size != integrity["size_bytes"] or digest(path) != integrity["blob_id"]:
            raise ValueError(f"model cache integrity mismatch: {identity}")
        admitted.append(path)
        files[identity] = integrity["blob_id"]
    return admitted[0]


def write_receipt(root, receipt):
    receipt["artifacts"] = {path: digest(root / path) for path in inventory(root, [root])
                            if path != "receipt.json"}
    write_json(root / "receipt.json", receipt)
