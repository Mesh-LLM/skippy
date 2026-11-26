"""Pinned inputs and native configuration for the cache-off scheduler suite."""

import hashlib
import json
import re
import runpy
import time
from pathlib import Path

from lib import profile_kv_host as host


SOURCE = "671c6ebbf7661b236ce44d352024c51741b08f9c"
PATCH = "b3cbe9f85ad166ffff4dd86043870ba780e447240d63b6c48373a6d67741a8d6"
THOUGHTWORKS = "d0b0f913f3a3095453820eb0e3e1f58b102e25910ee1bc0da78d62ab0041ee15"
LLAMA_MODEL_SHA = "6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83"
MODELS = ("llama-3.2-1b-q4-k-m", "qwen3.5-4b-q4-k-m", "qwen3-4b-q4-k-m",
          "qwen3.5-35b-a3b-q4-k-m", "qwen3-coder-30b-a3b-q4-k-m",
          "qwen3.6-35b-a3b-q4-k-m", "qwen3.8-flash-next-iq4-xs")
ARMS = ("skippy-base", "skippy-patched", "llama-slots", "llama-unified")
QUALIFICATION_IDS = {"qwen3.6-35b-a3b-q4-k-m": "qwen36-35b-a3b",
                     "qwen3.8-flash-next-iq4-xs": "qwen38-flash-next"}
ORDERS = ((0, 1, 3, 2), (1, 2, 0, 3), (2, 3, 1, 0), (3, 0, 2, 1))
CORRECTNESS_CASES = {
    "qwen3.6-35b-a3b-q4-k-m": {"load-0000": 32, "load-0001": 32,
        "short-0": 128, "long-2": 24, "long-3": 24, "short-9": 48},
    "qwen3.5-4b-q4-k-m": {"load-0000": 32, "load-0100": 32, "load-0190": 32},
    "qwen3.8-flash-next-iq4-xs": {"load-0001": 32, "short-9": 48},
}


def source_identity(files):
    if len(files) == 1:
        return files[0]["sha256"]
    digest = hashlib.sha256(b"mesh-llm-split-gguf-v1\0")
    digest.update(len(files).to_bytes(8, "little"))
    for index, file in enumerate(files):
        digest.update(index.to_bytes(8, "little"))
        digest.update(file["expected_bytes"].to_bytes(8, "little"))
        digest.update(file["sha256"].encode("ascii"))
    return digest.hexdigest()


def inputs(cache, key, root):
    scripts = Path(__file__).resolve().parents[1]
    qualification = runpy.run_path(str(scripts / "profile-kv-scheduler-qualification.py"))
    identity, candidate, roots = qualification["qualified_arms"](cache, PATCH, root)
    if identity["source_sha"] != SOURCE:
        raise ValueError("wrong baseline source")
    if key == MODELS[0]:
        staged = cache / f"thoughtworks-{THOUGHTWORKS}"
        path = Path((staged / "model-path.txt").read_text().strip())
        model = {"key": key, "path": str(path), "files": [{"sha256": LLAMA_MODEL_SHA}]}
    else:
        models = json.loads((cache / "models.json").read_text())
        admitted = cache / "scheduler-qualification-models-v1.json"
        if admitted.exists():
            models += json.loads(admitted.read_text())
        if key in QUALIFICATION_IDS:
            pin = json.loads(Path("ci/inference-artifacts.json").read_text())[QUALIFICATION_IDS[key]]
            snapshot = (Path("/Users/Shared/jianyang/model-store/huggingface/hub") /
                        ("models--" + pin["repo"].replace("/", "--")) / "snapshots" / pin["revision"])
            files = [{"path": str(snapshot / f["path"]), "sha256": f["sha256"],
                      "expected_bytes": f["size"]} for f in pin["files"]]
            models.insert(0, {"key": key, "repo": pin["repo"], "revision": pin["revision"],
                              "files": files, "path": files[0]["path"]})
        model = next(m for m in models if m["key"] == key)
    for index, file in enumerate(model["files"]):
        path = Path(file.get("path", model["path"] if index == 0 else ""))
        if not path.is_file() or qualification["file_digest"](path) != file["sha256"]:
            raise ValueError("model/shard digest mismatch")
        if "expected_bytes" in file and path.stat().st_size != file["expected_bytes"]:
            raise ValueError("model/shard size mismatch")
    replay = runpy.run_path(str(scripts / "profile-kv-scheduler.py"))
    model["source_model_sha256"] = source_identity(model["files"])
    return identity, candidate, roots, model, replay


