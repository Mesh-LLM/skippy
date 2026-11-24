"""Mesh-style repair feedback within shared coding and verification windows."""

import json
import os
import subprocess
import time
import uuid

from .ci_process import command_logs, time_budget
from .upgrade_recovery import retain
from .upgrade_source import git

CODING_SECONDS = 41400
VERIFICATION_SECONDS = 43200
FAILURES = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)


def verify_or_repair(checkout, diagnostics, verify, repair, save_diagnostics, *, initial_repair=False):
    diagnostics.mkdir(parents=True, exist_ok=True)
    session = {"name": "skippy-upgrade-" + uuid.uuid4().hex, "started": False}
    deadline = None
    turn = 0
    needs_repair = initial_repair
    # Repair rejects history changes, but recovery must still retain their work.
    base = git(checkout, "rev-parse", "HEAD")
    try:
        while True:
            if needs_repair:
                if not os.environ.get("SKIPPY_UPGRADE_REPAIR_COMMAND"):
                    raise ValueError("Automatic repair requires SKIPPY_UPGRADE_REPAIR_COMMAND; diagnostics retained")
                if deadline is None:
                    deadline = time.monotonic() + CODING_SECONDS
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("upgrade repair coding window exhausted")
                turn += 1
                # This also bounds any tools invoked by the repair adapter.
                with time_budget(remaining):
                    repair(checkout, diagnostics, session=session, timeout=remaining)
                if retain(checkout, diagnostics / "recovery", f"turn-{turn}", base=base) is None:
                    raise RuntimeError("could not preserve returned repair work before verification")
            try:
                # Like Mesh, a returned coding turn gets a fresh verification pass.
                with time_budget(VERIFICATION_SECONDS), command_logs(diagnostics / f"verification-{turn}"):
                    verify()
                return
            except FAILURES as error:
                save_diagnostics(checkout, diagnostics)
                (diagnostics / "last-check.json").write_text(json.dumps({
                    "status": "fail", "turn": turn, "error": str(error),
                    "logs": str(diagnostics / f"verification-{turn}"),
                }, indent=2) + "\n")
                print(f"Candidate verification failed; returning evidence to repair: {error}", flush=True)
                if not os.environ.get("SKIPPY_UPGRADE_REPAIR_COMMAND"):
                    raise
                needs_repair = True
    finally:
        retain(checkout, diagnostics / "recovery", "final", base=base)
