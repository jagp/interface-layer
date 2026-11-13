"""Tests for bump_pins.main: results go to the run summary; one failed bump
never stops the others."""
import subprocess
import unittest
from unittest import mock

import bump_pins
import catalog

BUMPS = [("a", "jagp/a", "v1.1.0", "1" * 40, "1.1.0"), ("b", "jagp/b", "v2.0.0", "2" * 40, "2.0.0")]


class Main(unittest.TestCase):
    def test_a_failed_bump_is_reported_and_the_next_still_runs(self):
        opened, lines = [], []
        def open_pr(bump, merge=False):
            opened.append(bump[0])
            if bump[0] == "a":
                raise subprocess.CalledProcessError(1, ["gh", "pr", "merge"], stderr="Pull request is not mergeable")
        with mock.patch.object(bump_pins, "find_bumps", lambda *a: (BUMPS, [])), \
             mock.patch.object(bump_pins, "open_pr", open_pr), \
             mock.patch.object(catalog, "report", lines.append), \
             mock.patch("sys.argv", ["bump_pins.py", "--open-prs", "--merge"]):
            code = bump_pins.main()
        self.assertEqual(opened, ["a", "b"])
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith("bump  a:") for line in lines))      # summary lines
        self.assertTrue(any("a:" in line and "not mergeable" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
