#!/usr/bin/env python3
"""Select the one upgrade candidate whose exact source passed independent full CI."""

import json
import os
import sys

from lib.upgrade_source import output, validate_sha

ROUNDS = (("candidate", "qualify", "llama-initial-candidate"),
          ("repair", "requalify", "llama-repaired-candidate"),
          ("repair_2", "requalify_2", "llama-repaired-2-candidate"))


def select(results):
    verified = []
    for producer, ci, artifact in ROUNDS:
        if results[ci]["result"] != "success":
            continue
        candidate = results[producer]
        if candidate["result"] != "success":
            raise ValueError("successful full CI has no successful candidate producer")
        verified.append((validate_sha(candidate["outputs"]["candidate_sha"]), artifact))
    if len(verified) != 1:
        raise ValueError("upgrade stopped without exactly one verified candidate after at most three full-CI rounds; "
                         "inspect the latest CI failures and repair diagnostics; retained bundles are unverified")
    return verified[0]


if __name__ == "__main__":
    try:
        sha, artifact = select(json.loads(os.environ["UPGRADE_RESULTS"]))
        output("candidate_sha", sha)
        output("candidate_artifact", artifact)
    except (KeyError, ValueError) as error:
        sys.exit(str(error))
