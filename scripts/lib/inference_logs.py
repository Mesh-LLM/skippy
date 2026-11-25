"""Forward native attempt logs into Actions and retain cursor-based evidence."""

import json
import re
import sys
import time
import uuid
from pathlib import Path
from http.client import IncompleteRead


def identifier(value, prefix):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[A-Za-z0-9_]+", value):
        raise ValueError("invalid native log identity")
    return value


class NativeLogs:
    def __init__(self, pilot, row, output):
        self.pilot = pilot
        self.row = row
        self.output = Path(output) / f"{row}-logs"
        self.cursors = {}
        self.complete = {}

    def diagnostic(self, message):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / "diagnostics.txt").open("a", encoding="utf-8") as evidence:
            evidence.write(message + "\n")
        print(f"Native log capture: {message}", file=sys.stderr, flush=True)

    def poll(self, job, formation, deadline):
        jobs = [(job, "leader" if formation else "standalone")]
        if formation:
            for member in formation["members"]:
                member_id = identifier(member["job_id"], "job")
                if member_id != job["id"]:
                    jobs.append((self.pilot.api("GET", f"/v1/jobs/{member_id}"), "worker"))
        caught_up = True
        for member, role in jobs:
            for run in member.get("runs") or []:
                run_id = identifier(run["id"], "run")
                if not self.complete.get(run_id):
                    caught_up = self.drain(run_id, role, deadline) and caught_up
        return caught_up and all(self.complete.values())

    def drain(self, run_id, role, deadline):
        self.output.mkdir(parents=True, exist_ok=True)
        cursor = self.cursors.get(run_id, 0)
        while time.monotonic() < deadline:
            page = self.pilot.api("GET", f"/v1/runs/{run_id}/logs/full?since={cursor}&limit=500")
            if page.get("run_id") != run_id:
                raise ValueError("native log run identity mismatch")
            if page.get("gap_at") is not None:
                raise ValueError("native log sequence gap")
            chunks, next_cursor = page["chunks"], page["cursor"]
            if (type(next_cursor) is not int or next_cursor != cursor + len(chunks)
                    or type(page["caught_up"]) is not bool or type(page["complete"]) is not bool
                    or (not chunks and not page["caught_up"])):
                raise ValueError("native log cursor did not advance correctly")
            for seq, chunk in enumerate(chunks, cursor + 1):
                if chunk.get("seq") != seq or not isinstance(chunk.get("text"), str):
                    raise ValueError("invalid native log chunk")
            with (self.output / f"{run_id}.jsonl").open("a", encoding="utf-8") as evidence:
                for chunk in chunks:
                    evidence.write(json.dumps(chunk) + "\n")
                    self.emit(chunk["text"], role, run_id)
            self.cursors[run_id] = cursor = next_cursor
            self.complete[run_id] = page["complete"] and page["caught_up"]
            if page["caught_up"]:
                return True
        return False

    def emit(self, text, role, run_id):
        # Actions recognizes commands even after a prefix. The resume token is
        # generated on the protected controller and never sent to native jobs.
        token = uuid.uuid4().hex
        print(f"::stop-commands::{token}", flush=True)
        try:
            for line in text.splitlines():
                safe = "".join(c if c.isprintable() or c == "\t" else repr(c)[1:-1]
                               for c in line)
                print(f"[{self.row}/{role}/{run_id}] {safe}", flush=True)
        finally:
            print(f"::{token}::", flush=True)

    def capture(self, job, formation, deadline, final=False):
        # Logging is diagnostic: never replace the native qualification verdict.
        limit = min(deadline, time.monotonic() + 30) if final else deadline
        while time.monotonic() < limit:
            try:
                complete = self.poll(job, formation, limit)
                if not final or complete:
                    return
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, IncompleteRead) as error:
                self.diagnostic(f"{self.row}: {type(error).__name__}; log capture incomplete")
                if not final:
                    return
            time.sleep(min(2, max(0, limit - time.monotonic())))
        self.diagnostic(f"{self.row}: log capture deadline reached; logs may be incomplete")
