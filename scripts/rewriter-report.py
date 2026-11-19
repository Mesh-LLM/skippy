#!/usr/bin/env python3
"""Check a rewriter report and the surrounding generation gates."""

import argparse
import json
from pathlib import Path

from lib.rewriter_report import load

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--mode", choices=("validate", "idempotence"), default="validate")
parser.add_argument("--patch-drift-gate", choices=("fail", "warn"))
for name in ("patch-check", "compile-result", "graph-verify-result"):
    parser.add_argument("--" + name, choices=("pass", "skipped"))
args = parser.parse_args()
try:
    print(json.dumps(load(args.report, args.mode == "idempotence"), sort_keys=True))
except (OSError, ValueError, TypeError, KeyError) as error:
    parser.exit(1, f"{error}\n")
