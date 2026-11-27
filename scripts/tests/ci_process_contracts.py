"""Process-group cleanup must handle exits racing with permission probes."""

import os
import unittest
from unittest.mock import Mock, patch

from lib.ci_process import group_alive


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
