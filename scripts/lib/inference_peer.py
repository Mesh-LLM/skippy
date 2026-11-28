"""Bind split readiness and completion to the actual JY formation attempts."""

import ipaddress
import json
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from .ci_artifacts import write_json


def port_available(host, port):
    family = socket.AF_INET6 if ipaddress.ip_address(host).version == 6 else socket.AF_INET
    with socket.socket(family) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))


class PeerControl:
    def __init__(self, identity, host, peer_url, *, port=9912):
        self.identity = identity
        self.expected = {k: v for k, v in identity.items() if k != "run_id"}
        self.expected["role"] = "worker" if identity["role"] == "leader" else "leader"
        self.host, self.peer_url, self.port = host, peer_url, port
        self.peer = None
        self.phase = "starting"
        self.ack = threading.Event()
        self.completed = False
        self.opener = build_opener(ProxyHandler({}))

    @classmethod
    def from_environment(cls, sha, model, profile):
        formation, run, role = (os.environ[name] for name in
                                ("JIANYANG_FORMATION_ID", "JIANYANG_RUN_ID", "JIANYANG_ROLE"))
        if (not re.fullmatch(r"frm_[A-Za-z0-9_]+", formation)
                or not re.fullmatch(r"run_[A-Za-z0-9_]+", run) or role not in {"leader", "worker"}):
            raise ValueError("split qualification requires a JY formation and attempt identity")
        peers = json.loads(os.environ["JIANYANG_PEERS"])
        if len(peers) != 2 or {p["role"] for p in peers} != {"leader", "worker"}:
            raise ValueError("split qualification requires exactly two peer roles")
        hosts = {p["role"]: ipaddress.ip_address(p["host"]) for p in peers}
        remote = hosts["worker" if role == "leader" else "leader"]
        address = f"[{remote}]" if remote.version == 6 else str(remote)
        return cls({"formation_id": formation, "run_id": run, "source_sha": sha,
                    "model": model, "profile": profile, "role": role},
                   str(hosts[role]), f"http://{address}:9912")

    def __enter__(self):
        control = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_args):
                pass

            def respond(self, status):
                body = json.dumps({"identity": control.identity, "phase": control.phase}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

            def do_GET(self):
                self.respond(200 if self.path == "/" else 404)

            def do_POST(self):
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        raise ValueError("invalid acknowledgement size")
                    identity = json.loads(self.rfile.read(size))
                    valid = (self.path == "/ack" and control.identity["role"] == "leader"
                             and control.phase == "complete" and control.peer is not None
                             and identity == control.peer)
                    self.respond(200 if valid else 409)
                    if valid:
                        # Finish the response before allowing the leader to close.
                        control.ack.set()
                except (OSError, ValueError):
                    self.respond(400)

        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ipaddress.ip_address(control.host).version == 6 else socket.AF_INET
            daemon_threads = True

        self.server = Server((self.host, self.port), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def matches(self, identity):
        return (isinstance(identity, dict)
                and {k: v for k, v in identity.items() if k != "run_id"} == self.expected
                and isinstance(identity.get("run_id"), str)
                and re.fullmatch(r"run_[A-Za-z0-9_]+", identity["run_id"]) is not None
                and (self.peer is None or identity == self.peer))

    def request(self, path="/", body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.peer_url + path, data=data,
                          headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=2) as response:
            payload = response.read(4097)
        if len(payload) > 4096:
            raise ValueError("split peer response exceeded capture limit")
        value = json.loads(payload)
        if not isinstance(value, dict) or not self.matches(value.get("identity")):
            raise ValueError("split peer formation/source/attempt identity mismatch")
        return value

    def await_peer(self, phase=None, process=None, *, seconds=900):
        deadline = time.monotonic() + seconds
        last = "peer absent"
        reported = None
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise RuntimeError("split inference server exited; inspect server.log")
            try:
                value = self.request()
                if phase is None or value.get("phase") == phase:
                    self.peer = value["identity"]
                    return
                last = "peer has not reached " + phase
            except (OSError, ValueError, URLError) as error:
                last = str(error)
            if last != reported:
                print("Waiting for split peer: " + last, flush=True)
                reported = last
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        raise RuntimeError("split peer rendezvous timed out: " + last)

    def worker_ready(self, process, log, stage, *, port=9401, seconds=900):
        address = f"[{self.host}]:{port}" if ":" in self.host else f"{self.host}:{port}"
        if stage["bind_addr"] != address:
            raise ValueError("worker stage listener differs from its reserved endpoint")
        # binary_messaging emits this from the serving child only after its
        # TcpListener::bind succeeds. The per-attempt log is newly truncated
        # and receives only that child's stdout/stderr, not a peer's output.
        marker = (f"skippy-serving listening: binary={address} "
                  f"stage_id={stage['stage_id']} layer_range=").encode()
        deadline = time.monotonic() + seconds
        bound = False
        with log.open("rb") as child_output:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("split worker exited before readiness; inspect server.log")
                while not bound:
                    position = child_output.tell()
                    line = child_output.readline()
                    if not line.endswith(b"\n"):
                        # A tracing write may be observed mid-line. Re-read
                        # that fragment when the child appends its remainder.
                        child_output.seek(position)
                        break
                    bound = marker in line
                if bound:
                    try:
                        with socket.create_connection((self.host, port), timeout=1):
                            self.phase = "ready"
                            return
                    except OSError:
                        pass
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        raise RuntimeError("split worker did not report its own bound listener; inspect server.log")

    def complete(self, process, *, seconds=60):
        if self.peer is None:
            raise ValueError("split completion requires a verified peer attempt")
        self.phase = "complete"
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("split leader exited before completion acknowledgement")
            if self.ack.wait(timeout=min(1, deadline - time.monotonic())):
                self.completed = True
                return
        raise RuntimeError("split worker completion acknowledgement timed out")

    def acknowledge(self):
        if self.request("/ack", self.identity).get("phase") != "complete":
            raise ValueError("split leader has not completed qualification")
        self.completed = True

    def proof(self):
        if not self.completed or self.peer is None:
            raise ValueError("split peer proof requires acknowledged completion")
        return {"local": self.identity, "peer": self.peer, "completion_acknowledged": True}

    def save(self, output):
        write_json(output / "split-peer.json", self.proof())
