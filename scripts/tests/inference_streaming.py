"""Exercise the candidate controller through a buffered pipe, visibly in Actions.

Synthetic API pages need no lab credentials. The child cannot finish until the
parent has received live output from every role; completion-only dumps fail.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from inference_logs import page


def produce(mode, output):
    wait = run_path(str(Path(__file__).resolve().parents[1] / "inference-dispatch.py"))["wait"]
    split = mode == "split"
    runs = ["leader", "worker"] if split else ["standalone"]
    job = {"id": "job_a", "status": "running", "repo_rev": "a" * 40,
           "runs": [{"id": f"run_{runs[0]}"}]}
    worker = {"id": "job_b", "runs": [{"id": "run_worker"}]}
    formation = {"status": "running", "members": [
        {"job_id": "job_a", "role": "leader"}, {"job_id": "job_b", "role": "worker"}]}
    if split:
        job["formation_id"] = "frm_pair"
    responses = []
    for terminal in (False, True):
        status = "succeeded" if terminal else "running"
        responses.append({**job, "status": status})
        if split:
            responses.extend([{**formation, "status": status}, worker])
        responses.extend(page(f"run_{role}", int(terminal),
                              ["fixture: complete" if terminal else "fixture: live"],
                              complete=terminal) for role in runs)
    pilot = Mock()
    pilot.api.side_effect = responses

    def await_receipt(_seconds):
        if sys.stdin.readline() != "received\n":
            raise RuntimeError("parent did not receive live output")

    # Preserve normal Python stdout buffering: NativeLogs must flush explicitly.
    with patch("time.sleep", side_effect=await_receipt):
        wait(pilot, "job_a", f"stream-proof-{mode}", "a" * 40,
             Path(output), time.monotonic() + 30)
    assert pilot.api.call_count == len(responses)


class StreamingTests(unittest.TestCase):
    def test_controller_output_arrives_before_completion(self):
        for mode, roles in (("standalone", ["standalone"]), ("split", ["leader", "worker"])):
            with TemporaryDirectory() as directory:
                environment = {key: value for key, value in os.environ.items()
                               if key != "PYTHONUNBUFFERED"}
                child = subprocess.Popen([sys.executable, __file__, "--produce", mode, directory],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, env=environment)
                lines = queue.Queue()

                def receive():
                    for line in child.stdout:
                        lines.put(line)
                    lines.put(None)

                reader = threading.Thread(target=receive, daemon=True)
                reader.start()
                observed, live = [], set()
                try:
                    while True:
                        line = lines.get(timeout=10)
                        if line is None:
                            break
                        # Real Actions output, including production command guards.
                        print(line, end="", flush=True)
                        observed.append(line)
                        for role in roles:
                            if line == f"[stream-proof-{mode}/{role}/run_{role}] fixture: live\n":
                                live.add(role)
                        if live == set(roles):
                            self.assertIsNone(child.poll(), "output arrived only after exit")
                            child.stdin.write("received\n")
                            child.stdin.flush()
                            live.add("released")
                    self.assertIn("released", live)
                    self.assertEqual(child.wait(timeout=5), 0)
                    for role in roles:
                        prefix = f"[stream-proof-{mode}/{role}/run_{role}] fixture: "
                        self.assertEqual([line for line in observed if line.startswith(prefix)],
                                         [prefix + "live\n", prefix + "complete\n"])
                        saved = Path(directory) / f"stream-proof-{mode}-logs/run_{role}.jsonl"
                        self.assertEqual([json.loads(line)["text"] for line in saved.read_text().splitlines()],
                                         ["fixture: live", "fixture: complete"])
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                    reader.join(timeout=5)
                    child.stdin.close()
                    child.stdout.close()
        print("STREAMING PROOF PASSED: standalone, leader and worker output received before completion; saved logs match.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--produce":
        produce(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
