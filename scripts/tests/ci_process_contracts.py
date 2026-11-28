"""Exercise bounded build and qualification process cleanup."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from lib.ci_process import command, command_logs, force_stop, group_alive


class CommandCleanupContract(unittest.TestCase):
    def test_descendant_termination_requires_a_successful_exit(self):
        for exit_code in (0, 1):
            with (
                self.subTest(exit_code=exit_code),
                patch("lib.ci_process.start") as start,
                patch("lib.ci_process.group_alive", return_value=True),
                patch("lib.ci_process.force_stop") as terminate,
                patch("lib.ci_process.stop") as stop,
            ):
                child = MagicMock(returncode=exit_code)
                child.wait.return_value = exit_code
                start.return_value.__enter__.return_value = child
                if exit_code == 0:
                    command(["fixture"], terminate_descendants=True)
                    terminate.assert_called_once_with(child)
                    stop.assert_not_called()
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        command(["fixture"], terminate_descendants=True)
                    terminate.assert_not_called()
                    stop.assert_called_once_with(child)

    def test_logged_command_preserves_descendant_termination(self):
        with (
            TemporaryDirectory() as directory,
            command_logs(Path(directory)),
            patch("lib.ci_process.start") as start,
            patch("lib.ci_process.group_alive", return_value=True),
            patch("lib.ci_process.force_stop") as terminate,
        ):
            child = MagicMock(returncode=0)
            child.wait.return_value = 0
            start.return_value.__enter__.return_value = child
            command(["fixture"], terminate_descendants=True)
            terminate.assert_called_once_with(child)
            self.assertEqual(Path(start.call_args.kwargs["stdout"].name).name, "step-001.log")

    @unittest.skipUnless(os.name == "nt", "requires a Windows job object")
    def test_successful_build_drains_a_detached_descendant(self):
        spawn = (
            "import subprocess, sys;"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
            " creationflags=subprocess.DETACHED_PROCESS,"
            " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
        )
        with patch("lib.ci_process.force_stop", wraps=force_stop) as terminate:
            command([sys.executable, "-c", spawn], timeout=30, terminate_descendants=True)
        terminate.assert_called_once()


@unittest.skipIf(os.name == "nt", "Unix process-group behavior")
class ProcessGroupContract(unittest.TestCase):
    def test_exit_during_permission_probe_allows_an_empty_or_zombie_group(self):
        for rows in ("", "1234 Z\n"):
            with self.subTest(rows=rows):
                child = Mock(pid=1234)
                child.poll.side_effect = [None, 0]
                with patch("lib.ci_process.os.killpg", side_effect=PermissionError), \
                        patch("lib.ci_process.subprocess.check_output", return_value=rows):
                    self.assertFalse(group_alive(child))

    def test_exit_during_permission_probe_keeps_live_descendants_visible(self):
        child = Mock(pid=1234)
        child.poll.side_effect = [None, 0]
        with patch("lib.ci_process.os.killpg", side_effect=PermissionError), \
                patch("lib.ci_process.subprocess.check_output", return_value="1234 S\n"):
            self.assertTrue(group_alive(child))

    def test_permission_error_for_a_live_child_is_not_suppressed(self):
        child = Mock(pid=1234)
        child.poll.return_value = None
        with patch("lib.ci_process.os.killpg", side_effect=PermissionError), \
                patch("lib.ci_process.subprocess.check_output") as inspect:
            with self.assertRaises(PermissionError):
                group_alive(child)
        inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
