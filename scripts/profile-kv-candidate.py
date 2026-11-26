#!/usr/bin/env python3
"""Single existing-flag experiment: default versus ordinary shared-pool attention."""

import argparse
import json
import shutil
import time
from pathlib import Path

import lib.profile_kv_host as host


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-record", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    resumed = json.loads(args.resume_record.read_text()) if args.resume_record else None
    root = Path(resumed["private_root"]) if resumed else args.cache / ("followup-candidate-" + str(time.time_ns()))
    if resumed:
        if root.parent.resolve() != args.cache.resolve() or not root.name.startswith("followup-candidate-"):
            raise ValueError("resume root is outside this campaign cache")
        if len(resumed["quality"]) != 4 or any(not r["four_texts_equal"] or not r["logprobs_exact"] for r in resumed["quality"]):
            raise ValueError("cannot resume a failed/incomplete quality gate")
    else:
        root.mkdir()
    env = host.clean_env(root)
    models = json.loads((args.cache / "models.json").read_text())
    original = host.launch_args
    result = {"private_root": str(root), "change": "--kv-row-attention=false only", "quality": [], "timing": [],
              "scope": "Default Skippy versus candidate at identical source/model/precision; upstream equivalence remains a separate failed screen."}
    if resumed:
        result = resumed
    for model in ([] if resumed else models):
        model["skippy_version"] = json.loads((args.cache / "build-identity.json").read_text())["source_sha"]
        source = args.reference_root / model["key"] / "prompts.jsonl"
        prompts = [json.loads(line) for line in source.read_text().splitlines()]
        saved = {}
        # Fresh per-arm correctness lifetimes across every architecture.
        for arm in ("base", "ordinary"):
            directory = root / (model["key"] + "-quality-" + arm)
            directory.mkdir()
            def launch(cache, selected, engine, lanes=1):
                argv = original(cache, selected, engine, lanes)
                return argv + (["--kv-row-attention=false"] if arm == "ordinary" else [])
            host.launch_args = launch
            print("QUALITY", model["key"], arm, flush=True)
            time.sleep(30)
            host.require_idle(env)
            responses = []
            try:
                with host.server(args.cache, model, "skippy", directory, env) as child:
                    for prompt in prompts:
                        host.require_idle(env, child.pid)
                        response = host.post("/v1/completions", host.body(model, prompt["messages"][0]["content"], prompt["max_tokens"]))
                        if response["usage"]["completion_tokens"] != prompt["max_tokens"]:
                            raise ValueError("incorrect generation length in candidate quality screen")
                        responses.append(response)
                    request = host.body(model, prompts[1]["messages"][0]["content"], 256)
                    request["logprobs"] = 5
                    logprobs = host.post("/v1/completions", request)
                    if logprobs["usage"]["completion_tokens"] != 256:
                        raise ValueError("incorrect generation length in candidate logprob screen")
                    for response in responses + [logprobs]:
                        if response["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0):
                            raise ValueError("unexpected prompt reuse in candidate quality screen")
                    host.save(args.output / (model["key"] + "-" + arm + "-logprobs.json"), logprobs)
                    if host.thermal_pressure():
                        raise host.HostUnavailable("non-nominal pressure during candidate quality screen")
            finally:
                host.launch_args = original
            saved[arm] = {"responses": responses, "logprobs": logprobs}
            host.save(args.output / (model["key"] + "-" + arm + "-outputs.json"), responses)
            shutil.copy2(directory / "launch.json", args.output / (model["key"] + "-" + arm + "-launch.json"))
        text_equal = all(a["choices"][0]["text"] == b["choices"][0]["text"]
                         for a, b in zip(saved["base"]["responses"], saved["ordinary"]["responses"]))
        logprobs_equal = saved["base"]["logprobs"]["choices"][0]["logprobs"] == saved["ordinary"]["logprobs"]["choices"][0]["logprobs"]
        result["quality"].append({"model": model["key"], "four_texts_equal": text_equal,
                                  "logprobs_exact": logprobs_equal})
        host.save(args.output / "candidate.json", result)
        if not text_equal or not logprobs_equal:
            result["verdict"] = "REJECTED: candidate failed registered exact fixed-screen gate; timing NOT RUN"
            host.save(args.output / "candidate.json", result)
            print(result["verdict"], model["key"], flush=True)
            return
    model = next(m for m in models if m["key"] == "qwen3.5-35b-a3b-q4-k-m")
    model["skippy_version"] = json.loads((args.cache / "build-identity.json").read_text())["source_sha"]
    directory = root / model["key"]
    directory.mkdir(exist_ok=True)
    prompt_path = directory / "prompts.jsonl"
    shutil.copy2(args.reference_root / model["key"] / "prompts.jsonl", prompt_path)
    previous = {}
    first_pair = 0
    if resumed:
        while {r["arm"] for r in result["timing"] if r["pair"] == first_pair} == {"base", "ordinary"}:
            first_pair += 1
        result["discarded_incomplete_pair_entries"] = [r for r in result["timing"] if r["pair"] >= first_pair]
        result["timing"] = [r for r in result["timing"] if r["pair"] < first_pair]
        import importlib.util
        module_spec = importlib.util.spec_from_file_location("candidate_audit", Path(__file__).with_name("profile-kv-candidate-audit.py"))
        auditor = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(auditor)
        result["resume_admission"] = auditor.audit_timing(root, result, model["skippy_version"],
                                                         expected_pairs=first_pair, checks_only=True)
        for row in result["timing"]:
            previous[row["arm"]] = row["evidence_id"]
        for cell in directory.glob(f"b{first_pair}-p*-*"):
            cell.rename(cell.with_name("interrupted-" + str(time.time_ns()) + "-" + cell.name))
        result["resume_scope"] = "Completed original pairs retained; whole interrupted pair restarted. No thermal/contention failures admitted; independent audit required."
    try:
        for pair in range(first_pair, 6):
            order = ("base", "ordinary") if pair % 2 == 0 else ("ordinary", "base")
            for position, arm in enumerate(order):
                cell = directory / f"b{pair}-p{position}-{arm}"
                cell.mkdir()
                arm_root = directory / arm
                arm_root.mkdir(exist_ok=True)
                def launch(cache, selected, engine, lanes=1):
                    argv = original(cache, selected, engine, lanes)
                    return argv + (["--kv-row-attention=false"] if arm == "ordinary" else [])
                host.launch_args = launch
                print("PAIR_CELL", pair, position, arm, flush=True)
                time.sleep(30)
                host.require_idle(env)
                with host.server(args.cache, model, "skippy", cell, env) as child:
                    record = host.bench(model, "skippy", prompt_path, arm_root, cell, env,
                                        previous.get(arm), child.pid, round_index=pair, label=arm)
                    previous[arm] = record["evidence_id"]
                    result["timing"].append({"pair": pair, "position": position, "arm": arm,
                                             "evidence_id": record["evidence_id"]})
                host.save(args.output / "candidate.json", result)
        result["verdict"] = "COMPLETED: requires independent current-round count/host/paired-statistics audit"
    finally:
        host.launch_args = original
        import tarfile
        with tarfile.open(args.output / "candidate-evidence.tar.gz", "w:gz") as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    if any(part.endswith((".trace", ".gputrace")) for part in path.parts) or path.suffix == ".xml":
                        raise ValueError("raw capture artifact excluded from candidate export")
                    archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
        host.save(args.output / "candidate.json", result)


if __name__ == "__main__":
    main()
