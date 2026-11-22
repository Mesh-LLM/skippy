"""Select upgrade fixtures without shrinking the complete graph-contract registry."""

import copy
import re

from .ci_artifacts import read_json
from .upgrade_source import PIN, validate_sha

POLICY = "ci/llama-canary/upgrade-suite.json"
SELECTION = "ci/llama-canary/upgrade-selection.json"


def selected_models(root, models, backend, suite="routine"):
    if backend not in {"cpu", "cuda"} or suite not in {"routine", "full"}:
        raise ValueError("unsupported upgrade backend or suite")
    policy = read_json(root / POLICY)
    groups = [*policy.get("dgx_families", {}), *policy.get("split_families", []), *policy.get("workload_families", [])]
    if (policy.get("schema_version") != 1 or len(groups) != len(set(groups))
            or len(policy.get("dgx_families", {})) != 4):
        raise ValueError("unsupported upgrade suite policy")
    selection = read_json(root / SELECTION)
    if (selection.get("schema_version") != 1
            or selection.get("upstream_sha") != (root / PIN).read_text().strip()
            or not isinstance(selection.get("additional_families"), list)):
        raise ValueError("upgrade selection does not match candidate upstream")
    names = [*policy["dgx_families"], *policy["split_families"], *selection["additional_families"]]
    if suite == "full":
        names = [model["family"] for model in models if model["class"] == "causal_generation"]
    if backend == "cuda" and suite == "routine":
        # The four fixed models already have exact-source CUDA inference on Sparks.
        names = [name for name in names if name not in policy["dgx_families"]]
    if backend == "cpu":
        names += policy["workload_families"]
    by_name = {model["family"]: model for model in models}
    if set(names) - by_name.keys():
        raise ValueError("upgrade suite selects an unknown family")
    if backend == "cuda":
        names = [name for name in names if by_name[name]["class"] == "causal_generation"]
    if any(by_name[name]["class"] == "causal_generation" for name in policy["workload_families"]):
        raise ValueError("upgrade workload roster contains a causal model")
    artifacts = read_json(root / "ci/inference-artifacts.json")
    result = []
    for name in dict.fromkeys(names):
        model = copy.deepcopy(by_name[name])
        dgx = policy["dgx_families"].get(name)
        if dgx:
            fixture = artifacts[dgx["fixture"]]
            validate_sha(fixture["revision"])
            model["artifact"] = {
                "repo": fixture["repo"], "revision": fixture["revision"], "selector": dgx["quantization"],
                "files": [item["path"] for item in fixture["files"]],
                "file_integrity": {item["path"]: {"blob_id": item["sha256"], "size_bytes": item["size"]}
                                   for item in fixture["files"]},
            }
            model.pop("mmproj_artifact", None)
            model.pop("draft_artifact", None)
            model["execution"].update(trunk_layers=fixture["layers"], activation_width=fixture["activation_width"])
            model["resources"]["estimated_model_bytes"] = sum(item["size"] for item in fixture["files"])
        result.append(model)
    return result


def parse_factory(architecture_source, model_source):
    """Read factory cases instead of enum-only architecture names."""
    table = architecture_source.split("LLM_ARCH_NAMES =", 1)[1].split("};", 1)[0]
    names = dict(re.findall(r'\{\s*LLM_ARCH_(\w+)\s*,\s*"([^"]+)"', table))
    text = model_source.split('unsupported model architecture:', 1)[0]
    cases = set(re.findall(r"case LLM_ARCH_(\w+)\s*:", text))
    if not cases or cases - names.keys():
        raise ValueError("cannot reconcile upstream model factory")
    return sorted(names[name] for name in cases)


def factory_architectures(source):
    return parse_factory((source / "src/llama-arch.cpp").read_text(),
                         (source / "src/llama-model.cpp").read_text())
