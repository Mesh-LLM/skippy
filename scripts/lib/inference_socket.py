"""Verify the native Linux serving child's own listening socket."""

import ipaddress
import os
import re
import sys
from pathlib import Path


def owns_listener(pid, host, port, *, proc=Path("/proc")):
    address = ipaddress.ip_address(host)
    # Linux prints addresses as native-endian 32-bit words in both TCP tables.
    packed = address.packed
    local = "".join(f"{int.from_bytes(packed[i:i + 4], sys.byteorder):08X}"
                    for i in range(0, len(packed), 4)) + f":{port:04X}"
    process = proc / str(pid)
    try:
        sockets = set()
        for descriptor in (process / "fd").iterdir():
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue  # The serving child can close an unrelated descriptor.
            match = re.fullmatch(r"socket:\[([0-9]+)\]", target)
            if match:
                sockets.add(match[1])
        table = process / "net" / ("tcp6" if address.version == 6 else "tcp")
        with table.open() as rows:
            for row in rows:
                fields = row.split()
                if (len(fields) >= 10 and fields[1] == local and fields[3] == "0A"
                        and fields[9] in sockets):
                    return True
    except FileNotFoundError:
        return False  # The process may have exited; the caller also polls it.
    return False
