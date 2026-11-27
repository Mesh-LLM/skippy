"""Keep split comparison arms in lockstep within one reserved JY formation."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen

from .ci_process import DEADLINE


def await_condition(check, seconds=1800):
    deadline = time.monotonic() + seconds
    if DEADLINE.get() is not None:
        deadline = min(deadline, DEADLINE.get())
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.25)
    raise TimeoutError("paired inference rendezvous timed out")


class Coordinator:
    def __init__(self, worker_host, port=9913):
        self.state = {"index": -1, "done": -1, "worker": None, "error": None, "complete": False}
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.client_address[0] != worker_host or self.path != "/state":
                    self.send_error(403)
                    return
                body = json.dumps(state).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.client_address[0] != worker_host or self.path not in {"/ready", "/done", "/error"}:
                    self.send_error(403)
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    self.send_error(400)
                    return
                value = json.loads(self.rfile.read(size))
                if self.path == "/ready":
                    state["worker"] = value
                elif self.path == "/done":
                    state["done"] = value["index"]
                else:
                    state["error"] = "split comparison worker failed"
                self.send_response(200)
                self.end_headers()

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def wait(self, check, seconds=1800):
        def poll():
            if self.state["error"]:
                raise RuntimeError(self.state["error"])
            return check(self.state)
        return await_condition(poll, seconds)

    def __exit__(self, *_args):
        self.state["complete"] = True
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


def exchange(origin, path, value=None):
    data = None if value is None else json.dumps(value).encode()
    request = Request(origin + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        body = response.read(65537)
        return json.loads(body) if body else True


def worker_state(origin):
    def poll():
        try:
            return exchange(origin, "/state")
        except (URLError, TimeoutError, ConnectionError):
            return None
    return await_condition(poll, seconds=7200)
