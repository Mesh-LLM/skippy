"""Own-process listener evidence, including real Linux sockets in hosted CI."""

import os
import selectors
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib.ci_process import start, stop
from lib.inference_socket import owns_listener


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.proc = Path(self.enterContext(TemporaryDirectory()))
        self.child = self.proc / "123"
        (self.child / "fd").mkdir(parents=True)
        (self.child / "net").mkdir()
        (self.child / "fd/3").symlink_to("socket:[456]")

    def row(self, local, state="0A", inode="456"):
        return f"0: {local} 00000000:0000 {state} 0:0 0:0 0 1000 0 {inode}\n"

    def test_owned_ipv4_and_ipv6_listeners(self):
        for host, table, address in (("127.0.0.1", "tcp", "0100007F"),
                                     ("::1", "tcp6", "00000000000000000000000001000000")):
            with self.subTest(host=host):
                (self.child / "net" / table).write_text(self.row(address + ":24B9"))
                self.assertTrue(owns_listener(123, host, 9401, proc=self.proc))

    def test_another_process_socket_or_wrong_endpoint_or_state_cannot_pass(self):
        for row in (self.row("0100007F:24B9", inode="789"), self.row("0100007F:24B8"),
                    self.row("0200007F:24B9"), self.row("0100007F:24B9", state="01")):
            with self.subTest(row=row):
                (self.child / "net/tcp").write_text(row)
                self.assertFalse(owns_listener(123, "127.0.0.1", 9401, proc=self.proc))

    def test_exited_process_and_closed_descriptor_cannot_pass(self):
        (self.child / "net/tcp").write_text(self.row("0100007F:24B9"))
        self.assertFalse(owns_listener(999, "127.0.0.1", 9401, proc=self.proc))
        (self.child / "fd/3").unlink()
        self.assertFalse(owns_listener(123, "127.0.0.1", 9401, proc=self.proc))

    @unittest.skipUnless(sys.platform == "linux", "real /proc ownership executes on Linux CI")
    def test_actual_child_socket_is_admitted_without_any_tracing_output(self):
        from lib.inference_peer import PeerControl
        code = ("import socket,time; s=socket.socket(); s.bind(('127.0.0.1',0)); "
                "s.listen(); print(s.getsockname()[1],flush=True); time.sleep(30)")
        child = start([sys.executable, "-c", code], stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=5), "child did not report its test port")
            port = int(child.stdout.readline())
            self.assertTrue(owns_listener(child.pid, "127.0.0.1", port))
            self.assertFalse(owns_listener(os.getpid(), "127.0.0.1", port))
            peer = PeerControl({"role": "worker"}, "127.0.0.1", "")
            peer.worker_ready(child, {"bind_addr": f"127.0.0.1:{port}"}, port=port, seconds=1)
            self.assertEqual(peer.phase, "ready")
        finally:
            stop(child)
            child.stdout.close()


if __name__ == "__main__":
    unittest.main()
