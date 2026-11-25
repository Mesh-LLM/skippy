"""Offline checks for fail-closed Windows VM rehearsal admission."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ci-windows-vm-profile.py"


class WindowsHostProfile(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "base").mkdir()
        (self.root / "base/base.json").write_text("{}")
        (self.root / "driver.exe").write_bytes(b"driver fixture")
        manager = self.root / "manager"
        manager.write_text("#!/bin/sh\nexit 1\n")
        manager.chmod(0o700)
        (self.root / "bin").mkdir()
        findmnt = self.root / "bin/findmnt"
        findmnt.write_text('#!/bin/sh\nprintf "%s\\n" "${FIXTURE_FILESYSTEM:-nfs}"\n')
        findmnt.chmod(0o700)
        self.profile = {"base": str(self.root / "base"), "driver": str(self.root / "driver.exe"),
                        "driver_sha256": hashlib.sha256(b"driver fixture").hexdigest(),
                        "gpu": "0000:01:00.0", "work_root": str(self.root / "work"),
                        "lifecycle_manager": str(manager)}

    def invoke(self, **environment):
        return subprocess.run([sys.executable, str(SCRIPT)], text=True, capture_output=True,
                              env={**os.environ, "WINDOWS_HOST_PROFILE": json.dumps(self.profile),
                                   "WINDOWS_BACKEND": "cuda", "PATH": str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                                   **environment})

    def test_rejects_local_disk_fallback(self):
        result = self.invoke(FIXTURE_FILESYSTEM="ext4")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mounted NFS", result.stderr)
        self.assertFalse((self.root / "work").exists())

    def test_accepts_nfs_behind_automount_stub(self):
        result = self.invoke(FIXTURE_FILESYSTEM="autofs\nnfs")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_complete_pinned_profile(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 6)

    def test_rejects_bad_checksum_before_creating_work(self):
        self.profile["driver_sha256"] = "0" * 64
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse((self.root / "work").exists())

    def test_rejects_missing_lifecycle_manager(self):
        self.profile["lifecycle_manager"] = str(self.root / "absent")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lifecycle manager", result.stderr)

    def test_rejects_multiline_and_pci_injection(self):
        for invalid in ["0000:01:00.0\nextra", "0000:01:00.0 -device other"]:
            self.profile["gpu"] = invalid
            self.assertNotEqual(self.invoke().returncode, 0)


if __name__ == "__main__":
    unittest.main()
