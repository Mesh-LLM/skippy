#!/usr/bin/env python3
"""Fetch named JY campaign artifacts with their recorded digest; never print credentials."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", action="append", default=[])
    args = parser.parse_args()
    session = json.loads((Path.home() / "Library/Application Support/jianyang/session.json").read_text())
    origin = session["api_url"].rstrip("/")
    headers = {"Authorization": "Bearer " + session["api_key"], "User-Agent": "jy/0.9.46"}

    def read(path):
        with urlopen(Request(origin + path, headers=headers), timeout=60) as response:
            return response.read()

    base = "/v1/artifacts/" + args.run
    inventory = json.loads(read(base))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "artifact-index.json").write_text(json.dumps(inventory, indent=2) + "\n")
    for item in inventory["artifacts"]:
        kind, name = item["key"].rsplit("/", 2)[-2:]
        if name not in args.name:
            continue
        if any(suffix in name.lower() for suffix in (".trace", ".gputrace", ".xml")):
            raise ValueError("raw capture documents and XML exports must remain private on the host")
        data = read(base + "/" + kind + "/" + name)
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("artifact digest mismatch: " + name)
        (args.output / name).write_bytes(data)
        print(name, len(data), item["sha256"])
    missing = set(args.name) - {p.name for p in args.output.iterdir()}
    if missing:
        raise ValueError("requested artifacts unavailable: " + ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