def settings(root, model, arm, lanes):
    engine = "skippy" if arm.startswith("skippy") else "llama"
    argv = host.launch_args(root, model, engine, lanes)
    if len(model["files"]) > 1:
        # GGUF shard discovery requires the snapshot's original split filenames.
        # Resolving a model-store symlink to its hash-named blob loses that contract.
        flag = "--model-path" if engine == "skippy" else "-m"
        argv[argv.index(flag) + 1] = str(Path(model["path"]).absolute())
    if engine == "skippy":
        argv += ["--kv-cache-disk", "off"]
    else:
        argv += ["--no-cache-prompt"]
        argv += (["--no-kv-unified"] if arm == "llama-slots" else
                 ["--kv-unified", "--kv-unified-per-slot", "8192"])
    return engine, argv


def resolved(path, arm, lanes, batch_size=512, microbatch_size=512):
    log = path.read_text(errors="replace")
    result = {}
    expected = {"n_ctx": str(8192 * lanes), "n_batch": str(batch_size), "n_ubatch": str(microbatch_size),
                "kv_unified": "false" if arm == "llama-slots" else "true"}
    for name in (*expected, "n_seq_max", "n_rs_seq", "n_ctx_seq", "n_ctx_slot", "flash_attn"):
        result[name] = sorted(set(re.findall(rf"\b{name}\s*=\s*['\"]?(\w+)", log)))
    for name, value in expected.items():
        if value not in result[name]:
            raise ValueError(f"native setting mismatch {arm}: {name}={result[name]}, wanted {value}")
    layers = re.search(r"offloaded (\d+)/(\d+) layers to GPU", log)
    if not layers or layers[1] != layers[2]:
        raise ValueError("full GPU offload unverified")
    result["gpu_layers"] = int(layers[1])
    result["kv_buffers"] = [line.strip() for line in log.splitlines()
                            if "llama_kv_cache:" in line and ("f16" in line or "cells" in line)]
    if not any("f16" in line for line in result["kv_buffers"]):
        raise ValueError("F16 KV buffers unverified")
    return result


def nominal(env, pid=None, soak=30, timeout=600):
    start = time.monotonic()
    since = start
    while time.monotonic() - start < timeout:
        if host.thermal_pressure() or host.competing_work(env, pid):
            since = time.monotonic()
        if time.monotonic() - since >= soak:
            return
        time.sleep(1)
    raise host.HostUnavailable("continuous nominal cooldown not achieved")


def make_load_prompts(model, replay):
    rows = []
    for index in range(256):
        text = f"Notebook {index:04d}. Describe concrete observations for this numbered notebook.\n"
        text += "The keeper checks the equipment and writes careful observations each morning.\n" * 100
        tokens = replay["tokenize"](model, "llama", text)[:512]
        text = host.post("/detokenize", {"tokens": tokens})["content"]
        if len(tokens) != 512 or replay["tokenize"](model, "llama", text) != tokens:
            raise ValueError("load prompt failed token round-trip")
        rows.append({"id": f"load-{index:04d}", "bucket": "p512-o32",
                     "messages": [{"role": "user", "content": text}], "max_tokens": 32,
                     "native_prompt_tokens": 512,
                     "token_ids_sha256": replay["digest"](json.dumps(tokens).encode())})
    if len({p["token_ids_sha256"] for p in rows}) != len(rows):
        raise ValueError("duplicate load prompts")
    return rows


def verify_prompts(model, engine, replay, prompts):
    for prompt in prompts:
        tokens = replay["tokenize"](model, engine, prompt["messages"][0]["content"])
        if replay["digest"](json.dumps(tokens).encode()) != prompt["token_ids_sha256"]:
            raise ValueError(f"tokenizer mismatch: {prompt['id']}")


def effective_tokens(model, engine, text):
    """Count the model's automatic special tokens as the completion path does."""
    if engine == "llama":
        return host.post("/tokenize", {"content": text, "add_special": True})["tokens"]
    sha = model.get("source_model_sha256", source_identity(model["files"]))
    identity = {"model_id": model["key"], "source_model_sha256": sha,
                "tokenizer_id": "gguf-source-sha256:" + sha, "stage_index": 0}
    return host.post("/v1/tokenize", {"text": text, "add_special": True,
                                    "expected_identity": identity})["token_ids"]


def verify_effective_tokens(model, engine, replay, fixture, establish):
    observed = {}
    groups = [(name, row, row["text"], "tokens") for name, row in fixture["mixed"].items()]
    groups += [(row["id"], row, row["messages"][0]["content"], "native_prompt_tokens")
               for row in fixture["load"]]
    for name, row, text, field in groups:
        ids = effective_tokens(model, engine, text)
        digest = replay["digest"](json.dumps(ids).encode())
        if establish and "effective_token_ids_sha256" not in row:
            row["content_token_count"] = row[field]
            row["effective_token_ids_sha256"] = digest
            row[field] = len(ids)
        if row.get("effective_token_ids_sha256") != digest or row[field] != len(ids):
            raise ValueError(f"effective special-token mismatch: {engine} {name}")
        observed[name] = {"tokens": len(ids), "token_ids_sha256": digest}
    return observed
