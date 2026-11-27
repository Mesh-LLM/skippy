"""Phase barriers for one reserved two-Spark diagnostic formation."""
import json
import os
import socket
import time


def barrier(key, phase):
    peers = json.loads(os.environ['JIANYANG_PEERS'])
    assert len(peers) == 2 and len({p['node'] for p in peers}) == 2
    host = next(p['host'] for p in peers if p['role'] == 'leader')
    expected = f'prefill:{key}:{phase}\n'.encode()
    if os.environ['JIANYANG_ROLE'] == 'leader':
        family = socket.AF_INET6 if ':' in host else socket.AF_INET
        with socket.socket(family) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('::' if family == socket.AF_INET6 else '0.0.0.0', 9490))
            listener.listen(1)
            listener.settimeout(1800)
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(10)
                with peer.makefile('rb') as stream:
                    assert stream.readline(256) == expected, 'split diagnostic phase mismatch'
                peer.sendall(expected)
    else:
        deadline = time.monotonic() + 1800
        while True:
            try:
                peer = socket.create_connection((host, 9490), timeout=10)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('split diagnostic leader barrier did not become ready')
                time.sleep(1)
        with peer:
            peer.settimeout(10)
            peer.sendall(expected)
            with peer.makefile('rb') as stream:
                assert stream.readline(256) == expected, 'split diagnostic phase mismatch'
