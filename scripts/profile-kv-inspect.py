#!/usr/bin/env python3
"""Inspect private pilot traces and resolved native settings without uploading traces."""

import argparse
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from lib.profile_kv_host import body, clean_env, command, post, save, server


def inspect_trace(trace, directory, env):
    result = {"trace": str(trace), "tables": {}}
    for schema in ("metal-gpu-intervals", "metal-gpu-state-intervals", "metal-application-intervals", "time-profile", "time-sample"):
        path = directory / (schema + ".xml")
        xpath = '/trace-toc/run[@number="1"]/data/table[@schema="' + schema + '"]'
        args = ["xcrun", "xctrace", "export", "--input", str(trace), "--xpath", xpath]
        with path.open("wb") as stream:
            run = subprocess.run(args, env=env, stdout=stream, stderr=subprocess.PIPE, timeout=180)
        if run.returncode:
            result["tables"][schema] = {"error": run.stderr.decode(errors="replace")[-1500:]}
            continue
        tree = ET.parse(path)
        rows = tree.findall(".//row")
        metadata = [ET.tostring(x, encoding="unicode")[:4000] for x in tree.findall(".//schema")]
        examples = []
        for row in rows[:3]:
            values = []
            for value in row:
                text = value.get("fmt") or value.text or ""
                text = re.sub(r"/Users/[^\s]+", "<private-path>", text)
                values.append({"tag": value.tag, "display": text[:300], "ref": value.get("ref")})
            examples.append(values)
        result["tables"][schema] = {"rows": len(rows), "schema": metadata, "examples": examples,
                                     "private_export": str(path)}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.cache / ("inspection-" + str(time.time_ns()))
    root.mkdir()
    env = clean_env(root)
    model = json.loads((args.cache / "models.json").read_text())[0]
    results = {"trace_exports": [], "resolved_settings": []}
    for trace in args.pilot_root.rglob("metal.trace"):
        destination = root / trace.parent.parent.name
        destination.mkdir()
        results["trace_exports"].append(inspect_trace(trace, destination, env))
        save(args.output / "inspection.json", results)
    for engine in ("llama", "skippy"):
        destination = root / ("settings-" + engine)
        destination.mkdir()
        # Diagnostics only: retain verbose native startup outside every performance cell.
        import lib.profile_kv_host as host
        original = host.launch_args
        def diagnostic(cache, selected_model, selected_engine, lanes=1):
            argv = original(cache, selected_model, selected_engine, lanes)
            argv.insert(1, "--debug" if selected_engine == "skippy" else "--verbose")
            return argv
        host.launch_args = diagnostic
        try:
            with server(args.cache, model, engine, destination, env):
                response = post("/v1/completions", body(model, "The orchard keeper records", 4))
                save(destination / "response.json", response)
        finally:
            host.launch_args = original
        log = (destination / "server.log").read_text(errors="replace")
        patterns = r"offload|n_ctx|flash_attn|type_k|type_v|n_batch|n_ubatch|n_seq|KV.*buffer|recurrent|f16|backend_device"
        lines = [line for line in log.splitlines() if re.search(patterns, line, re.I)]
        results["resolved_settings"].append({"engine": engine, "lines": lines})
        save(args.output / "inspection.json", results)
    print(json.dumps({"trace_row_counts": [{schema: table.get("rows") for schema, table in result["tables"].items()}
                                           for result in results["trace_exports"]],
                      "settings_line_counts": {r["engine"]: len(r["lines"]) for r in results["resolved_settings"]}}), flush=True)


if __name__ == "__main__":
    main()
